"""Track preview activity and invite access for bounded hosting."""

import sqlalchemy as sa
from alembic import op

revision = "0004_preview_lifecycle"
down_revision = "0003_interpretation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "workspaces",
        sa.Column(
            "last_active_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.alter_column("workspaces", "last_active_at", server_default=None)
    op.create_index("ix_workspaces_last_active_at", "workspaces", ["last_active_at"])
    op.add_column(
        "sessions",
        sa.Column("provider_access", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.alter_column("sessions", "provider_access", server_default=None)


def downgrade() -> None:
    op.drop_column("sessions", "provider_access")
    op.drop_index("ix_workspaces_last_active_at", table_name="workspaces")
    op.drop_column("workspaces", "last_active_at")
