"""Create decision audit records.

Revision ID: 20260920_01
Revises:
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260920_01"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "decision_contracts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("canonical_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("fingerprint", name="uq_decision_contracts_fingerprint"),
    )
    op.create_table(
        "decision_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("contract_id", sa.Uuid(), sa.ForeignKey("decision_contracts.id"), nullable=False),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("requested_model", sa.String(256)),
        sa.Column("resolved_model", sa.String(256)),
        sa.Column("sdk_version", sa.String(64)),
        sa.Column("request_id", sa.String(256)),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("failure_kind", sa.String(64)),
        sa.Column("failure_message", sa.String(512)),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("latency_ms", sa.Integer()),
        sa.Column("input_tokens", sa.Integer()),
        sa.Column("output_tokens", sa.Integer()),
    )
    op.create_table(
        "decision_answers",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("run_id", sa.Uuid(), sa.ForeignKey("decision_runs.id"), nullable=False),
        sa.Column("question_id", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("answer", postgresql.JSONB(), nullable=False),
        sa.UniqueConstraint("run_id", "question_id", name="uq_decision_answers_run_question"),
    )
    op.create_table(
        "policy_outcomes",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "run_id", sa.Uuid(), sa.ForeignKey("decision_runs.id"), nullable=False, unique=True
        ),
        sa.Column("outcome", sa.String(32), nullable=False),
        sa.Column("matched_rule_id", sa.String(64)),
        sa.Column("trace", postgresql.JSONB(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("policy_outcomes")
    op.drop_table("decision_answers")
    op.drop_table("decision_runs")
    op.drop_table("decision_contracts")
