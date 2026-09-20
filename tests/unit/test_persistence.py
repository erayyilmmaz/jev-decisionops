from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Table
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from decisionops.contracts import load_contract
from decisionops.contracts.schema import ValidatedContract
from decisionops.models import (
    NoulAnswer,
    PolicyEvaluation,
    PolicyOutcome,
    PolicyPredicateEvaluation,
    ProviderFailure,
    ProviderFailureKind,
    ProviderMetadata,
    ProviderResult,
    RuleEvaluation,
)
from decisionops.persistence import record_execution
from decisionops.persistence.database import Base
from decisionops.persistence.models import (
    DecisionAnswerRecord,
    DecisionContractRecord,
    PolicyOutcomeRecord,
)


class FakeAuditSession:
    """Minimal in-memory session double for write-boundary unit tests."""

    def __init__(self) -> None:
        self.records: list[object] = []

    async def get(self, _: type[object], __: UUID) -> None:
        return None

    async def scalar(self, _: object) -> None:
        return None

    def add(self, record: object) -> None:
        if isinstance(record, DecisionContractRecord) and record.id is None:
            record.id = uuid4()
        self.records.append(record)

    async def flush(self) -> None:
        return None


def validated_contract() -> ValidatedContract:
    return load_contract(Path("contracts/support-ticket-triage.yaml"))


def provider_result() -> ProviderResult:
    return ProviderResult(
        metadata=ProviderMetadata(
            provider="typesafe_jev",
            requested_model="jev-latest",
            resolved_model="jev-2026-09-20",
            sdk_version="0.7.0",
            request_id="request-safe-123",
            input_tokens=12,
            output_tokens=34,
        ),
        answers=(NoulAnswer(question_id="unauthorized_activity", noul=0.94),),
        latency_ms=56,
    )


def policy_evaluation() -> PolicyEvaluation:
    return PolicyEvaluation(
        outcome=PolicyOutcome.REVIEW,
        matched_rule_id="suspicious-activity-review",
        rule_evaluations=(
            RuleEvaluation(
                rule_id="suspicious-activity-review",
                question_id="unauthorized_activity",
                predicates=(
                    PolicyPredicateEvaluation(
                        predicate="probability_gte",
                        observed=0.94,
                        expected=0.90,
                        matched=True,
                    ),
                ),
                matched=True,
            ),
        ),
    )


def test_audit_schema_has_required_tables_and_postgresql_jsonb_columns() -> None:
    assert {
        "decision_contracts",
        "decision_runs",
        "decision_answers",
        "policy_outcomes",
    } <= set(Base.metadata.tables)
    assert isinstance(DecisionAnswerRecord.__table__.c.answer.type, JSONB)
    assert isinstance(PolicyOutcomeRecord.__table__.c.trace.type, JSONB)
    contract_table = cast(Table, DecisionContractRecord.__table__)
    answer_table = cast(Table, DecisionAnswerRecord.__table__)
    assert "uq_decision_contracts_fingerprint" in {
        constraint.name for constraint in contract_table.constraints
    }
    assert "uq_decision_answers_run_question" in {
        constraint.name for constraint in answer_table.constraints
    }


@pytest.mark.anyio
async def test_successful_execution_persists_contract_answers_and_policy_trace() -> None:
    session = FakeAuditSession()
    started_at = datetime(2026, 9, 20, tzinfo=UTC)
    run_id = uuid4()

    run = await record_execution(
        cast(AsyncSession, session),
        run_id=run_id,
        contract=validated_contract(),
        started_at=started_at,
        completed_at=started_at + timedelta(milliseconds=56),
        result=provider_result(),
        policy=policy_evaluation(),
    )

    assert run.id == run_id
    assert run.status == "succeeded"
    assert run.contract_id is not None
    assert (
        len([record for record in session.records if isinstance(record, DecisionContractRecord)])
        == 1
    )
    answers = [record for record in session.records if isinstance(record, DecisionAnswerRecord)]
    assert len(answers) == 1
    assert answers[0].answer == {
        "kind": "noul",
        "noul": 0.94,
        "question_id": "unauthorized_activity",
    }
    outcomes = [record for record in session.records if isinstance(record, PolicyOutcomeRecord)]
    assert outcomes[0].outcome == "review"
    assert outcomes[0].trace == [
        {
            "matched": True,
            "predicates": [
                {
                    "expected": 0.9,
                    "matched": True,
                    "observed": 0.94,
                    "predicate": "probability_gte",
                }
            ],
            "question_id": "unauthorized_activity",
            "rule_id": "suspicious-activity-review",
        }
    ]


@pytest.mark.anyio
async def test_failed_execution_persists_no_answer_or_policy_outcome() -> None:
    session = FakeAuditSession()
    now = datetime(2026, 9, 20, tzinfo=UTC)

    run = await record_execution(
        cast(AsyncSession, session),
        run_id=uuid4(),
        contract=validated_contract(),
        started_at=now,
        completed_at=now,
        failure=ProviderFailure(
            kind=ProviderFailureKind.TIMEOUT,
            message="TypeSafe request timed out",
            request_id="request-safe-456",
        ),
    )

    assert run.status == "failed"
    assert run.failure_kind == "timeout"
    assert not any(isinstance(record, DecisionAnswerRecord) for record in session.records)
    assert not any(isinstance(record, PolicyOutcomeRecord) for record in session.records)


@pytest.mark.anyio
async def test_execution_requires_exactly_one_provider_terminal_state() -> None:
    now = datetime(2026, 9, 20, tzinfo=UTC)

    with pytest.raises(ValueError, match="exactly one"):
        await record_execution(
            cast(AsyncSession, FakeAuditSession()),
            run_id=uuid4(),
            contract=validated_contract(),
            started_at=now,
            completed_at=now,
        )
