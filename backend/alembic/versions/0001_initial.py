"""Create the version-one PostgreSQL schema explicitly."""
# ruff: noqa: E501

import sqlalchemy as sa
from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def _id(name: str = "id") -> sa.Column:
    return sa.Column(name, sa.Uuid(as_uuid=True), primary_key=True)


def upgrade() -> None:
    op.create_table(
        "workspaces",
        _id(),
        sa.Column("mode", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "sessions",
        _id(),
        sa.Column(
            "workspace_id", sa.Uuid(as_uuid=True), sa.ForeignKey("workspaces.id"), nullable=False
        ),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("csrf_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_table(
        "import_batches",
        _id(),
        sa.Column(
            "workspace_id", sa.Uuid(as_uuid=True), sa.ForeignKey("workspaces.id"), nullable=False
        ),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("profile", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "sources",
        _id(),
        sa.Column(
            "workspace_id", sa.Uuid(as_uuid=True), sa.ForeignKey("workspaces.id"), nullable=False
        ),
        sa.Column(
            "batch_id", sa.Uuid(as_uuid=True), sa.ForeignKey("import_batches.id"), nullable=False
        ),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("raw_bytes", sa.LargeBinary(), nullable=False),
        sa.Column("source_metadata", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("accepted_count", sa.Integer(), nullable=False),
        sa.Column("rejected_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("workspace_id", "kind", "sha256", name="uq_source_hash"),
    )
    op.create_table(
        "payments",
        _id(),
        sa.Column(
            "workspace_id", sa.Uuid(as_uuid=True), sa.ForeignKey("workspaces.id"), nullable=False
        ),
        sa.Column("source_id", sa.Uuid(as_uuid=True), sa.ForeignKey("sources.id"), nullable=False),
        sa.Column("source_account_id", sa.String(100), nullable=False),
        sa.Column("transaction_id", sa.String(100), nullable=False),
        sa.Column("booking_date", sa.Date(), nullable=False),
        sa.Column("payer_name", sa.String(200), nullable=False),
        sa.Column("reference", sa.String(500), nullable=False),
        sa.Column("amount", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("customer_id", sa.String(100)),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("conflicted", sa.Boolean(), nullable=False),
        sa.UniqueConstraint(
            "workspace_id", "source_account_id", "transaction_id", name="uq_payment_natural"
        ),
    )
    op.create_table(
        "invoices",
        _id(),
        sa.Column(
            "workspace_id", sa.Uuid(as_uuid=True), sa.ForeignKey("workspaces.id"), nullable=False
        ),
        sa.Column("source_id", sa.Uuid(as_uuid=True), sa.ForeignKey("sources.id"), nullable=False),
        sa.Column("customer_id", sa.String(100), nullable=False),
        sa.Column("customer_name", sa.String(200), nullable=False),
        sa.Column("invoice_id", sa.String(100), nullable=False),
        sa.Column("issued_date", sa.Date(), nullable=False),
        sa.Column("due_date", sa.Date(), nullable=False),
        sa.Column("balance_as_of", sa.Date(), nullable=False),
        sa.Column("outstanding_amount", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("conflicted", sa.Boolean(), nullable=False),
        sa.UniqueConstraint("workspace_id", "customer_id", "invoice_id", name="uq_invoice_natural"),
    )
    op.create_table(
        "credit_notes",
        _id(),
        sa.Column(
            "workspace_id", sa.Uuid(as_uuid=True), sa.ForeignKey("workspaces.id"), nullable=False
        ),
        sa.Column("source_id", sa.Uuid(as_uuid=True), sa.ForeignKey("sources.id"), nullable=False),
        sa.Column("customer_id", sa.String(100), nullable=False),
        sa.Column("credit_note_id", sa.String(100), nullable=False),
        sa.Column("balance_as_of", sa.Date(), nullable=False),
        sa.Column("available_amount", sa.BigInteger(), nullable=False),
        sa.Column("invoice_id", sa.String(100)),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("conflicted", sa.Boolean(), nullable=False),
        sa.UniqueConstraint(
            "workspace_id", "customer_id", "credit_note_id", name="uq_credit_natural"
        ),
    )
    op.create_table(
        "proposals",
        _id(),
        sa.Column(
            "workspace_id", sa.Uuid(as_uuid=True), sa.ForeignKey("workspaces.id"), nullable=False
        ),
        sa.Column(
            "payment_id", sa.Uuid(as_uuid=True), sa.ForeignKey("payments.id"), nullable=False
        ),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("current_revision", sa.Integer(), nullable=False),
        sa.Column("applied_revision", sa.Integer()),
        sa.Column("review_required", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "proposal_revisions",
        _id(),
        sa.Column(
            "proposal_id", sa.Uuid(as_uuid=True), sa.ForeignKey("proposals.id"), nullable=False
        ),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("cash_lines", sa.JSON(), nullable=False),
        sa.Column("credit_lines", sa.JSON(), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("alternatives", sa.JSON(), nullable=False),
        sa.Column("signals", sa.JSON(), nullable=False),
        sa.Column("reason", sa.Text()),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("version_token", sa.String(64), nullable=False),
        sa.Column("provenance", sa.String(30), nullable=False),
        sa.Column("reviewer", sa.String(200)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("proposal_id", "revision", name="uq_proposal_revision"),
    )
    op.create_table(
        "application_groups",
        _id(),
        sa.Column(
            "workspace_id", sa.Uuid(as_uuid=True), sa.ForeignKey("workspaces.id"), nullable=False
        ),
        sa.Column(
            "proposal_id", sa.Uuid(as_uuid=True), sa.ForeignKey("proposals.id"), nullable=False
        ),
        sa.Column("proposal_revision", sa.Integer(), nullable=False),
        sa.Column(
            "payment_id", sa.Uuid(as_uuid=True), sa.ForeignKey("payments.id"), nullable=False
        ),
        sa.Column("reviewer", sa.String(200), nullable=False),
        sa.Column("reversed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "cash_applications",
        _id(),
        sa.Column(
            "workspace_id", sa.Uuid(as_uuid=True), sa.ForeignKey("workspaces.id"), nullable=False
        ),
        sa.Column(
            "application_group_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("application_groups.id"),
            nullable=False,
        ),
        sa.Column(
            "payment_id", sa.Uuid(as_uuid=True), sa.ForeignKey("payments.id"), nullable=False
        ),
        sa.Column(
            "invoice_id", sa.Uuid(as_uuid=True), sa.ForeignKey("invoices.id"), nullable=False
        ),
        sa.Column("amount", sa.BigInteger(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
    )
    op.create_table(
        "credit_applications",
        _id(),
        sa.Column(
            "workspace_id", sa.Uuid(as_uuid=True), sa.ForeignKey("workspaces.id"), nullable=False
        ),
        sa.Column(
            "application_group_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("application_groups.id"),
            nullable=False,
        ),
        sa.Column(
            "credit_note_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("credit_notes.id"),
            nullable=False,
        ),
        sa.Column(
            "invoice_id", sa.Uuid(as_uuid=True), sa.ForeignKey("invoices.id"), nullable=False
        ),
        sa.Column("amount", sa.BigInteger(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
    )
    op.create_table(
        "idempotency_keys",
        _id(),
        sa.Column(
            "workspace_id", sa.Uuid(as_uuid=True), sa.ForeignKey("workspaces.id"), nullable=False
        ),
        sa.Column("action", sa.String(30), nullable=False),
        sa.Column("key", sa.String(200), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("result_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("workspace_id", "action", "key", name="uq_idempotency"),
    )
    op.create_table(
        "audit_events",
        _id(),
        sa.Column(
            "workspace_id", sa.Uuid(as_uuid=True), sa.ForeignKey("workspaces.id"), nullable=False
        ),
        sa.Column("action", sa.String(50), nullable=False),
        sa.Column("actor", sa.String(200), nullable=False),
        sa.Column("entity_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "jobs",
        _id(),
        sa.Column(
            "workspace_id", sa.Uuid(as_uuid=True), sa.ForeignKey("workspaces.id"), nullable=False
        ),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("lease_owner", sa.String(100)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("error", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
    )
    for table, columns in {
        "sessions": ["workspace_id", "expires_at"],
        "import_batches": ["workspace_id"],
        "sources": ["workspace_id", "batch_id"],
        "payments": ["workspace_id"],
        "invoices": ["workspace_id"],
        "credit_notes": ["workspace_id"],
        "proposals": ["workspace_id", "payment_id", "status"],
        "proposal_revisions": ["proposal_id"],
        "application_groups": ["workspace_id", "proposal_id", "payment_id"],
        "cash_applications": ["workspace_id", "application_group_id"],
        "credit_applications": ["workspace_id", "application_group_id"],
        "idempotency_keys": ["workspace_id"],
        "audit_events": ["workspace_id"],
        "jobs": ["workspace_id", "status", "available_at", "lease_expires_at"],
    }.items():
        for column in columns:
            op.create_index(f"ix_{table}_{column}", table, [column])


def downgrade() -> None:
    for table in [
        "jobs",
        "audit_events",
        "idempotency_keys",
        "credit_applications",
        "cash_applications",
        "application_groups",
        "proposal_revisions",
        "proposals",
        "credit_notes",
        "invoices",
        "payments",
        "sources",
        "import_batches",
        "sessions",
        "workspaces",
    ]:
        op.drop_table(table)
