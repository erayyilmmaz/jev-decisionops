from __future__ import annotations

import asyncio
from collections.abc import Mapping
from pathlib import Path
from uuid import UUID

import pytest

from decisionops.contracts import load_contract
from decisionops.contracts.schema import ValidatedContract
from decisionops.evaluation import (
    RegressionEngine,
    ReplayEngine,
    load_dataset,
    regression_comparison_json,
    replay_artifact_json,
    select_baseline,
)
from decisionops.models import (
    CaseChangeKind,
    CaseQualityState,
    ChoiceAnswer,
    EvaluationRunStatus,
    JsonValue,
    NoulAnswer,
    ProviderFailure,
    ProviderFailureKind,
    ProviderMetadata,
    ProviderResult,
    RegressionOutcome,
    RegressionThresholds,
    ReplayRunArtifact,
    ScoreAnswer,
    ValidatedDataset,
)
from decisionops.providers import ProviderRequest

REPOSITORY_ROOT = Path(__file__).parents[2]
CONTRACT_PATH = REPOSITORY_ROOT / "contracts" / "support-ticket-triage.yaml"
DATASET_PATH = REPOSITORY_ROOT / "tests" / "fixtures" / "datasets" / "support-ticket-triage-v1.yaml"


class FixedIdentifiers:
    def __init__(self, evaluation_id: UUID) -> None:
        self._evaluation_id = evaluation_id

    def new(self) -> UUID:
        return self._evaluation_id


class FixtureReplayProvider:
    def __init__(
        self,
        *,
        provider_name: str,
        model_name: str,
        fail_case_id: str | None = None,
        incorrect_case_id: str | None = None,
    ) -> None:
        self._provider_name = provider_name
        self._model_name = model_name
        self._fail_case_id = fail_case_id
        self._incorrect_case_id = incorrect_case_id
        self.active_requests = 0
        self.max_active_requests = 0

    async def evaluate(self, request: ProviderRequest) -> ProviderResult | ProviderFailure:
        self.active_requests += 1
        self.max_active_requests = max(self.max_active_requests, self.active_requests)
        try:
            await asyncio.sleep(0.001)
            case_id = _case_id_for_state(request.state)
            if case_id == self._fail_case_id:
                return ProviderFailure(
                    provider=self._provider_name,
                    kind=ProviderFailureKind.TIMEOUT,
                    message="synthetic replay timeout",
                )
            return _provider_result(
                case_id=case_id,
                provider_name=self._provider_name,
                model_name=self._model_name,
                incorrect=case_id == self._incorrect_case_id,
            )
        finally:
            self.active_requests -= 1


def contract_and_dataset() -> tuple[ValidatedContract, ValidatedDataset]:
    contract = load_contract(CONTRACT_PATH)
    return contract, load_dataset(DATASET_PATH, contract)


def run_replay(
    provider: FixtureReplayProvider,
    *,
    evaluation_id: UUID,
    max_concurrency: int = 4,
) -> ReplayRunArtifact:
    contract, dataset = contract_and_dataset()
    return asyncio.run(
        ReplayEngine(
            max_concurrency=max_concurrency,
            identifiers=FixedIdentifiers(evaluation_id),
        ).run(provider=provider, contract=contract, dataset=dataset)
    )


def _case_id_for_state(state: Mapping[str, JsonValue]) -> str:
    message = state["message"]
    assert isinstance(message, str)
    case_markers = {
        "duplicate charge": "refund-clear-001",
        "accessed by someone else": "unauthorized-critical-002",
        "cancel my subscription": "cancellation-normal-003",
        "current plan information": "information-low-004",
    }
    return next(case_id for marker, case_id in case_markers.items() if marker in message)


def _provider_result(
    *,
    case_id: str,
    provider_name: str,
    model_name: str,
    incorrect: bool,
) -> ProviderResult:
    expected_answers: dict[str, tuple[str, float, int]] = {
        "refund-clear-001": ("refund", 0.05, 0),
        "unauthorized-critical-002": ("support", 0.95, 3),
        "cancellation-normal-003": ("cancellation", 0.05, 1),
        "information-low-004": ("information", 0.05, 0),
    }
    intent, unauthorized_activity, urgency = expected_answers[case_id]
    if incorrect:
        intent = "support"
    return ProviderResult(
        metadata=ProviderMetadata(
            provider=provider_name,
            requested_model=model_name,
            resolved_model=f"{model_name}-resolved",
        ),
        latency_ms=12,
        answers=(
            ChoiceAnswer(
                question_id="intent",
                choice=intent,
                probabilities={
                    option: 0.97 if option == intent else 0.01
                    for option in ("refund", "cancellation", "support", "information")
                },
                confidence=0.99,
            ),
            NoulAnswer(question_id="unauthorized_activity", noul=unauthorized_activity),
            ScoreAnswer(
                question_id="urgency",
                score=float(urgency),
                legend={0: "low", 1: "medium", 2: "high", 3: "critical"},
                probabilities={score: 0.97 if score == urgency else 0.01 for score in range(4)},
                confidence=0.99,
            ),
        ),
    )


def test_replay_is_bounded_complete_and_excludes_raw_case_state() -> None:
    provider = FixtureReplayProvider(provider_name="baseline", model_name="baseline-v1")
    artifact = run_replay(
        provider,
        evaluation_id=UUID("00000000-0000-0000-0000-000000000011"),
        max_concurrency=2,
    )

    assert artifact.evaluation_id == UUID("00000000-0000-0000-0000-000000000011")
    assert artifact.status == EvaluationRunStatus.COMPLETE
    assert provider.max_active_requests <= 2
    assert tuple(case.case_id for case in artifact.metrics.cases) == (
        "refund-clear-001",
        "unauthorized-critical-002",
        "cancellation-normal-003",
        "information-low-004",
    )
    assert artifact.providers == ("baseline",)
    serialized = replay_artifact_json(artifact)
    assert serialized == replay_artifact_json(artifact)
    assert "Synthetic fixture" not in serialized
    assert '"message"' not in serialized


def test_regression_detects_case_regression_and_applies_current_quality_gate() -> None:
    baseline = run_replay(
        FixtureReplayProvider(provider_name="baseline", model_name="baseline-v1"),
        evaluation_id=UUID("00000000-0000-0000-0000-000000000012"),
    )
    current = run_replay(
        FixtureReplayProvider(
            provider_name="candidate",
            model_name="candidate-v2",
            incorrect_case_id="cancellation-normal-003",
        ),
        evaluation_id=UUID("00000000-0000-0000-0000-000000000013"),
    )

    comparison = RegressionEngine().compare(
        baseline,
        current,
        thresholds=RegressionThresholds(minimum_accuracy=0.99, maximum_provider_error_rate=0.0),
    )

    assert comparison.outcome == RegressionOutcome.FAIL
    assert comparison.baseline_providers == ("baseline",)
    assert comparison.current_providers == ("candidate",)
    assert comparison.current_requested_models == ("candidate-v2",)
    overall_accuracy = next(
        delta for delta in comparison.metric_deltas if delta.name == "overall_accuracy"
    )
    assert overall_accuracy.delta == pytest.approx(-1 / 12)
    assert len(comparison.changed_cases) == 1
    changed_case = comparison.changed_cases[0]
    assert changed_case.case_id == "cancellation-normal-003"
    assert changed_case.kind == CaseChangeKind.REGRESSED
    assert changed_case.baseline == CaseQualityState.CORRECT
    assert changed_case.current == CaseQualityState.INCORRECT
    assert not next(
        check for check in comparison.threshold_checks if check.name == "minimum_accuracy"
    ).passed
    assert regression_comparison_json(comparison) == regression_comparison_json(comparison)


def test_partial_or_fingerprint_mismatched_runs_are_explicitly_incomparable() -> None:
    baseline = run_replay(
        FixtureReplayProvider(provider_name="baseline", model_name="baseline-v1"),
        evaluation_id=UUID("00000000-0000-0000-0000-000000000014"),
    )
    partial = run_replay(
        FixtureReplayProvider(
            provider_name="candidate",
            model_name="candidate-v2",
            fail_case_id="information-low-004",
        ),
        evaluation_id=UUID("00000000-0000-0000-0000-000000000015"),
    )

    partial_comparison = RegressionEngine().compare(baseline, partial)
    assert partial.status == EvaluationRunStatus.PARTIAL
    assert partial_comparison.outcome == RegressionOutcome.INCOMPARABLE
    assert partial_comparison.compatibility_reasons == ("current_run_is_partial",)
    assert partial_comparison.metric_deltas == ()
    assert partial_comparison.threshold_checks == ()

    mismatched = baseline.model_copy(update={"dataset_fingerprint": "0" * 64})
    mismatch_comparison = RegressionEngine().compare(baseline, mismatched)
    assert mismatch_comparison.outcome == RegressionOutcome.INCOMPARABLE
    assert mismatch_comparison.compatibility_reasons == ("dataset_fingerprint_mismatch",)


def test_baseline_selection_is_explicit_and_rejects_missing_or_duplicate_ids() -> None:
    baseline = run_replay(
        FixtureReplayProvider(provider_name="baseline", model_name="baseline-v1"),
        evaluation_id=UUID("00000000-0000-0000-0000-000000000016"),
    )

    assert select_baseline([baseline], evaluation_id=baseline.evaluation_id) == baseline
    with pytest.raises(ValueError, match="exactly one"):
        select_baseline([], evaluation_id=baseline.evaluation_id)
    with pytest.raises(ValueError, match="exactly one"):
        select_baseline([baseline, baseline], evaluation_id=baseline.evaluation_id)


@pytest.mark.parametrize("max_concurrency", [0, 17])
def test_replay_rejects_unsafe_concurrency_bounds(max_concurrency: int) -> None:
    with pytest.raises(ValueError, match="within"):
        ReplayEngine(max_concurrency=max_concurrency)
