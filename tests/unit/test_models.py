import pytest
from pydantic import ValidationError

from decisionops.models import (
    ChoiceAnswer,
    NoulAnswer,
    PolicyOutcome,
    ProviderFailure,
    ProviderFailureKind,
)


def test_noul_requires_a_probability() -> None:
    with pytest.raises(ValidationError, match="noul must be within"):
        NoulAnswer(question_id="is_urgent", noul=1.01)


def test_choice_keeps_confidence_separate_from_distribution() -> None:
    answer = ChoiceAnswer(
        question_id="intent",
        choice="support",
        probabilities={"support": 0.80, "refund": 0.20},
        confidence=0.75,
    )

    assert answer.probabilities[answer.choice] == 0.80
    assert answer.confidence == 0.75


def test_provider_failure_is_not_a_policy_outcome() -> None:
    failure = ProviderFailure(kind=ProviderFailureKind.TIMEOUT, message="request timed out")

    assert failure.kind == ProviderFailureKind.TIMEOUT
    assert PolicyOutcome.FALLBACK.value != failure.kind.value
