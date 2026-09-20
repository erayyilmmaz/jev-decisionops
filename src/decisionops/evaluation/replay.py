"""Bounded dataset replay and deterministic baseline regression comparison."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager, nullcontext
from uuid import UUID

from decisionops.clock import IdentifierGenerator, Uuid4Generator
from decisionops.contracts.schema import ValidatedContract
from decisionops.evaluation.metrics import CaseEvaluation, MetricsEngine
from decisionops.models import (
    CaseChange,
    CaseChangeKind,
    CaseQuality,
    CaseQualityState,
    EvaluationMetricsReport,
    EvaluationRunStatus,
    JsonValue,
    MetricDelta,
    PolicyEvaluation,
    PolicyOutcome,
    ProviderFailure,
    ProviderFailureKind,
    QuestionMetricDelta,
    RegressionComparison,
    RegressionOutcome,
    RegressionThresholds,
    ReplayRunArtifact,
    ThresholdCheck,
    ValidatedDataset,
)
from decisionops.policy import PolicyEngine, PolicyEvaluationError
from decisionops.providers import DecisionProvider, ProviderRequest


class ReplayEngine:
    """Run a bounded, ordered dataset replay without persistence or live-only assumptions."""

    def __init__(
        self,
        *,
        policy_engine: PolicyEngine | None = None,
        metrics_engine: MetricsEngine | None = None,
        max_concurrency: int = 4,
        identifiers: IdentifierGenerator | None = None,
        case_span: Callable[[], AbstractContextManager[None]] | None = None,
        aggregate_span: Callable[[], AbstractContextManager[None]] | None = None,
        policy_span: Callable[[], AbstractContextManager[None]] | None = None,
        policy_outcome_observer: Callable[[PolicyOutcome], None] | None = None,
    ) -> None:
        if not 1 <= max_concurrency <= 16:
            raise ValueError("max_concurrency must be within [1, 16]")
        self._policy_engine = policy_engine or PolicyEngine()
        self._metrics_engine = metrics_engine or MetricsEngine()
        self._max_concurrency = max_concurrency
        self._identifiers = identifiers or Uuid4Generator()
        self._case_span = case_span or nullcontext
        self._aggregate_span = aggregate_span or nullcontext
        self._policy_span = policy_span or nullcontext
        self._policy_outcome_observer = policy_outcome_observer

    async def run(
        self,
        *,
        provider: DecisionProvider,
        contract: ValidatedContract,
        dataset: ValidatedDataset,
    ) -> ReplayRunArtifact:
        """Evaluate every ordered case, then derive one metrics artifact."""

        with self._aggregate_span():
            semaphore = asyncio.Semaphore(self._max_concurrency)
            outcomes = await asyncio.gather(
                *(
                    self._run_case(
                        semaphore=semaphore,
                        provider=provider,
                        contract=contract,
                        case_id=case.case_id,
                        state=case.state,
                    )
                    for case in dataset.dataset.cases
                )
            )
            evaluations = tuple(outcome.evaluation for outcome in outcomes)
            policy_errors = sum(outcome.policy_error for outcome in outcomes)
            metrics = self._metrics_engine.evaluate(contract, dataset, evaluations)
            return ReplayRunArtifact(
                evaluation_id=self._identifiers.new(),
                status=_run_status(metrics, policy_errors),
                contract_fingerprint=contract.fingerprint,
                dataset_fingerprint=dataset.fingerprint,
                providers=_provider_names(evaluations),
                requested_models=_requested_models(evaluations),
                resolved_models=_resolved_models(evaluations),
                policy_errors=policy_errors,
                metrics=metrics,
            )

    async def _run_case(
        self,
        *,
        semaphore: asyncio.Semaphore,
        provider: DecisionProvider,
        contract: ValidatedContract,
        case_id: str,
        state: dict[str, JsonValue],
    ) -> _CaseReplayOutcome:
        with self._case_span():
            async with semaphore:
                request = ProviderRequest(
                    contract=contract.contract,
                    canonical_json=contract.canonical_json,
                    fingerprint=contract.fingerprint,
                    state=state,
                )
                try:
                    terminal = await provider.evaluate(request)
                except Exception:
                    return _CaseReplayOutcome(
                        evaluation=CaseEvaluation(
                            case_id=case_id,
                            failure=ProviderFailure(
                                provider="replay_runtime",
                                kind=ProviderFailureKind.UNKNOWN,
                                message="provider evaluation raised an unexpected error",
                            ),
                        ),
                    )

            if isinstance(terminal, ProviderFailure):
                return _CaseReplayOutcome(
                    evaluation=CaseEvaluation(case_id=case_id, failure=terminal)
                )

            policy: PolicyEvaluation | None = None
            policy_error = False
            try:
                with self._policy_span():
                    policy = self._policy_engine.evaluate(contract.contract, terminal)
                if self._policy_outcome_observer is not None:
                    try:
                        self._policy_outcome_observer(policy.outcome)
                    except Exception:
                        pass
            except PolicyEvaluationError:
                policy_error = True
            return _CaseReplayOutcome(
                evaluation=CaseEvaluation(case_id=case_id, result=terminal, policy=policy),
                policy_error=policy_error,
            )


class RegressionEngine:
    """Compare two complete compatible replay artifacts and apply V1 quality gates."""

    def compare(
        self,
        baseline: ReplayRunArtifact,
        current: ReplayRunArtifact,
        *,
        thresholds: RegressionThresholds | None = None,
    ) -> RegressionComparison:
        """Return PASS, FAIL, or explicit INCOMPARABLE without hidden fallback behavior."""

        reasons = _compatibility_reasons(baseline, current)
        if reasons:
            return _comparison(
                outcome=RegressionOutcome.INCOMPARABLE,
                baseline=baseline,
                current=current,
                compatibility_reasons=tuple(reasons),
            )

        metric_deltas = _metric_deltas(baseline.metrics, current.metrics)
        question_deltas = _question_deltas(baseline.metrics, current.metrics)
        changed_cases = _changed_cases(baseline.metrics.cases, current.metrics.cases)
        threshold_checks = _threshold_checks(current.metrics, thresholds or RegressionThresholds())
        outcome = (
            RegressionOutcome.FAIL
            if any(not check.passed for check in threshold_checks)
            else RegressionOutcome.PASS
        )
        return _comparison(
            outcome=outcome,
            baseline=baseline,
            current=current,
            metric_deltas=metric_deltas,
            question_deltas=question_deltas,
            changed_cases=changed_cases,
            threshold_checks=threshold_checks,
        )


def replay_artifact_json(artifact: ReplayRunArtifact) -> str:
    """Serialize a replay artifact deterministically without raw input state."""

    return json.dumps(
        artifact.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def regression_comparison_json(comparison: RegressionComparison) -> str:
    """Serialize a baseline comparison deterministically for CI artifacts."""

    return json.dumps(
        comparison.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def select_baseline(
    artifacts: Sequence[ReplayRunArtifact],
    *,
    evaluation_id: UUID,
) -> ReplayRunArtifact:
    """Select exactly one explicit baseline; never infer recency or compatibility."""

    matches = [artifact for artifact in artifacts if artifact.evaluation_id == evaluation_id]
    if len(matches) != 1:
        raise ValueError("baseline evaluation_id must identify exactly one replay artifact")
    return matches[0]


class _CaseReplayOutcome:
    def __init__(self, *, evaluation: CaseEvaluation, policy_error: bool = False) -> None:
        self.evaluation = evaluation
        self.policy_error = policy_error


def _run_status(metrics: EvaluationMetricsReport, policy_errors: int) -> EvaluationRunStatus:
    if metrics.operational.provider_failures or metrics.unscored_answers or policy_errors:
        return EvaluationRunStatus.PARTIAL
    return EvaluationRunStatus.COMPLETE


def _provider_names(evaluations: Sequence[CaseEvaluation]) -> tuple[str, ...]:
    return tuple(sorted({_provider_name(evaluation) for evaluation in evaluations}))


def _provider_name(evaluation: CaseEvaluation) -> str:
    if evaluation.result is not None:
        return evaluation.result.metadata.provider
    if evaluation.failure is not None:
        return evaluation.failure.provider or "unknown"
    return "unknown"


def _requested_models(evaluations: Sequence[CaseEvaluation]) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                result.metadata.requested_model
                for evaluation in evaluations
                if (result := evaluation.result) is not None
                and result.metadata.requested_model is not None
            }
        )
    )


def _resolved_models(evaluations: Sequence[CaseEvaluation]) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                result.metadata.resolved_model
                for evaluation in evaluations
                if (result := evaluation.result) is not None
                and result.metadata.resolved_model is not None
            }
        )
    )


def _compatibility_reasons(
    baseline: ReplayRunArtifact,
    current: ReplayRunArtifact,
) -> list[str]:
    reasons: list[str] = []
    if baseline.status != EvaluationRunStatus.COMPLETE:
        reasons.append("baseline_run_is_partial")
    if current.status != EvaluationRunStatus.COMPLETE:
        reasons.append("current_run_is_partial")
    if baseline.contract_fingerprint != current.contract_fingerprint:
        reasons.append("contract_fingerprint_mismatch")
    if baseline.dataset_fingerprint != current.dataset_fingerprint:
        reasons.append("dataset_fingerprint_mismatch")
    if {case.case_id for case in baseline.metrics.cases} != {
        case.case_id for case in current.metrics.cases
    }:
        reasons.append("case_set_mismatch")
    return reasons


def _metric_deltas(
    baseline: EvaluationMetricsReport,
    current: EvaluationMetricsReport,
) -> tuple[MetricDelta, ...]:
    return (
        _delta("overall_accuracy", baseline.overall_accuracy.value, current.overall_accuracy.value),
        _delta(
            "act_subset_accuracy",
            baseline.automation.act_subset_accuracy.value,
            current.automation.act_subset_accuracy.value,
        ),
        _delta(
            "provider_error_rate",
            baseline.automation.provider_error_rate.value,
            current.automation.provider_error_rate.value,
        ),
        _delta(
            "latency_p95_ms",
            baseline.operational.latency_p95_ms.value,
            current.operational.latency_p95_ms.value,
        ),
    )


def _question_deltas(
    baseline: EvaluationMetricsReport,
    current: EvaluationMetricsReport,
) -> tuple[QuestionMetricDelta, ...]:
    baseline_questions = {question.question_id: question for question in baseline.questions}
    current_questions = {question.question_id: question for question in current.questions}
    return tuple(
        QuestionMetricDelta(
            question_id=question_id,
            accuracy=_delta(
                "accuracy",
                baseline_questions[question_id].accuracy.value,
                current_questions[question_id].accuracy.value,
            ),
            brier_score=_delta(
                "brier_score",
                baseline_questions[question_id].brier_score.value,
                current_questions[question_id].brier_score.value,
            ),
            ece=_delta(
                "ece",
                _ece_value(baseline_questions[question_id]),
                _ece_value(current_questions[question_id]),
            ),
        )
        for question_id in sorted(baseline_questions.keys() & current_questions.keys())
    )


def _changed_cases(
    baseline_cases: Sequence[CaseQuality],
    current_cases: Sequence[CaseQuality],
) -> tuple[CaseChange, ...]:
    baseline_by_id = {case.case_id: case for case in baseline_cases}
    changes: list[CaseChange] = []
    for current in current_cases:
        baseline = baseline_by_id[current.case_id]
        if baseline.state == current.state:
            continue
        direction = _quality_rank(current.state) - _quality_rank(baseline.state)
        changes.append(
            CaseChange(
                case_id=current.case_id,
                kind=(
                    CaseChangeKind.IMPROVED
                    if direction > 0
                    else CaseChangeKind.REGRESSED
                    if direction < 0
                    else CaseChangeKind.CHANGED
                ),
                baseline=baseline.state,
                current=current.state,
            )
        )
    return tuple(changes)


def _threshold_checks(
    metrics: EvaluationMetricsReport,
    thresholds: RegressionThresholds,
) -> tuple[ThresholdCheck, ...]:
    checks: list[ThresholdCheck] = []
    if thresholds.minimum_accuracy is not None:
        checks.append(
            _minimum_check(
                "minimum_accuracy", metrics.overall_accuracy.value, thresholds.minimum_accuracy
            )
        )
    if thresholds.maximum_ece is not None:
        checks.extend(
            _maximum_check(
                f"maximum_ece:{question.question_id}",
                _ece_value(question),
                thresholds.maximum_ece,
            )
            for question in metrics.questions
        )
    if thresholds.maximum_brier_score is not None:
        checks.extend(
            _maximum_check(
                f"maximum_brier_score:{question.question_id}",
                question.brier_score.value,
                thresholds.maximum_brier_score,
            )
            for question in metrics.questions
        )
    if thresholds.minimum_act_accuracy is not None:
        checks.append(
            _minimum_check(
                "minimum_act_accuracy",
                metrics.automation.act_subset_accuracy.value,
                thresholds.minimum_act_accuracy,
            )
        )
    if thresholds.maximum_provider_error_rate is not None:
        checks.append(
            _maximum_check(
                "maximum_provider_error_rate",
                metrics.automation.provider_error_rate.value,
                thresholds.maximum_provider_error_rate,
            )
        )
    if thresholds.maximum_latency_p95_ms is not None:
        checks.append(
            _maximum_check(
                "maximum_latency_p95_ms",
                metrics.operational.latency_p95_ms.value,
                thresholds.maximum_latency_p95_ms,
            )
        )
    return tuple(checks)


def _comparison(
    *,
    outcome: RegressionOutcome,
    baseline: ReplayRunArtifact,
    current: ReplayRunArtifact,
    compatibility_reasons: tuple[str, ...] = (),
    metric_deltas: tuple[MetricDelta, ...] = (),
    question_deltas: tuple[QuestionMetricDelta, ...] = (),
    changed_cases: tuple[CaseChange, ...] = (),
    threshold_checks: tuple[ThresholdCheck, ...] = (),
) -> RegressionComparison:
    return RegressionComparison(
        outcome=outcome,
        compatibility_reasons=compatibility_reasons,
        baseline_evaluation_id=baseline.evaluation_id,
        current_evaluation_id=current.evaluation_id,
        baseline_providers=baseline.providers,
        current_providers=current.providers,
        baseline_requested_models=baseline.requested_models,
        current_requested_models=current.requested_models,
        baseline_resolved_models=baseline.resolved_models,
        current_resolved_models=current.resolved_models,
        metric_deltas=metric_deltas,
        question_deltas=question_deltas,
        changed_cases=changed_cases,
        threshold_checks=threshold_checks,
    )


def _delta(name: str, baseline: float | None, current: float | None) -> MetricDelta:
    return MetricDelta(
        name=name,
        baseline=baseline,
        current=current,
        delta=current - baseline if baseline is not None and current is not None else None,
    )


def _ece_value(question: object) -> float | None:
    if not hasattr(question, "probability_calibration"):
        return None
    ece = question.probability_calibration.ece
    return ece.value if ece is not None else None


def _quality_rank(state: CaseQualityState) -> int:
    return {
        CaseQualityState.PROVIDER_FAILURE: 0,
        CaseQualityState.UNSCORED: 1,
        CaseQualityState.INCORRECT: 2,
        CaseQualityState.CORRECT: 3,
    }[state]


def _minimum_check(name: str, actual: float | None, threshold: float) -> ThresholdCheck:
    return ThresholdCheck(
        name=name,
        actual=actual,
        threshold=threshold,
        passed=actual is not None and actual >= threshold,
    )


def _maximum_check(name: str, actual: float | None, threshold: float) -> ThresholdCheck:
    return ThresholdCheck(
        name=name,
        actual=actual,
        threshold=threshold,
        passed=actual is not None and actual <= threshold,
    )
