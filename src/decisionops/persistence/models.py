"""Audit-ready SQLAlchemy entities; raw caller state is intentionally absent."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import Uuid

from decisionops.persistence.database import Base


class DecisionContractRecord(Base):
    __tablename__ = "decision_contracts"
    __table_args__ = (UniqueConstraint("fingerprint", name="uq_decision_contracts_fingerprint"),)

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    canonical_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    runs: Mapped[list[DecisionRunRecord]] = relationship(back_populates="contract")


class DecisionRunRecord(Base):
    __tablename__ = "decision_runs"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    contract_id: Mapped[UUID] = mapped_column(ForeignKey("decision_contracts.id"), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    requested_model: Mapped[str | None] = mapped_column(String(256))
    resolved_model: Mapped[str | None] = mapped_column(String(256))
    sdk_version: Mapped[str | None] = mapped_column(String(64))
    request_id: Mapped[str | None] = mapped_column(String(256))
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    failure_kind: Mapped[str | None] = mapped_column(String(64))
    failure_message: Mapped[str | None] = mapped_column(String(512))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    contract: Mapped[DecisionContractRecord] = relationship(back_populates="runs")
    answers: Mapped[list[DecisionAnswerRecord]] = relationship(back_populates="run")
    policy_outcome: Mapped[PolicyOutcomeRecord | None] = relationship(back_populates="run")


class DecisionAnswerRecord(Base):
    __tablename__ = "decision_answers"
    __table_args__ = (
        UniqueConstraint("run_id", "question_id", name="uq_decision_answers_run_question"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    run_id: Mapped[UUID] = mapped_column(ForeignKey("decision_runs.id"), nullable=False)
    question_id: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    answer: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    run: Mapped[DecisionRunRecord] = relationship(back_populates="answers")


class PolicyOutcomeRecord(Base):
    __tablename__ = "policy_outcomes"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    run_id: Mapped[UUID] = mapped_column(
        ForeignKey("decision_runs.id"), nullable=False, unique=True
    )
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    matched_rule_id: Mapped[str | None] = mapped_column(String(64))
    trace: Mapped[list[dict[str, object]]] = mapped_column(JSONB, nullable=False)
    run: Mapped[DecisionRunRecord] = relationship(back_populates="policy_outcome")
