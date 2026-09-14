"""Add bounded interpretation budgets, telemetry, and validated cache."""

import sqlalchemy as sa
from alembic import op

revision = "0003_interpretation"
down_revision = "0002_model_trace"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "interpretation_budget_counters",
        sa.Column("key", sa.String(160), primary_key=True),
        sa.Column("reserved_microdollars", sa.BigInteger(), nullable=False),
        sa.Column("committed_microdollars", sa.BigInteger(), nullable=False),
        sa.Column("reserved_tokens", sa.BigInteger(), nullable=False),
        sa.Column("committed_tokens", sa.BigInteger(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "interpretation_calls",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("session_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("execution_id", sa.String(100), nullable=False),
        sa.Column("mode", sa.String(20), nullable=False),
        sa.Column("requested_model", sa.String(100), nullable=False),
        sa.Column("response_model", sa.String(100)),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("reservation_microdollars", sa.BigInteger(), nullable=False),
        sa.Column("estimated_microdollars", sa.BigInteger()),
        sa.Column("input_tokens", sa.Integer()),
        sa.Column("output_tokens", sa.Integer()),
        sa.Column("cached_input_tokens", sa.Integer()),
        sa.Column("reasoning_tokens", sa.Integer()),
        sa.Column("latency_ms", sa.Integer()),
        sa.Column("provider_cache_hit", sa.Boolean(), nullable=False),
        sa.Column("error_code", sa.String(80)),
        sa.Column("reservation_retained", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
    )
    op.create_index(
        "ix_interpretation_calls_workspace_id", "interpretation_calls", ["workspace_id"]
    )
    op.create_index("ix_interpretation_calls_session_id", "interpretation_calls", ["session_id"])
    op.create_index(
        "ix_interpretation_calls_execution_id", "interpretation_calls", ["execution_id"]
    )
    op.create_index("ix_interpretation_calls_status", "interpretation_calls", ["status"])
    op.create_table(
        "interpretation_cache",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column(
            "workspace_id", sa.Uuid(as_uuid=True), sa.ForeignKey("workspaces.id"), nullable=False
        ),
        sa.Column("cache_key", sa.String(64), nullable=False),
        sa.Column("source_fingerprint", sa.String(64), nullable=False),
        sa.Column("requested_model", sa.String(100), nullable=False),
        sa.Column("response_model", sa.String(100), nullable=False),
        sa.Column("mode", sa.String(20), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("workspace_id", "cache_key", name="uq_interpretation_cache_key"),
    )
    op.create_index(
        "ix_interpretation_cache_workspace_id", "interpretation_cache", ["workspace_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_interpretation_cache_workspace_id", table_name="interpretation_cache")
    op.drop_table("interpretation_cache")
    op.drop_index("ix_interpretation_calls_status", table_name="interpretation_calls")
    op.drop_index("ix_interpretation_calls_execution_id", table_name="interpretation_calls")
    op.drop_index("ix_interpretation_calls_session_id", table_name="interpretation_calls")
    op.drop_index("ix_interpretation_calls_workspace_id", table_name="interpretation_calls")
    op.drop_table("interpretation_calls")
    op.drop_table("interpretation_budget_counters")
