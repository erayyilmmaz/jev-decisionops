"""Deterministic, side-effect-free policy evaluation for typed Jev answers."""

from __future__ import annotations

from decisionops.contracts.schema import (
    ChoiceQuestion,
    DecisionContract,
    NoulQuestion,
    PolicyCondition,
    PolicyRule,
    ScoreQuestion,
)
from decisionops.models import (
    ChoiceAnswer,
    DecisionAnswer,
    NoulAnswer,
    PolicyEvaluation,
    PolicyPredicateEvaluation,
    ProviderResult,
    RuleEvaluation,
    ScoreAnswer,
)
from decisionops.policy.errors import PolicyEvaluationError


class PolicyEngine:
    """Evaluate ordered contract rules without calling a provider or mutating its result."""

    def evaluate(self, contract: DecisionContract, result: ProviderResult) -> PolicyEvaluation:
        """Return the first matching rule's outcome, otherwise the contract default."""

        answers = _answers_by_question_id(result.answers)
        evaluations: list[RuleEvaluation] = []

        for rule in contract.policy.rules:
            question = contract.questions.get(rule.when.question)
            if question is None:
                raise PolicyEvaluationError(
                    f"policy rule {rule.id!r} references unknown question {rule.when.question!r}"
                )
            answer = answers.get(rule.when.question)
            if answer is None:
                raise PolicyEvaluationError(
                    f"provider result is missing answer for policy question {rule.when.question!r}"
                )

            evaluation = _evaluate_rule(rule, question, answer)
            evaluations.append(evaluation)
            if evaluation.matched:
                return PolicyEvaluation(
                    outcome=rule.outcome,
                    matched_rule_id=rule.id,
                    rule_evaluations=tuple(evaluations),
                )

        return PolicyEvaluation(
            outcome=contract.policy.default,
            matched_rule_id=None,
            rule_evaluations=tuple(evaluations),
        )


def evaluate_policy(contract: DecisionContract, result: ProviderResult) -> PolicyEvaluation:
    """Convenience function for application services that do not need engine injection."""

    return PolicyEngine().evaluate(contract, result)


def _answers_by_question_id(answers: tuple[DecisionAnswer, ...]) -> dict[str, DecisionAnswer]:
    indexed: dict[str, DecisionAnswer] = {}
    for answer in answers:
        if answer.question_id in indexed:
            raise PolicyEvaluationError(
                f"provider result contains duplicate answer {answer.question_id!r}"
            )
        indexed[answer.question_id] = answer
    return indexed


def _evaluate_rule(
    rule: PolicyRule,
    question: NoulQuestion | ChoiceQuestion | ScoreQuestion,
    answer: DecisionAnswer,
) -> RuleEvaluation:
    if isinstance(question, NoulQuestion) and isinstance(answer, NoulAnswer):
        predicates = _evaluate_noul(rule.when, answer)
    elif isinstance(question, ChoiceQuestion) and isinstance(answer, ChoiceAnswer):
        predicates = _evaluate_choice(rule.when, answer)
    elif isinstance(question, ScoreQuestion) and isinstance(answer, ScoreAnswer):
        predicates = _evaluate_score(rule.when, answer)
    else:
        raise PolicyEvaluationError(
            f"provider answer type does not match contract question {rule.when.question!r}"
        )

    return RuleEvaluation(
        rule_id=rule.id,
        question_id=rule.when.question,
        predicates=tuple(predicates),
        matched=all(predicate.matched for predicate in predicates),
    )


def _evaluate_noul(
    condition: PolicyCondition,
    answer: NoulAnswer,
) -> list[PolicyPredicateEvaluation]:
    predicates: list[PolicyPredicateEvaluation] = []
    if condition.probability_gte is not None:
        predicates.append(
            _comparison(
                "probability_gte",
                answer.noul,
                condition.probability_gte,
                answer.noul >= condition.probability_gte,
            )
        )
    if condition.probability_lt is not None:
        predicates.append(
            _comparison(
                "probability_lt",
                answer.noul,
                condition.probability_lt,
                answer.noul < condition.probability_lt,
            )
        )
    return _require_predicates(condition.question, predicates)


def _evaluate_choice(
    condition: PolicyCondition,
    answer: ChoiceAnswer,
) -> list[PolicyPredicateEvaluation]:
    predicates: list[PolicyPredicateEvaluation] = []
    if condition.equals is not None:
        predicates.append(
            _comparison(
                "equals", answer.choice, condition.equals, answer.choice == condition.equals
            )
        )
    if condition.not_equals is not None:
        predicates.append(
            _comparison(
                "not_equals",
                answer.choice,
                condition.not_equals,
                answer.choice != condition.not_equals,
            )
        )
    if condition.confidence_gte is not None:
        predicates.append(
            _comparison(
                "confidence_gte",
                answer.confidence,
                condition.confidence_gte,
                answer.confidence >= condition.confidence_gte,
            )
        )
    if condition.confidence_lt is not None:
        predicates.append(
            _comparison(
                "confidence_lt",
                answer.confidence,
                condition.confidence_lt,
                answer.confidence < condition.confidence_lt,
            )
        )
    return _require_predicates(condition.question, predicates)


def _evaluate_score(
    condition: PolicyCondition,
    answer: ScoreAnswer,
) -> list[PolicyPredicateEvaluation]:
    predicates: list[PolicyPredicateEvaluation] = []
    if condition.score_gte is not None:
        predicates.append(
            _comparison(
                "score_gte", answer.score, condition.score_gte, answer.score >= condition.score_gte
            )
        )
    if condition.score_lt is not None:
        predicates.append(
            _comparison(
                "score_lt", answer.score, condition.score_lt, answer.score < condition.score_lt
            )
        )
    if condition.confidence_gte is not None:
        predicates.append(
            _comparison(
                "confidence_gte",
                answer.confidence,
                condition.confidence_gte,
                answer.confidence >= condition.confidence_gte,
            )
        )
    if condition.confidence_lt is not None:
        predicates.append(
            _comparison(
                "confidence_lt",
                answer.confidence,
                condition.confidence_lt,
                answer.confidence < condition.confidence_lt,
            )
        )
    return _require_predicates(condition.question, predicates)


def _comparison(
    predicate: str,
    observed: str | float,
    expected: str | float,
    matched: bool,
) -> PolicyPredicateEvaluation:
    return PolicyPredicateEvaluation(
        predicate=predicate,
        observed=observed,
        expected=expected,
        matched=matched,
    )


def _require_predicates(
    question_id: str,
    predicates: list[PolicyPredicateEvaluation],
) -> list[PolicyPredicateEvaluation]:
    if not predicates:
        raise PolicyEvaluationError(f"policy question {question_id!r} has no executable predicates")
    return predicates
