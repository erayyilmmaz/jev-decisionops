"""Deterministic offline quality, calibration, policy, and latency metrics."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from math import ceil, isfinite, log
from typing import Literal

from decisionops.contracts.schema import (
    ChoiceQuestion,
    ContractQuestion,
    NoulQuestion,
    ScoreQuestion,
    ValidatedContract,
)
from decisionops.evaluation.errors import MetricsEvaluationError
from decisionops.models import (
    AutomationMetrics,
    CalibrationBucket,
    CalibrationReport,
    ChoiceAnswer,
    DecisionAnswer,
    EvaluationMetricsReport,
    MetricValue,
    NoulAnswer,
    OperationalMetrics,
    PolicyEvaluation,
    PolicyOutcome,
    ProviderFailure,
    ProviderResult,
    QuestionMetrics,
    ScoreAnswer,
    ValidatedDataset,
)

_DISTRIBUTION_TOLERANCE = 1e-9
_LOG_LOSS_EPSILON = 1e-15


@dataclass(frozen=True)
class CaseEvaluation:
    """One provider terminal state and optional policy evaluation for a dataset case."""

    case_id: str
    result: ProviderResult | None = None
    failure: ProviderFailure | None = None
    policy: PolicyEvaluation | None = None

    def __post_init__(self) -> None:
        if (self.result is None) == (self.failure is None):
            raise ValueError("exactly one of result or failure is required")
        if self.policy is not None and self.result is None:
            raise ValueError("a policy evaluation requires a successful provider result")


@dataclass(frozen=True)
class _ScoredAnswer:
    prediction: str
    truth: str
    correct: bool
    brier: float
    log_loss: float
    calibration_prediction: float
    calibration_observed: float
    confidence: float | None


class MetricsEngine:
    """Compute reproducible metrics without provider, database, or clock access."""

    def __init__(self, *, calibration_bucket_count: int = 10) -> None:
        if not 2 <= calibration_bucket_count <= 100:
            raise ValueError("calibration_bucket_count must be within [2, 100]")
        self._calibration_bucket_count = calibration_bucket_count

    def evaluate(
        self,
        contract: ValidatedContract,
        dataset: ValidatedDataset,
        evaluations: Sequence[CaseEvaluation],
    ) -> EvaluationMetricsReport:
        """Score one terminal provider state per dataset case against ground truth."""

        _require_dataset_contract_match(contract, dataset)
        evaluation_by_case = _index_evaluations(dataset, evaluations)
        questions = contract.contract.questions
        scored_by_question: dict[str, list[_ScoredAnswer]] = {
            question_id: [] for question_id in questions
        }
        unscored_by_question: dict[str, int] = {question_id: 0 for question_id in questions}
        provider_successes = 0
        provider_failures = 0
        successful_latencies: list[int] = []
        act_cases = 0
        act_scored_cases = 0
        act_correct_cases = 0
        act_unscored_cases = 0
        review_cases = 0
        fallback_cases = 0

        for case in dataset.dataset.cases:
            evaluation = evaluation_by_case[case.case_id]
            if evaluation.failure is not None:
                provider_failures += 1
                for question_id in questions:
                    unscored_by_question[question_id] += 1
                continue

            assert evaluation.result is not None
            provider_successes += 1
            successful_latencies.append(evaluation.result.latency_ms)
            answers_by_question = _answers_by_question(evaluation.result.answers, questions)
            case_scores: list[_ScoredAnswer] = []

            for question_id, question in questions.items():
                answer = answers_by_question.get(question_id)
                if answer is None:
                    unscored_by_question[question_id] += 1
                    continue
                scored = _score_answer(question, case.labels[question_id], answer)
                if scored is None:
                    unscored_by_question[question_id] += 1
                    continue
                scored_by_question[question_id].append(scored)
                case_scores.append(scored)

            if evaluation.policy is not None:
                if evaluation.policy.outcome == PolicyOutcome.ACT:
                    act_cases += 1
                    if len(case_scores) != len(questions):
                        act_unscored_cases += 1
                    else:
                        act_scored_cases += 1
                        if all(score.correct for score in case_scores):
                            act_correct_cases += 1
                elif evaluation.policy.outcome == PolicyOutcome.REVIEW:
                    review_cases += 1
                elif evaluation.policy.outcome == PolicyOutcome.FALLBACK:
                    fallback_cases += 1

        question_reports = tuple(
            _question_metrics(
                question_id=question_id,
                question=question,
                scores=scored_by_question[question_id],
                unscored_answers=unscored_by_question[question_id],
                bucket_count=self._calibration_bucket_count,
            )
            for question_id, question in questions.items()
        )
        all_scores = [score for scores in scored_by_question.values() for score in scores]
        total_cases = len(dataset.dataset.cases)
        scored_answers = len(all_scores)

        return EvaluationMetricsReport(
            contract_fingerprint=contract.fingerprint,
            dataset_fingerprint=dataset.fingerprint,
            total_cases=total_cases,
            scored_answers=scored_answers,
            unscored_answers=sum(unscored_by_question.values()),
            overall_accuracy=_mean_metric(
                (1.0 if score.correct else 0.0 for score in all_scores),
                denominator=scored_answers,
            ),
            questions=question_reports,
            automation=_automation_metrics(
                total_cases=total_cases,
                act_cases=act_cases,
                act_scored_cases=act_scored_cases,
                act_correct_cases=act_correct_cases,
                act_unscored_cases=act_unscored_cases,
                review_cases=review_cases,
                fallback_cases=fallback_cases,
                provider_failures=provider_failures,
            ),
            operational=OperationalMetrics(
                provider_successes=provider_successes,
                provider_failures=provider_failures,
                latency_p50_ms=_percentile_metric(successful_latencies, 0.50),
                latency_p95_ms=_percentile_metric(successful_latencies, 0.95),
            ),
        )


def report_json(report: EvaluationMetricsReport) -> str:
    """Serialize a report with stable mapping-key order for reproducible artifacts."""

    return json.dumps(
        report.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _require_dataset_contract_match(contract: ValidatedContract, dataset: ValidatedDataset) -> None:
    reference = dataset.dataset.contract
    expected = contract.contract
    if (
        reference.name != expected.name
        or reference.version != expected.version
        or reference.fingerprint != contract.fingerprint
    ):
        raise MetricsEvaluationError("dataset contract identity does not match the loaded contract")


def _index_evaluations(
    dataset: ValidatedDataset,
    evaluations: Sequence[CaseEvaluation],
) -> dict[str, CaseEvaluation]:
    indexed: dict[str, CaseEvaluation] = {}
    for evaluation in evaluations:
        if evaluation.case_id in indexed:
            raise MetricsEvaluationError(f"duplicate evaluation for case {evaluation.case_id!r}")
        indexed[evaluation.case_id] = evaluation

    expected_case_ids = {case.case_id for case in dataset.dataset.cases}
    actual_case_ids = set(indexed)
    if expected_case_ids != actual_case_ids:
        missing = sorted(expected_case_ids - actual_case_ids)
        unknown = sorted(actual_case_ids - expected_case_ids)
        raise MetricsEvaluationError(
            f"evaluation cases do not match dataset; missing={missing!r}, unknown={unknown!r}"
        )
    return indexed


def _answers_by_question(
    answers: tuple[DecisionAnswer, ...],
    questions: Mapping[str, ContractQuestion],
) -> dict[str, DecisionAnswer]:
    indexed: dict[str, DecisionAnswer] = {}
    duplicate_question_ids: set[str] = set()
    for answer in answers:
        if answer.question_id not in questions:
            continue
        if answer.question_id in indexed:
            duplicate_question_ids.add(answer.question_id)
        indexed[answer.question_id] = answer
    for question_id in duplicate_question_ids:
        del indexed[question_id]
    return indexed


def _score_answer(
    question: ContractQuestion,
    label: object,
    answer: DecisionAnswer,
) -> _ScoredAnswer | None:
    if isinstance(question, NoulQuestion) and isinstance(answer, NoulAnswer):
        return _score_noul(label, answer)
    if isinstance(question, ChoiceQuestion) and isinstance(answer, ChoiceAnswer):
        return _score_choice(question, label, answer)
    if isinstance(question, ScoreQuestion) and isinstance(answer, ScoreAnswer):
        return _score_score(question, label, answer)
    return None


def _score_noul(label: object, answer: NoulAnswer) -> _ScoredAnswer | None:
    if type(label) is not bool:
        return None
    truth_value = 1.0 if label else 0.0
    predicted_label = "true" if answer.noul >= 0.5 else "false"
    truth_label = "true" if label else "false"
    true_probability = answer.noul if label else 1.0 - answer.noul
    return _ScoredAnswer(
        prediction=predicted_label,
        truth=truth_label,
        correct=predicted_label == truth_label,
        brier=(answer.noul - truth_value) ** 2,
        log_loss=-log(_clip_probability(true_probability)),
        calibration_prediction=answer.noul,
        calibration_observed=truth_value,
        confidence=None,
    )


def _score_choice(
    question: ChoiceQuestion,
    label: object,
    answer: ChoiceAnswer,
) -> _ScoredAnswer | None:
    labels = set(question.criteria)
    if not isinstance(label, str) or label not in labels:
        return None
    if answer.choice not in labels or not _valid_distribution(answer.probabilities, labels):
        return None
    brier = sum(
        (answer.probabilities[candidate] - (1.0 if candidate == label else 0.0)) ** 2
        for candidate in labels
    )
    correct = answer.choice == label
    return _ScoredAnswer(
        prediction=answer.choice,
        truth=label,
        correct=correct,
        brier=brier,
        log_loss=-log(_clip_probability(answer.probabilities[label])),
        calibration_prediction=answer.probabilities[answer.choice],
        calibration_observed=1.0 if correct else 0.0,
        confidence=answer.confidence,
    )


def _score_score(
    question: ScoreQuestion,
    label: object,
    answer: ScoreAnswer,
) -> _ScoredAnswer | None:
    levels = tuple(sorted(answer.legend))
    if not isinstance(label, str) or label not in question.criteria or not isfinite(answer.score):
        return None
    if tuple(answer.legend[level] for level in levels) != question.criteria:
        return None
    if not _valid_distribution(answer.probabilities, set(levels)):
        return None
    predicted_level = min(levels, key=lambda level: (abs(answer.score - level), level))
    truth_level = next(level for level in levels if answer.legend[level] == label)
    predicted_label = answer.legend[predicted_level]
    if not isinstance(predicted_label, str):
        return None
    brier = sum(
        (answer.probabilities[level] - (1.0 if level == truth_level else 0.0)) ** 2
        for level in levels
    )
    correct = predicted_level == truth_level
    return _ScoredAnswer(
        prediction=predicted_label,
        truth=label,
        correct=correct,
        brier=brier,
        log_loss=-log(_clip_probability(answer.probabilities[truth_level])),
        calibration_prediction=answer.probabilities[predicted_level],
        calibration_observed=1.0 if correct else 0.0,
        confidence=answer.confidence,
    )


def _valid_distribution[DistributionKey: (str, int)](
    distribution: Mapping[DistributionKey, float], labels: set[DistributionKey]
) -> bool:
    return (
        set(distribution) == labels
        and all(isfinite(value) and 0.0 <= value <= 1.0 for value in distribution.values())
        and abs(sum(distribution.values()) - 1.0) <= _DISTRIBUTION_TOLERANCE
    )


def _clip_probability(value: float) -> float:
    return min(max(value, _LOG_LOSS_EPSILON), 1.0 - _LOG_LOSS_EPSILON)


def _question_metrics(
    *,
    question_id: str,
    question: ContractQuestion,
    scores: list[_ScoredAnswer],
    unscored_answers: int,
    bucket_count: int,
) -> QuestionMetrics:
    labels = _question_labels(question)
    confusion_matrix = {truth: {prediction: 0 for prediction in labels} for truth in labels}
    for score in scores:
        confusion_matrix[score.truth][score.prediction] += 1

    accuracy = _mean_metric((1.0 if score.correct else 0.0 for score in scores), len(scores))
    precision, recall, f1 = _macro_classification_metrics(confusion_matrix, labels)
    probability_calibration = _calibration_report(
        basis="probability",
        points=[(score.calibration_prediction, score.calibration_observed) for score in scores],
        bucket_count=bucket_count,
        calculate_ece=True,
    )
    confidence_points = [
        (score.confidence, 1.0 if score.correct else 0.0)
        for score in scores
        if score.confidence is not None
    ]
    return QuestionMetrics(
        question_id=question_id,
        kind=question.type,
        scored_answers=len(scores),
        unscored_answers=unscored_answers,
        accuracy=accuracy,
        precision=precision,
        recall=recall,
        f1=f1,
        brier_score=_mean_metric((score.brier for score in scores), len(scores)),
        log_loss=_mean_metric((score.log_loss for score in scores), len(scores)),
        confusion_matrix=confusion_matrix,
        probability_calibration=probability_calibration,
        confidence_buckets=(
            _calibration_report(
                basis="confidence",
                points=confidence_points,
                bucket_count=bucket_count,
                calculate_ece=False,
            )
            if confidence_points
            else None
        ),
    )


def _question_labels(question: ContractQuestion) -> tuple[str, ...]:
    if isinstance(question, NoulQuestion):
        return ("false", "true")
    if isinstance(question, ChoiceQuestion):
        return tuple(sorted(question.criteria))
    if isinstance(question, ScoreQuestion):
        return question.criteria
    raise AssertionError("unsupported contract question")


def _macro_classification_metrics(
    confusion_matrix: dict[str, dict[str, int]],
    labels: tuple[str, ...],
) -> tuple[MetricValue, MetricValue, MetricValue]:
    precision_values: list[float] = []
    recall_values: list[float] = []
    f1_values: list[float] = []
    for label in labels:
        true_positive = confusion_matrix[label][label]
        false_positive = sum(confusion_matrix[truth][label] for truth in labels if truth != label)
        false_negative = sum(
            confusion_matrix[label][prediction] for prediction in labels if prediction != label
        )
        precision = _ratio(true_positive, true_positive + false_positive)
        recall = _ratio(true_positive, true_positive + false_negative)
        if precision is not None:
            precision_values.append(precision)
        if recall is not None:
            recall_values.append(recall)
        if precision is not None and recall is not None and precision + recall > 0.0:
            f1_values.append(2.0 * precision * recall / (precision + recall))

    return (
        _mean_metric(precision_values, len(precision_values)),
        _mean_metric(recall_values, len(recall_values)),
        _mean_metric(f1_values, len(f1_values)),
    )


def _calibration_report(
    *,
    basis: Literal["probability", "confidence"],
    points: list[tuple[float, float]],
    bucket_count: int,
    calculate_ece: bool,
) -> CalibrationReport:
    grouped: list[list[tuple[float, float]]] = [[] for _ in range(bucket_count)]
    for prediction, observed in points:
        bucket_index = min(int(prediction * bucket_count), bucket_count - 1)
        grouped[bucket_index].append((prediction, observed))

    buckets: list[CalibrationBucket] = []
    ece = 0.0
    for index, bucket_points in enumerate(grouped):
        sample_count = len(bucket_points)
        mean_prediction = _mean_or_none(point[0] for point in bucket_points)
        observed_frequency = _mean_or_none(point[1] for point in bucket_points)
        if mean_prediction is not None and observed_frequency is not None and points:
            ece += sample_count / len(points) * abs(mean_prediction - observed_frequency)
        buckets.append(
            CalibrationBucket(
                lower_bound=index / bucket_count,
                upper_bound=(index + 1) / bucket_count,
                sample_count=sample_count,
                mean_prediction=mean_prediction,
                observed_frequency=observed_frequency,
            )
        )
    return CalibrationReport(
        basis=basis,
        ece=(
            MetricValue(value=ece, denominator=len(points))
            if calculate_ece and points
            else (MetricValue(denominator=0) if calculate_ece else None)
        ),
        buckets=tuple(buckets),
    )


def _automation_metrics(
    *,
    total_cases: int,
    act_cases: int,
    act_scored_cases: int,
    act_correct_cases: int,
    act_unscored_cases: int,
    review_cases: int,
    fallback_cases: int,
    provider_failures: int,
) -> AutomationMetrics:
    act_subset_accuracy = _ratio_metric(act_correct_cases, act_scored_cases)
    return AutomationMetrics(
        act_coverage=_ratio_metric(act_cases, total_cases),
        act_subset_accuracy=act_subset_accuracy,
        selective_risk=(
            MetricValue(value=1.0 - act_subset_accuracy.value, denominator=act_scored_cases)
            if act_subset_accuracy.value is not None
            else MetricValue(denominator=0)
        ),
        review_rate=_ratio_metric(review_cases, total_cases),
        fallback_rate=_ratio_metric(fallback_cases, total_cases),
        provider_error_rate=_ratio_metric(provider_failures, total_cases),
        act_unscored_cases=act_unscored_cases,
    )


def _percentile_metric(values: list[int], quantile: float) -> MetricValue:
    if not values:
        return MetricValue(denominator=0)
    sorted_values = sorted(values)
    index = ceil(quantile * len(sorted_values)) - 1
    return MetricValue(value=float(sorted_values[index]), denominator=len(sorted_values))


def _mean_metric(values: Iterable[float], denominator: int) -> MetricValue:
    values_list = list(values)
    if denominator == 0:
        return MetricValue(denominator=0)
    return MetricValue(value=sum(values_list) / denominator, denominator=denominator)


def _mean_or_none(values: Iterable[float]) -> float | None:
    values_list = list(values)
    return sum(values_list) / len(values_list) if values_list else None


def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _ratio_metric(numerator: int, denominator: int) -> MetricValue:
    value = _ratio(numerator, denominator)
    return MetricValue(value=value, denominator=denominator)
