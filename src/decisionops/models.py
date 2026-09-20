"""Provider-independent domain models shared by every delivery surface."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

type JsonPrimitive = str | int | float | bool | None
type JsonValue = JsonPrimitive | list[JsonValue] | dict[str, JsonValue]


class QuestionKind(StrEnum):
    """The only V0 typed question shapes."""

    NOUL = "noul"
    CHOICE = "choice"
    SCORE = "score"


class PolicyOutcome(StrEnum):
    """Deterministic dispositions, available only for valid provider results."""

    ACT = "act"
    REVIEW = "review"
    FALLBACK = "fallback"


class RegressionOutcome(StrEnum):
    """Comparison result for a compatible baseline/current evaluation pair."""

    PASS = "pass"
    FAIL = "fail"
    INCOMPARABLE = "incomparable"


class ProviderFailureKind(StrEnum):
    """Execution failures kept distinct from decision-quality failures."""

    AUTHENTICATION = "authentication"
    RATE_LIMITED = "rate_limited"
    TIMEOUT = "timeout"
    TRANSPORT = "transport"
    INVALID_RESPONSE = "invalid_response"
    UNKNOWN = "unknown"


class DomainModel(BaseModel):
    """Strict base model that rejects accidental provider-specific fields."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class DecisionContractReference(DomainModel):
    """Immutable identity of a validated contract, not the contract schema itself."""

    name: str = Field(min_length=1, max_length=128)
    version: int = Field(ge=1)
    fingerprint: str = Field(min_length=64, max_length=64)


class QuestionDefinition(DomainModel):
    """Shared question metadata; contract-specific validation arrives in JDO-4."""

    question_id: str = Field(min_length=1, max_length=128)
    kind: QuestionKind
    instructions: str = Field(min_length=1)


class PolicyDefinition(DomainModel):
    """The contract policy's required safe disposition when no rule matches."""

    default_outcome: PolicyOutcome


class ProviderMetadata(DomainModel):
    """Safe, reproducibility-relevant metadata supplied by a provider adapter."""

    provider: str = Field(min_length=1, max_length=64)
    requested_model: str | None = Field(default=None, max_length=256)
    sdk_version: str | None = Field(default=None, max_length=64)
    request_id: str | None = Field(default=None, max_length=256)


class DecisionRequest(DomainModel):
    """A validated contract reference and caller state to evaluate."""

    contract: DecisionContractReference
    state: dict[str, JsonValue]
    correlation_id: str | None = Field(default=None, max_length=128)


class AnswerBase(DomainModel):
    """Base fields shared by all provider-independent typed answers."""

    question_id: str = Field(min_length=1, max_length=128)


class NoulAnswer(AnswerBase):
    """Probability that a clearly defined yes/no statement is true."""

    kind: Literal[QuestionKind.NOUL] = QuestionKind.NOUL
    noul: float

    @field_validator("noul")
    @classmethod
    def validate_noul(cls, value: float) -> float:
        if not 0.0 <= value <= 1.0:
            raise ValueError("noul must be within [0, 1]")
        return value


class ChoiceAnswer(AnswerBase):
    """One declared label, its full distribution, and provider confidence."""

    kind: Literal[QuestionKind.CHOICE] = QuestionKind.CHOICE
    choice: str = Field(min_length=1)
    probabilities: dict[str, float] = Field(min_length=1)
    confidence: float

    @field_validator("confidence")
    @classmethod
    def validate_confidence(cls, value: float) -> float:
        if not 0.0 <= value <= 1.0:
            raise ValueError("confidence must be within [0, 1]")
        return value


class ScoreAnswer(AnswerBase):
    """Position on an ordered legend, its distribution, and confidence."""

    kind: Literal[QuestionKind.SCORE] = QuestionKind.SCORE
    score: float
    legend: tuple[str, ...] = Field(min_length=1)
    probabilities: dict[str, float] = Field(min_length=1)
    confidence: float

    @field_validator("confidence")
    @classmethod
    def validate_confidence(cls, value: float) -> float:
        if not 0.0 <= value <= 1.0:
            raise ValueError("confidence must be within [0, 1]")
        return value


type DecisionAnswer = Annotated[
    NoulAnswer | ChoiceAnswer | ScoreAnswer,
    Field(discriminator="kind"),
]


class ProviderResult(DomainModel):
    """A successfully validated provider result, before policy evaluation."""

    metadata: ProviderMetadata
    answers: tuple[DecisionAnswer, ...]
    latency_ms: int = Field(ge=0)


class ProviderFailure(DomainModel):
    """Sanitized provider execution failure; it is never a policy outcome."""

    kind: ProviderFailureKind
    message: str = Field(min_length=1, max_length=512)
    request_id: str | None = Field(default=None, max_length=256)


class PolicyEvaluation(DomainModel):
    """Explainable deterministic policy result for a successful provider call."""

    outcome: PolicyOutcome
    matched_rule_id: str | None = Field(default=None, max_length=128)


class DecisionRun(DomainModel):
    """In-memory representation of a decision attempt for later persistence."""

    run_id: UUID
    contract: DecisionContractReference
    provider: ProviderMetadata
    started_at: datetime
    completed_at: datetime | None = None
    result: ProviderResult | None = None
    failure: ProviderFailure | None = None
    policy: PolicyEvaluation | None = None


class EvaluationCase(DomainModel):
    """Labelled synthetic evaluation case; dataset parsing arrives in JDO-8."""

    case_id: str = Field(min_length=1, max_length=128)
    state: dict[str, JsonValue]
    expected: dict[str, JsonValue]


class EvaluationRun(DomainModel):
    """Reproducibility identity for a future dataset evaluation execution."""

    evaluation_id: UUID
    contract_fingerprint: str = Field(min_length=64, max_length=64)
    dataset_fingerprint: str = Field(min_length=64, max_length=64)
    started_at: datetime


class MetricResult(DomainModel):
    """A metric with an explicit denominator for truthful small-sample reports."""

    name: str = Field(min_length=1, max_length=128)
    value: float | None = None
    denominator: int = Field(ge=0)


class RegressionResult(DomainModel):
    """Future baseline comparison output with an explicit compatibility state."""

    outcome: RegressionOutcome
    baseline_evaluation_id: UUID
    current_evaluation_id: UUID
