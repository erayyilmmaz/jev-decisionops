"""Shared application services used by the CLI and FastAPI delivery surfaces."""

from __future__ import annotations

from collections.abc import Mapping
from time import monotonic
from uuid import UUID

from decisionops.clock import IdentifierGenerator, Uuid4Generator
from decisionops.contracts import validate_contract_data
from decisionops.contracts.schema import ValidatedContract
from decisionops.evaluation import RegressionEngine, ReplayEngine, validate_dataset_data
from decisionops.models import (
    DecisionExecutionArtifact,
    JsonValue,
    ProviderFailure,
    ProviderFailureKind,
    ProviderResult,
    RegressionComparison,
    RegressionThresholds,
    ReplayRunArtifact,
)
from decisionops.policy import PolicyEngine, PolicyEvaluationError
from decisionops.providers import DecisionProvider, ProviderRequest
from decisionops.providers.jev import jev_provider_from_settings
from decisionops.telemetry import Telemetry


class ProviderExecutionDisabledError(RuntimeError):
    """Raised before any provider call when live execution has not been enabled."""


class DecisionPolicyExecutionError(RuntimeError):
    """Safe error for an unexpected deterministic policy evaluation failure."""


class EvaluationNotFoundError(LookupError):
    """Raised when a caller requests an unknown in-memory evaluation artifact."""


class DatasetSizeLimitError(ValueError):
    """Raised before evaluation work when the declared dataset exceeds a configured bound."""


class OutboundProviderNotAllowedError(RuntimeError):
    """Raised before a provider call when its configured destination is not allow-listed."""


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
        telemetry: Telemetry | None = None,
        max_dataset_cases: int = 1_000,
        provider_name: str | None = None,
        allowed_provider_names: frozenset[str] | None = None,
    ) -> None:
        if max_dataset_cases < 1:
            raise ValueError("max_dataset_cases must be positive")
        self._provider = provider
        self._provider_execution_enabled = provider_execution_enabled
        self._policy_engine = policy_engine or PolicyEngine()
        self._telemetry = telemetry or Telemetry()
        self._replay_engine = replay_engine or ReplayEngine(
            case_span=self._telemetry.evaluation_case_span,
            aggregate_span=self._telemetry.evaluation_aggregate_span,
            policy_span=self._telemetry.policy_span,
            policy_outcome_observer=self._telemetry.record_policy_outcome,
        )
        self._identifiers = identifiers or Uuid4Generator()
        self._max_dataset_cases = max_dataset_cases
        self._provider_name = provider_name
        self._allowed_provider_names = allowed_provider_names
        self._evaluations: dict[UUID, ReplayRunArtifact] = {}

    @classmethod
    def from_settings(cls, settings: object) -> DecisionOpsApplicationService:
        """Construct the default Jev-backed service without initiating a provider call."""

        from decisionops.config import Settings

        if not isinstance(settings, Settings):
            raise TypeError("settings must be a Settings instance")
        telemetry = Telemetry(service_name=settings.telemetry_service_name)
        return cls(
            provider=jev_provider_from_settings(settings),
            provider_execution_enabled=settings.live_provider_calls_enabled,
            replay_engine=ReplayEngine(
                max_concurrency=settings.evaluation_max_concurrency,
                case_span=telemetry.evaluation_case_span,
                aggregate_span=telemetry.evaluation_aggregate_span,
                policy_span=telemetry.policy_span,
                policy_outcome_observer=telemetry.record_policy_outcome,
            ),
            telemetry=telemetry,
            max_dataset_cases=settings.max_dataset_cases,
            provider_name="typesafe_jev",
            allowed_provider_names=frozenset(
                item.strip()
                for item in settings.outbound_provider_allowlist.split(",")
                if item.strip()
            ),
        )

    async def execute_decision(
        self,
        *,
        contract_data: object,
        state: Mapping[str, JsonValue],
    ) -> DecisionExecutionArtifact:
        """Validate contract first, then execute one provider and policy evaluation."""

        with self._telemetry.decision_span():
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
                with self._telemetry.policy_span():
                    policy = self._policy_engine.evaluate(contract.contract, terminal)
            except PolicyEvaluationError as error:
                raise DecisionPolicyExecutionError(
                    "deterministic policy evaluation failed"
                ) from error
            self._telemetry.record_policy_outcome(policy.outcome)
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
        self._require_dataset_size(dataset_data)
        dataset = validate_dataset_data(dataset_data, contract)
        self._require_provider_execution()
        artifact = await self._replay_engine.run(
            provider=_InstrumentedProvider(self),
            contract=contract,
            dataset=dataset,
        )
        self._evaluations[artifact.evaluation_id] = artifact
        return artifact

    def compare_evaluations(
        self,
        baseline: ReplayRunArtifact,
        current: ReplayRunArtifact,
        *,
        thresholds: RegressionThresholds | None = None,
    ) -> RegressionComparison:
        """Compare artifacts through the common service and record only the outcome class."""

        comparison = RegressionEngine().compare(baseline, current, thresholds=thresholds)
        self._telemetry.record_regression_outcome(comparison.outcome)
        return comparison

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

    def prometheus_metrics(self) -> tuple[bytes, str]:
        """Expose only the dedicated low-cardinality operational metrics registry."""

        return self._telemetry.prometheus_payload()

    def _require_provider_execution(self) -> None:
        if not self._provider_execution_enabled:
            raise ProviderExecutionDisabledError("live provider execution is disabled")
        if (
            self._allowed_provider_names is not None
            and self._provider_name not in self._allowed_provider_names
        ):
            raise OutboundProviderNotAllowedError(
                "configured provider is not in the outbound allowlist"
            )

    def _require_dataset_size(self, dataset_data: object) -> None:
        if not isinstance(dataset_data, Mapping):
            return
        cases = dataset_data.get("cases")
        if isinstance(cases, list) and len(cases) > self._max_dataset_cases:
            raise DatasetSizeLimitError("evaluation dataset exceeds the configured case limit")

    async def _evaluate_provider(
        self, request: ProviderRequest
    ) -> ProviderResult | ProviderFailure:
        started = monotonic()
        with self._telemetry.provider_span():
            try:
                terminal = await self._provider.evaluate(request)
            except Exception:
                terminal = ProviderFailure(
                    provider="application_runtime",
                    kind=ProviderFailureKind.UNKNOWN,
                    message="provider execution raised an unexpected error",
                )
        self._telemetry.record_provider_terminal(terminal, monotonic() - started)
        return terminal


class _InstrumentedProvider:
    """Replay adapter that preserves its provider protocol while adding safe terminal metrics."""

    def __init__(self, service: DecisionOpsApplicationService) -> None:
        self._service = service

    async def evaluate(self, request: ProviderRequest) -> ProviderResult | ProviderFailure:
        return await self._service._evaluate_provider(request)


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
