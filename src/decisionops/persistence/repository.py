"""Transactional audit persistence for successful and failed decision executions."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from decisionops.contracts.schema import ValidatedContract
from decisionops.models import PolicyEvaluation, ProviderFailure, ProviderResult
from decisionops.persistence.models import (
    DecisionAnswerRecord,
    DecisionContractRecord,
    DecisionRunRecord,
    PolicyOutcomeRecord,
)


async def record_execution(
    session: AsyncSession,
    *,
    run_id: UUID,
    contract: ValidatedContract,
    started_at: datetime,
    completed_at: datetime,
    result: ProviderResult | None = None,
    failure: ProviderFailure | None = None,
    policy: PolicyEvaluation | None = None,
) -> DecisionRunRecord:
    """Persist one immutable execution; callers commit or roll back the transaction."""

    if (result is None) == (failure is None):
        raise ValueError("exactly one of result or failure is required")
    if policy is not None and result is None:
        raise ValueError("a policy outcome requires a successful provider result")

    existing_run = await session.get(DecisionRunRecord, run_id)
    if existing_run is not None:
        raise ValueError(f"decision run {run_id} already exists")

    contract_record = await session.scalar(
        select(DecisionContractRecord).where(
            DecisionContractRecord.fingerprint == contract.fingerprint
        )
    )
    if contract_record is None:
        contract_record = DecisionContractRecord(
            name=contract.contract.name,
            version=contract.contract.version,
            fingerprint=contract.fingerprint,
            canonical_json=contract.canonical_json,
            created_at=started_at,
        )
        session.add(contract_record)
        await session.flush()

    if result is not None:
        run = DecisionRunRecord(
            id=run_id,
            contract_id=contract_record.id,
            provider=result.metadata.provider,
            requested_model=result.metadata.requested_model,
            resolved_model=result.metadata.resolved_model,
            sdk_version=result.metadata.sdk_version,
            request_id=result.metadata.request_id,
            status="succeeded",
            started_at=started_at,
            completed_at=completed_at,
            latency_ms=result.latency_ms,
            input_tokens=result.metadata.input_tokens,
            output_tokens=result.metadata.output_tokens,
        )
        session.add(run)
        for answer in result.answers:
            session.add(
                DecisionAnswerRecord(
                    run_id=run_id,
                    question_id=answer.question_id,
                    kind=answer.kind.value,
                    answer=answer.model_dump(mode="json"),
                )
            )
        if policy is not None:
            session.add(
                PolicyOutcomeRecord(
                    run_id=run_id,
                    outcome=policy.outcome.value,
                    matched_rule_id=policy.matched_rule_id,
                    trace=[item.model_dump(mode="json") for item in policy.rule_evaluations],
                )
            )
    else:
        assert failure is not None
        run = DecisionRunRecord(
            id=run_id,
            contract_id=contract_record.id,
            provider="typesafe_jev",
            status="failed",
            failure_kind=failure.kind.value,
            failure_message=failure.message,
            request_id=failure.request_id,
            started_at=started_at,
            completed_at=completed_at,
        )
        session.add(run)

    await session.flush()
    return run
