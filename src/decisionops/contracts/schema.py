"""Versioned declarative Decision Contract schema and cross-field validation."""

from __future__ import annotations

import json
import re
from hashlib import sha256
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from decisionops.models import PolicyOutcome, QuestionKind

CONTRACT_VERSION = 1
QUESTION_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
CONTRACT_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9-]{0,63}$")


class ContractModel(BaseModel):
    """Strict immutable model for external contract data."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class NoulQuestion(ContractModel):
    type: Literal[QuestionKind.NOUL] = QuestionKind.NOUL
    instructions: str = Field(min_length=1)
    criteria: dict[Literal["true", "false"], str] | None = None


class ChoiceQuestion(ContractModel):
    type: Literal[QuestionKind.CHOICE] = QuestionKind.CHOICE
    instructions: str = Field(min_length=1)
    criteria: dict[str, str] = Field(min_length=2)

    @field_validator("criteria")
    @classmethod
    def validate_criteria(cls, criteria: dict[str, str]) -> dict[str, str]:
        if any(
            not label.strip() or not description.strip() for label, description in criteria.items()
        ):
            raise ValueError("choice criteria labels and descriptions must be non-empty")
        return criteria


class ScoreQuestion(ContractModel):
    type: Literal[QuestionKind.SCORE] = QuestionKind.SCORE
    instructions: str = Field(min_length=1)
    criteria: tuple[str, ...] = Field(min_length=2)

    @field_validator("criteria")
    @classmethod
    def validate_criteria(cls, criteria: tuple[str, ...]) -> tuple[str, ...]:
        if any(not level.strip() for level in criteria):
            raise ValueError("score criteria levels must be non-empty")
        if len(set(criteria)) != len(criteria):
            raise ValueError("score criteria levels must be unique and ordered")
        return criteria


type ContractQuestion = Annotated[
    NoulQuestion | ChoiceQuestion | ScoreQuestion,
    Field(discriminator="type"),
]


class PolicyCondition(ContractModel):
    question: str = Field(min_length=1, max_length=64)
    equals: str | None = None
    not_equals: str | None = None
    probability_gte: float | None = None
    probability_lt: float | None = None
    confidence_gte: float | None = None
    confidence_lt: float | None = None
    score_gte: float | None = None
    score_lt: float | None = None

    @model_validator(mode="after")
    def validate_predicates(self) -> PolicyCondition:
        predicates = (
            self.equals,
            self.not_equals,
            self.probability_gte,
            self.probability_lt,
            self.confidence_gte,
            self.confidence_lt,
            self.score_gte,
            self.score_lt,
        )
        if not any(value is not None for value in predicates):
            raise ValueError("at least one policy predicate is required")
        if self.equals is not None and self.not_equals is not None:
            raise ValueError("equals and not_equals cannot be used together")
        self._validate_probability_range("probability", self.probability_gte, self.probability_lt)
        self._validate_probability_range("confidence", self.confidence_gte, self.confidence_lt)
        self._validate_score_range()
        return self

    @staticmethod
    def _validate_probability_range(name: str, lower: float | None, upper: float | None) -> None:
        for value in (lower, upper):
            if value is not None and not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} thresholds must be within [0, 1]")
        if lower is not None and upper is not None and lower >= upper:
            raise ValueError(f"{name}_gte must be less than {name}_lt")

    def _validate_score_range(self) -> None:
        if (
            self.score_gte is not None
            and self.score_lt is not None
            and self.score_gte >= self.score_lt
        ):
            raise ValueError("score_gte must be less than score_lt")


class PolicyRule(ContractModel):
    id: str = Field(min_length=1, max_length=64)
    when: PolicyCondition
    outcome: PolicyOutcome


class ContractPolicy(ContractModel):
    rules: tuple[PolicyRule, ...] = Field(min_length=1)
    default: PolicyOutcome


class DecisionContract(ContractModel):
    """Validated V1 contract; its JSON serialization is canonical input to hashing."""

    version: Literal[1]
    name: str = Field(min_length=1, max_length=64)
    questions: dict[str, ContractQuestion] = Field(min_length=1)
    policy: ContractPolicy

    @field_validator("name")
    @classmethod
    def validate_name(cls, name: str) -> str:
        if not CONTRACT_NAME_PATTERN.fullmatch(name):
            raise ValueError("name must use lowercase letters, digits, and hyphens")
        return name

    @field_validator("questions")
    @classmethod
    def validate_question_ids(
        cls,
        questions: dict[str, ContractQuestion],
    ) -> dict[str, ContractQuestion]:
        for question_id in questions:
            if not QUESTION_ID_PATTERN.fullmatch(question_id):
                raise ValueError("question IDs must use lowercase letters, digits, and underscores")
        return questions

    @model_validator(mode="after")
    def validate_policy_references(self) -> DecisionContract:
        seen_conditions: set[str] = set()
        for rule in self.policy.rules:
            question = self.questions.get(rule.when.question)
            if question is None:
                raise ValueError(f"policy references unknown question {rule.when.question!r}")
            self._validate_question_predicates(
                question_id=rule.when.question, question=question, rule=rule
            )

            serialized_condition = json.dumps(
                rule.when.model_dump(mode="json", exclude_none=True),
                sort_keys=True,
                separators=(",", ":"),
            )
            if serialized_condition in seen_conditions:
                raise ValueError("policy contains duplicate conditions")
            seen_conditions.add(serialized_condition)
        return self

    @staticmethod
    def _validate_question_predicates(
        question_id: str,
        question: ContractQuestion,
        rule: PolicyRule,
    ) -> None:
        condition = rule.when
        has_probability = (
            condition.probability_gte is not None or condition.probability_lt is not None
        )
        has_confidence = condition.confidence_gte is not None or condition.confidence_lt is not None
        has_score = condition.score_gte is not None or condition.score_lt is not None
        has_choice_match = condition.equals is not None or condition.not_equals is not None

        if isinstance(question, NoulQuestion):
            if has_confidence or has_score or has_choice_match:
                raise ValueError(
                    f"Noul question {question_id!r} supports only probability predicates"
                )
        elif isinstance(question, ChoiceQuestion):
            if has_probability or has_score:
                raise ValueError(
                    f"Choice question {question_id!r} does not support "
                    "probability or score predicates"
                )
            for label in (condition.equals, condition.not_equals):
                if label is not None and label not in question.criteria:
                    raise ValueError(f"unknown Choice label {label!r} for question {question_id!r}")
        elif isinstance(question, ScoreQuestion):
            if has_probability or has_choice_match:
                raise ValueError(
                    f"Score question {question_id!r} does not support "
                    "probability or Choice predicates"
                )


class ValidatedContract(ContractModel):
    """Contract plus its stable canonical representation and fingerprint."""

    contract: DecisionContract
    canonical_json: str
    fingerprint: str = Field(min_length=64, max_length=64)


def canonical_json(contract: DecisionContract) -> str:
    """Return a whitespace-independent canonical JSON representation."""

    return json.dumps(
        contract.model_dump(mode="json", by_alias=True, exclude_none=True),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def fingerprint_contract(contract: DecisionContract) -> str:
    """Return the SHA-256 fingerprint of the canonical representation."""

    return sha256(canonical_json(contract).encode("utf-8")).hexdigest()
