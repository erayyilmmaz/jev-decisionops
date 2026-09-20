"""Shared application services used by the CLI and FastAPI delivery surfaces."""

from __future__ import annotations

from collections.abc import Mapping
from uuid import UUID

from decisionops.clock import IdentifierGenerator, Uuid4Generator
from decisionops.contracts import validate_contract_data
from decisionops.contracts.schema import ValidatedContract
from decisionops.evaluation import ReplayEngine, validate_dataset_data
from decisionops.models import (
    DecisionExecutionArtifact,
    JsonValue,
    ProviderFailure,
    ProviderFailureKind,
    ProviderResult,
    ReplayRunArtifact,
)
from decisionops.policy import PolicyEngine, PolicyEvaluationError
from decisionops.providers import DecisionProvider, ProviderRequest
from decisionops.providers.jev import jev_provider_from_settings


class ProviderExecutionDisabledError(RuntimeError):
    """Raised before any provider call when live execution has not been enabled."""


class DecisionPolicyExecutionError(RuntimeError):
    """Safe error for an unexpected deterministic policy evaluation failure."""


class EvaluationNotFoundError(LookupError):
    """Raised when a caller requests an unknown in-memory evaluation artifact."""


class DecisionOpsApplicationService:
    """One provider/policy/replay orchestration layer with explicit live-call gating."""

    def __init__(
        self,
        *,
        provider: DecisionProvider,
        provider_execution_enabled: bool,
        policy_engine: PolicyEngine | None = None,
        replay_engine: ReplayEngine | None = None,
        identifiers: IdentifierGenerator | None = None,
    ) -> None:
        self._provider = provider
        self._provider_execution_enabled = provider_execution_enabled
        self._policy_engine = policy_engine or PolicyEngine()
        self._replay_engine = replay_engine or ReplayEngine()
        self._identifiers = identifiers or Uuid4Generator()
        self._evaluations: dict[UUID, ReplayRunArtifact] = {}

    @classmethod
    def from_settings(cls, settings: object) -> DecisionOpsApplicationService:
        """Construct the default Jev-backed service without initiating a provider call."""

        from decisionops.config import Settings

        if not isinstance(settings, Settings):
            raise TypeError("settings must be a Settings instance")
        return cls(
            provider=jev_provider_from_settings(settings),
            provider_execution_enabled=settings.live_provider_calls_enabled,
        )

    async def execute_decision(
        self,
        *,
        contract_data: object,
        state: Mapping[str, JsonValue],
    ) -> DecisionExecutionArtifact:
        """Validate contract first, then execute one provider and policy evaluation."""

        contract = validate_contract_data(contract_data)
        self._require_provider_execution()
        request = _provider_request(contract, state)
        terminal = await self._evaluate_provider(request)
        run_id = self._identifiers.new()
        if isinstance(terminal, ProviderFailure):
            return DecisionExecutionArtifact(
                run_id=run_id,
                contract_fingerprint=contract.fingerprint,
                failure=terminal,
            )
        try:
            policy = self._policy_engine.evaluate(contract.contract, terminal)
        except PolicyEvaluationError as error:
            raise DecisionPolicyExecutionError("deterministic policy evaluation failed") from error
        return DecisionExecutionArtifact(
            run_id=run_id,
            contract_fingerprint=contract.fingerprint,
            result=terminal,
            policy=policy,
        )

    async def execute_evaluation(
        self,
        *,
        contract_data: object,
        dataset_data: object,
    ) -> ReplayRunArtifact:
        """Validate contract and dataset before running the shared replay engine."""

        contract = validate_contract_data(contract_data)
        dataset = validate_dataset_data(dataset_data, contract)
        self._require_provider_execution()
        artifact = await self._replay_engine.run(
            provider=self._provider,
            contract=contract,
            dataset=dataset,
        )
        self._evaluations[artifact.evaluation_id] = artifact
        return artifact

    def get_evaluation(self, evaluation_id: UUID) -> ReplayRunArtifact:
        """Return one locally retained artifact or a safe missing-resource error."""

        try:
            return self._evaluations[evaluation_id]
        except KeyError as error:
            raise EvaluationNotFoundError("evaluation artifact was not found") from error

    def readiness(self) -> dict[str, str]:
        """Report dependencies needed by this in-process V0 delivery surface."""

        return {
            "configuration": "ready",
            "evaluation_artifact_store": "ready",
        }

    def _require_provider_execution(self) -> None:
        if not self._provider_execution_enabled:
            raise ProviderExecutionDisabledError("live provider execution is disabled")

    async def _evaluate_provider(
        self, request: ProviderRequest
    ) -> ProviderResult | ProviderFailure:
        try:
            return await self._provider.evaluate(request)
        except Exception:
            return ProviderFailure(
                provider="application_runtime",
                kind=ProviderFailureKind.UNKNOWN,
                message="provider execution raised an unexpected error",
            )


def _provider_request(
    contract: ValidatedContract,
    state: Mapping[str, JsonValue],
) -> ProviderRequest:
    return ProviderRequest(
        contract=contract.contract,
        canonical_json=contract.canonical_json,
        fingerprint=contract.fingerprint,
        state=dict(state),
    )
