"""Shared safe mapping for TypeSafe-shaped System One provider responses."""

from __future__ import annotations

from math import isfinite
from typing import Any, cast

import typesafe_sdk as typesafe
from pydantic import ValidationError

from decisionops.contracts.schema import ChoiceQuestion, NoulQuestion, ScoreQuestion
from decisionops.models import (
    ChoiceAnswer,
    DecisionAnswer,
    NoulAnswer,
    ProviderFailure,
    ProviderFailureKind,
    ProviderMetadata,
    ProviderResult,
    ScoreAnswer,
)
from decisionops.providers.request import ProviderRequest


def build_system_one_questions(
    request: ProviderRequest,
) -> dict[str, typesafe.Noul | typesafe.Choice | typesafe.Score]:
    """Map one validated contract into the official typed question primitives."""

    questions: dict[str, typesafe.Noul | typesafe.Choice | typesafe.Score] = {}
    for question_id, question in request.contract.questions.items():
        if isinstance(question, NoulQuestion):
            questions[question_id] = typesafe.Noul(
                instructions=question.instructions,
                criteria=cast(Any, question.criteria),
            )
        elif isinstance(question, ChoiceQuestion):
            questions[question_id] = typesafe.Choice(
                instructions=question.instructions,
                criteria=question.criteria,
            )
        elif isinstance(question, ScoreQuestion):
            questions[question_id] = typesafe.Score(
                instructions=question.instructions,
                criteria=question.criteria,
            )
    return questions


def map_system_one_response(
    request: ProviderRequest,
    response: typesafe.SystemOneResponse,
    *,
    provider: str,
    requested_model: str,
    sdk_version: str,
    latency_ms: int,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
) -> ProviderResult | ProviderFailure:
    """Map supported typed answers without retaining raw response/debug data."""

    answers: list[DecisionAnswer] = []
    for question_id in request.contract.questions:
        answer = response.answers.get(question_id)
        if answer is None:
            return ProviderFailure(
                provider=provider,
                kind=ProviderFailureKind.INVALID_RESPONSE,
                message=f"provider response is missing required answer {question_id!r}",
            )
        if isinstance(answer, typesafe.NoulAnswer):
            answers.append(NoulAnswer(question_id=question_id, noul=answer.noul))
        elif isinstance(answer, typesafe.ChoiceAnswer):
            answers.append(
                ChoiceAnswer(
                    question_id=question_id,
                    choice=answer.choice,
                    probabilities=answer.probabilities,
                    confidence=answer.confidence,
                )
            )
        elif isinstance(answer, typesafe.ScoreAnswer):
            answers.append(
                ScoreAnswer(
                    question_id=question_id,
                    score=answer.score,
                    legend=cast(dict[int, Any], answer.legend),
                    probabilities=answer.probabilities,
                    confidence=answer.confidence,
                )
            )
        else:
            return ProviderFailure(
                provider=provider,
                kind=ProviderFailureKind.INVALID_RESPONSE,
                message=f"provider returned unsupported answer for {question_id!r}",
            )

    try:
        return ProviderResult(
            metadata=ProviderMetadata(
                provider=provider,
                requested_model=requested_model,
                resolved_model=response.model,
                sdk_version=sdk_version,
                request_id=response_request_id(response),
                input_tokens=input_tokens
                if input_tokens is not None
                else response.usage.input_tokens,
                output_tokens=(
                    output_tokens if output_tokens is not None else response.usage.output_tokens
                ),
            ),
            answers=tuple(answers),
            latency_ms=latency_ms,
        )
    except ValidationError:
        return ProviderFailure(
            provider=provider,
            kind=ProviderFailureKind.INVALID_RESPONSE,
            message="provider returned an invalid typed answer",
        )


def response_request_id(response: typesafe.SystemOneResponse) -> str | None:
    """Read only a request ID when the TypeSafe-shaped response safely supplies it."""

    try:
        return response.request_id
    except typesafe.TypeSafeError:
        return None


def safe_usage_total(response: typesafe.SystemOneResponse, field: str) -> int | None:
    """Return safe cumulative adapter usage, falling back to no value when absent."""

    value = getattr(response.usage, field, None)
    return value if isinstance(value, int) and value >= 0 else None


def safe_response_latency_ms(response: typesafe.SystemOneResponse) -> int:
    """Convert a finite adapter latency in seconds to non-negative integer milliseconds."""

    latency = getattr(response.usage, "latency", None)
    if not isinstance(latency, int | float) or not isfinite(latency) or latency < 0.0:
        return 0
    return max(0, round(latency * 1000))
