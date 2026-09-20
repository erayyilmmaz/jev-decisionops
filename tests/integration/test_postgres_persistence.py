"""PostgreSQL persistence verification, skipped outside an explicit integration environment."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select

from decisionops.contracts import load_contract
from decisionops.models import NoulAnswer, ProviderMetadata, ProviderResult
from decisionops.persistence import DecisionAnswerRecord, DecisionRunRecord, record_execution
from decisionops.persistence.database import create_database_engine, create_session_factory

pytestmark = pytest.mark.integration


@pytest.mark.anyio
async def test_migrated_postgresql_persists_an_audit_ready_execution() -> None:
    database_url = os.environ.get("DECISIONOPS_INTEGRATION_DATABASE_URL")
    if database_url is None:
        pytest.skip("DECISIONOPS_INTEGRATION_DATABASE_URL is not configured")

    engine = create_database_engine(database_url)
    sessions = create_session_factory(engine)
    run_id = uuid4()
    now = datetime.now(UTC)
    try:
        async with sessions.begin() as session:
            await record_execution(
                session,
                run_id=run_id,
                contract=load_contract(Path("contracts/support-ticket-triage.yaml")),
                started_at=now,
                completed_at=now,
                result=ProviderResult(
                    metadata=ProviderMetadata(provider="postgres-integration-fixture"),
                    latency_ms=1,
                    answers=(NoulAnswer(question_id="unauthorized_activity", noul=0.5),),
                ),
            )

        async with sessions() as session:
            run = await session.get(DecisionRunRecord, run_id)
            answer = await session.scalar(
                select(DecisionAnswerRecord).where(DecisionAnswerRecord.run_id == run_id)
            )
        assert run is not None
        assert run.status == "succeeded"
        assert answer is not None
        assert answer.answer == {
            "kind": "noul",
            "noul": 0.5,
            "question_id": "unauthorized_activity",
        }
    finally:
        await engine.dispose()
