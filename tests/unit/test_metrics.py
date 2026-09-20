from __future__ import annotations

import json
from pathlib import Path

import pytest

from decisionops.contracts import load_contract
from decisionops.contracts.schema import ValidatedContract
from decisionops.evaluation import (
    CaseEvaluation,
    MetricsEngine,
    MetricsEvaluationError,
    load_dataset,
    report_json,
)
from decisionops.models import (
    ChoiceAnswer,
    EvaluationMetricsReport,
    NoulAnswer,
    PolicyEvaluation,
    PolicyOutcome,
    ProviderFailure,
    ProviderFailureKind,
    ProviderMetadata,
    ProviderResult,
    QuestionMetrics,
    ScoreAnswer,
    ValidatedDataset,
)

REPOSITORY_ROOT = Path(__file__).parents[2]
CONTRACT_PATH = REPOSITORY_ROOT / "contracts" / "support-ticket-triage.yaml"
DATASET_PATH = REPOSITORY_ROOT / "tests" / "fixtures" / "datasets" / "support-ticket-triage-v1.yaml"


def contract_and_dataset() -> tuple[ValidatedContract, ValidatedDataset]:
    contract = load_contract(CONTRACT_PATH)
    return contract, load_dataset(DATASET_PATH, contract)


def provider_result(
    *,
    latency_ms: int,
    intent: str,
    intent_probabilities: dict[str, float],
    intent_confidence: float,
    unauthorized_probability: float,
    urgency_score: float,
    urgency_probabilities: dict[int, float],
    urgency_confidence: float,
) -> ProviderResult:
    return ProviderResult(
        metadata=ProviderMetadata(provider="fake", requested_model="fixture-model"),
        latency_ms=latency_ms,
        answers=(
            ChoiceAnswer(
                question_id="intent",
                choice=intent,
                probabilities=intent_probabilities,
                confidence=intent_confidence,
            ),
            NoulAnswer(question_id="unauthorized_activity", noul=unauthorized_probability),
            ScoreAnswer(
                question_id="urgency",
                score=urgency_score,
                legend={0: "low", 1: "medium", 2: "high", 3: "critical"},
                probabilities=urgency_probabilities,
                confidence=urgency_confidence,
            ),
        ),
    )


def fixture_evaluations() -> tuple[CaseEvaluation, ...]:
    return (
        CaseEvaluation(
            case_id="refund-clear-001",
            result=provider_result(
                latency_ms=10,
                intent="refund",
                intent_probabilities={
                    "refund": 0.80,
                    "cancellation": 0.05,
                    "support": 0.10,
                    "information": 0.05,
                },
                intent_confidence=0.90,
                unauthorized_probability=0.10,
                urgency_score=0.50,
                urgency_probabilities={0: 0.70, 1: 0.20, 2: 0.05, 3: 0.05},
                urgency_confidence=0.90,
            ),
            policy=PolicyEvaluation(outcome=PolicyOutcome.ACT),
        ),
        CaseEvaluation(
            case_id="unauthorized-critical-002",
            result=provider_result(
                latency_ms=20,
                intent="support",
                intent_probabilities={
                    "refund": 0.10,
                    "cancellation": 0.10,
                    "support": 0.70,
                    "information": 0.10,
                },
                intent_confidence=0.80,
                unauthorized_probability=0.95,
                urgency_score=2.80,
                urgency_probabilities={0: 0.05, 1: 0.05, 2: 0.30, 3: 0.60},
                urgency_confidence=0.80,
            ),
            policy=PolicyEvaluation(outcome=PolicyOutcome.REVIEW),
        ),
        CaseEvaluation(
            case_id="cancellation-normal-003",
            result=provider_result(
                latency_ms=30,
                intent="support",
                intent_probabilities={
                    "refund": 0.05,
                    "cancellation": 0.30,
                    "support": 0.60,
                    "information": 0.05,
                },
                intent_confidence=0.75,
                unauthorized_probability=0.20,
                urgency_score=1.20,
                urgency_probabilities={0: 0.10, 1: 0.70, 2: 0.15, 3: 0.05},
                urgency_confidence=0.70,
            ),
            policy=PolicyEvaluation(outcome=PolicyOutcome.FALLBACK),
        ),
        CaseEvaluation(
            case_id="information-low-004",
            failure=ProviderFailure(
                kind=ProviderFailureKind.TIMEOUT,
                message="synthetic timeout",
            ),
        ),
    )


def report() -> EvaluationMetricsReport:
    contract, dataset = contract_and_dataset()
    return MetricsEngine(calibration_bucket_count=10).evaluate(
        contract,
        dataset,
        fixture_evaluations(),
    )


def question_metrics(question_id: str) -> QuestionMetrics:
    return next(question for question in report().questions if question.question_id == question_id)


def test_metrics_apply_primitive_formulas_and_keep_confidence_separate() -> None:
    result = report()
    noul = question_metrics("unauthorized_activity")
    choice = question_metrics("intent")
    score = question_metrics("urgency")

    assert result.total_cases == 4
    assert result.operational.provider_successes == 3
    assert result.operational.provider_failures == 1
    assert result.scored_answers == 9
    assert result.unscored_answers == 3
    assert result.overall_accuracy.value == pytest.approx(8 / 9)
    assert noul.accuracy.value == 1.0
    assert noul.brier_score.value == pytest.approx(0.0175)
    assert noul.probability_calibration.ece is not None
    assert noul.probability_calibration.ece.value == pytest.approx((0.1 + 0.05 + 0.2) / 3)
    assert noul.confidence_buckets is None
    assert choice.accuracy.value == pytest.approx(2 / 3)
    assert choice.brier_score.value == pytest.approx((0.055 + 0.12 + 0.855) / 3)
    assert choice.probability_calibration.ece is not None
    assert choice.probability_calibration.ece.value == pytest.approx((0.2 + 0.3 + 0.6) / 3)
    assert choice.confidence_buckets is not None
    assert sum(bucket.sample_count for bucket in choice.confidence_buckets.buckets) == 3
    assert score.accuracy.value == 1.0
    assert score.confusion_matrix["low"]["low"] == 1


def test_policy_coverage_quality_and_operational_metrics_are_separate() -> None:
    result = report()

    assert result.automation.act_coverage.value == pytest.approx(0.25)
    assert result.automation.act_subset_accuracy.value == 1.0
    assert result.automation.selective_risk.value == 0.0
    assert result.automation.review_rate.value == pytest.approx(0.25)
    assert result.automation.fallback_rate.value == pytest.approx(0.25)
    assert result.automation.provider_error_rate.value == pytest.approx(0.25)
    assert result.operational.latency_p50_ms.value == 20.0
    assert result.operational.latency_p95_ms.value == 30.0


def test_invalid_distribution_is_unscored_not_a_silent_incorrect_prediction() -> None:
    contract, dataset = contract_and_dataset()
    evaluations = list(fixture_evaluations())
    first = evaluations[0]
    assert first.result is not None
    invalid_choice = ChoiceAnswer(
        question_id="intent",
        choice="refund",
        probabilities={
            "refund": 0.70,
            "cancellation": 0.05,
            "support": 0.10,
            "information": 0.05,
        },
        confidence=0.90,
    )
    evaluations[0] = CaseEvaluation(
        case_id=first.case_id,
        result=first.result.model_copy(
            update={"answers": (invalid_choice, *first.result.answers[1:])}
        ),
        policy=first.policy,
    )

    result = MetricsEngine().evaluate(contract, dataset, evaluations)
    choice = next(question for question in result.questions if question.question_id == "intent")

    assert choice.scored_answers == 2
    assert choice.unscored_answers == 2
    assert choice.accuracy.value == pytest.approx(0.5)
    assert result.operational.provider_successes == 3


def test_zero_scored_cases_return_not_applicable_metrics_instead_of_nan() -> None:
    contract, dataset = contract_and_dataset()
    failures = tuple(
        CaseEvaluation(
            case_id=case.case_id,
            failure=ProviderFailure(
                kind=ProviderFailureKind.TRANSPORT,
                message="synthetic transport failure",
            ),
        )
        for case in dataset.dataset.cases
    )

    result = MetricsEngine().evaluate(contract, dataset, failures)

    assert result.scored_answers == 0
    assert result.overall_accuracy.value is None
    assert result.overall_accuracy.denominator == 0
    assert result.questions[0].brier_score.value is None
    assert result.questions[0].probability_calibration.ece is not None
    assert result.questions[0].probability_calibration.ece.value is None
    assert result.operational.latency_p50_ms.value is None
    assert result.automation.act_subset_accuracy.value is None


def test_metric_input_requires_exactly_one_evaluation_for_each_dataset_case() -> None:
    contract, dataset = contract_and_dataset()

    with pytest.raises(MetricsEvaluationError, match="missing"):
        MetricsEngine().evaluate(contract, dataset, fixture_evaluations()[:-1])


def test_report_json_is_stable_and_excludes_fixture_state() -> None:
    first = report_json(report())
    second = report_json(report())

    assert first == second
    assert "Synthetic fixture" not in first
    assert json.loads(first)["total_cases"] == 4


def test_case_evaluation_requires_one_terminal_provider_state() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        CaseEvaluation(case_id="case-1")
