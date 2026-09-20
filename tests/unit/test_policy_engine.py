from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from decisionops.contracts import load_contract, validate_contract_data
from decisionops.contracts.schema import DecisionContract
from decisionops.models import (
    ChoiceAnswer,
    NoulAnswer,
    ProviderMetadata,
    ProviderResult,
    ScoreAnswer,
)
from decisionops.policy import PolicyEngine, PolicyEvaluationError


def provider_result(
    *,
    unauthorized_probability: float,
    intent: str = "support",
    intent_confidence: float = 0.50,
    urgency_score: float = 1.0,
    urgency_confidence: float = 0.50,
) -> ProviderResult:
    return ProviderResult(
        metadata=ProviderMetadata(provider="fake"),
        latency_ms=1,
        answers=(
            ChoiceAnswer(
                question_id="intent",
                choice=intent,
                probabilities={"refund": 0.25, "support": 0.75},
                confidence=intent_confidence,
            ),
            NoulAnswer(question_id="unauthorized_activity", noul=unauthorized_probability),
            ScoreAnswer(
                question_id="urgency",
                score=urgency_score,
                legend={0: "low", 1: "medium", 2: "high", 3: "critical"},
                probabilities={0: 0.25, 1: 0.25, 2: 0.25, 3: 0.25},
                confidence=urgency_confidence,
            ),
        ),
    )


def example_contract() -> DecisionContract:
    return load_contract(Path("contracts/support-ticket-triage.yaml")).contract


def test_noul_probability_rule_returns_review_with_trace() -> None:
    decision = PolicyEngine().evaluate(
        example_contract(),
        provider_result(unauthorized_probability=0.94),
    )

    assert decision.outcome.value == "review"
    assert decision.matched_rule_id == "suspicious-activity-review"
    assert decision.rule_evaluations[0].predicates[0].observed == 0.94
    assert decision.rule_evaluations[0].predicates[0].expected == 0.90


def test_threshold_below_boundary_does_not_match_and_uses_default() -> None:
    decision = PolicyEngine().evaluate(
        example_contract(),
        provider_result(unauthorized_probability=0.89),
    )

    assert decision.outcome.value == "fallback"
    assert decision.matched_rule_id is None
    assert decision.rule_evaluations[0].matched is False


def test_choice_label_and_confidence_must_both_match() -> None:
    decision = PolicyEngine().evaluate(
        example_contract(),
        provider_result(
            unauthorized_probability=0.01,
            intent="refund",
            intent_confidence=0.95,
        ),
    )

    assert decision.outcome.value == "act"
    assert decision.matched_rule_id == "high-confidence-refund"
    assert len(decision.rule_evaluations[1].predicates) == 2


def test_policy_uses_first_matching_rule_in_declaration_order() -> None:
    data = {
        "version": 1,
        "name": "rule-order",
        "questions": {
            "signal": {"type": "noul", "instructions": "Is the signal present?"},
        },
        "policy": {
            "rules": [
                {
                    "id": "review-first",
                    "when": {"question": "signal", "probability_gte": 0.90},
                    "outcome": "review",
                },
                {
                    "id": "act-second",
                    "when": {"question": "signal", "probability_gte": 0.80},
                    "outcome": "act",
                },
            ],
            "default": "fallback",
        },
    }
    contract = validate_contract_data(data).contract
    result = ProviderResult(
        metadata=ProviderMetadata(provider="fake"),
        latency_ms=1,
        answers=(NoulAnswer(question_id="signal", noul=0.95),),
    )

    decision = PolicyEngine().evaluate(contract, result)

    assert decision.outcome.value == "review"
    assert decision.matched_rule_id == "review-first"
    assert len(decision.rule_evaluations) == 1


def test_score_range_and_confidence_predicates_are_deterministic() -> None:
    data = {
        "version": 1,
        "name": "score-policy",
        "questions": {
            "urgency": {
                "type": "score",
                "instructions": "How urgent is this?",
                "criteria": ["low", "high"],
            },
        },
        "policy": {
            "rules": [
                {
                    "when": {
                        "question": "urgency",
                        "score_gte": 1.5,
                        "confidence_gte": 0.9,
                    },
                    "outcome": "review",
                },
            ],
            "default": "fallback",
        },
    }
    contract = validate_contract_data(data).contract
    result = ProviderResult(
        metadata=ProviderMetadata(provider="fake"),
        latency_ms=1,
        answers=(
            ScoreAnswer(
                question_id="urgency",
                score=1.5,
                legend={0: "low", 1: "high"},
                probabilities={0: 0.25, 1: 0.75},
                confidence=0.9,
            ),
        ),
    )

    assert PolicyEngine().evaluate(contract, result).outcome.value == "review"


def test_policy_does_not_mutate_provider_result() -> None:
    result = provider_result(unauthorized_probability=0.94)
    before = deepcopy(result)

    PolicyEngine().evaluate(example_contract(), result)

    assert result == before


def test_missing_policy_answer_is_an_error_not_a_fallback() -> None:
    result = ProviderResult(
        metadata=ProviderMetadata(provider="fake"),
        latency_ms=1,
        answers=(NoulAnswer(question_id="unauthorized_activity", noul=0.10),),
    )

    with pytest.raises(PolicyEvaluationError, match="missing answer"):
        PolicyEngine().evaluate(example_contract(), result)
