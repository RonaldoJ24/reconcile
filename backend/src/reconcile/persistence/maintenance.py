from __future__ import annotations

import os
import uuid
from datetime import timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from reconcile.persistence.models import (
    ApplicationGroup,
    AuditEvent,
    CashApplication,
    CreditApplication,
    CreditNote,
    IdempotencyKey,
    ImportBatch,
    InterpretationCache,
    Invoice,
    Job,
    Payment,
    Proposal,
    ProposalRevision,
    Source,
    Workspace,
    now_utc,
)
from reconcile.persistence.models import (
    Session as DbSession,
)
from reconcile.persistence.service import ServiceError

DEFAULT_DATABASE_LIMIT = 300 * 1024 * 1024
DEFAULT_PREVIEW_TTL_HOURS = 24


def _positive_int(name: str, default: int) -> int:
    raw = os.getenv(name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer") from exc
    if value <= 0:
        raise RuntimeError(f"{name} must be positive")
    return value


def enforce_database_admission(session: Session) -> None:
    limit = _positive_int("RECONCILE_MAX_DATABASE_BYTES", DEFAULT_DATABASE_LIMIT)
    size = int(session.scalar(select(func.pg_database_size(func.current_database()))) or 0)
    if size >= limit:
        raise ServiceError(
            "database_capacity",
            "preview storage admission is paused at the configured database limit",
            503,
        )


def cleanup_expired_preview_workspaces(session: Session, *, batch_size: int = 10) -> int:
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    ttl = _positive_int("RECONCILE_SYNTHETIC_SESSION_TTL_HOURS", DEFAULT_PREVIEW_TTL_HOURS)
    cutoff = now_utc() - timedelta(hours=ttl)
    workspace_ids = list(
        session.scalars(
            select(Workspace.id)
            .where(Workspace.mode == "preview", Workspace.last_active_at < cutoff)
            .order_by(Workspace.last_active_at)
            .limit(batch_size)
            .with_for_update(skip_locked=True)
        )
    )
    if not workspace_ids:
        session.rollback()
        return 0
    _delete_workspace_records(session, workspace_ids)
    session.commit()
    return len(workspace_ids)


def _delete_workspace_records(session: Session, workspace_ids: list[uuid.UUID]) -> None:
    proposal_ids = select(Proposal.id).where(Proposal.workspace_id.in_(workspace_ids))
    session.execute(delete(CashApplication).where(CashApplication.workspace_id.in_(workspace_ids)))
    session.execute(
        delete(CreditApplication).where(CreditApplication.workspace_id.in_(workspace_ids))
    )
    session.execute(
        delete(ApplicationGroup).where(ApplicationGroup.workspace_id.in_(workspace_ids))
    )
    session.execute(delete(ProposalRevision).where(ProposalRevision.proposal_id.in_(proposal_ids)))
    session.execute(
        delete(InterpretationCache).where(InterpretationCache.workspace_id.in_(workspace_ids))
    )
    session.execute(delete(AuditEvent).where(AuditEvent.workspace_id.in_(workspace_ids)))
    session.execute(delete(IdempotencyKey).where(IdempotencyKey.workspace_id.in_(workspace_ids)))
    session.execute(delete(Job).where(Job.workspace_id.in_(workspace_ids)))
    session.execute(delete(Proposal).where(Proposal.workspace_id.in_(workspace_ids)))
    session.execute(delete(Payment).where(Payment.workspace_id.in_(workspace_ids)))
    session.execute(delete(Invoice).where(Invoice.workspace_id.in_(workspace_ids)))
    session.execute(delete(CreditNote).where(CreditNote.workspace_id.in_(workspace_ids)))
    session.execute(delete(Source).where(Source.workspace_id.in_(workspace_ids)))
    session.execute(delete(ImportBatch).where(ImportBatch.workspace_id.in_(workspace_ids)))
    session.execute(delete(DbSession).where(DbSession.workspace_id.in_(workspace_ids)))
    # Interpretation calls intentionally survive cleanup so spend cannot be reset.
    session.execute(delete(Workspace).where(Workspace.id.in_(workspace_ids)))
