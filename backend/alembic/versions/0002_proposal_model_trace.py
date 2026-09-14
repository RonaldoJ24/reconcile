"""Persist observed learned-ranker traces on proposal revisions."""

import sqlalchemy as sa
from alembic import op

revision = "0002_model_trace"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "proposal_revisions",
        sa.Column("model_trace", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
    )
    op.alter_column("proposal_revisions", "model_trace", server_default=None)


def downgrade() -> None:
    op.drop_column("proposal_revisions", "model_trace")
