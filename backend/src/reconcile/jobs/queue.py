from __future__ import annotations

import os
import socket
import uuid
from datetime import timedelta

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from reconcile.domain.types import JobStatus
from reconcile.persistence.models import Job, now_utc
from reconcile.persistence.service import ReconcileService


def claim_one(session: Session, owner: str | None = None, lease_seconds: int = 60) -> Job | None:
    owner = owner or f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex}"
    now = now_utc()
    job = session.scalar(
        select(Job)
        .where(
            Job.status == JobStatus.PENDING.value,
            Job.available_at <= now,
            or_(Job.lease_expires_at.is_(None), Job.lease_expires_at <= now),
        )
        .order_by(Job.available_at, Job.created_at)
        .with_for_update(skip_locked=True)
    )
    if job is None:
        # Expired RUNNING rows become reclaimable without a cleanup sweep.
        job = session.scalar(
            select(Job)
            .where(
                Job.status == JobStatus.RUNNING.value,
                Job.lease_expires_at <= now,
                Job.attempts < Job.max_attempts,
            )
            .order_by(Job.lease_expires_at)
            .with_for_update(skip_locked=True)
        )
    if job is None:
        return None
    job.status = JobStatus.RUNNING.value
    job.lease_owner = owner
    job.lease_expires_at = now + timedelta(seconds=lease_seconds)
    job.attempts += 1
    session.commit()
    return job


def run_once(session: Session, *, owner: str | None = None, lease_seconds: int = 60) -> Job | None:
    job = claim_one(session, owner=owner, lease_seconds=lease_seconds)
    if job is None:
        return None
    try:
        if job.kind == "match-payment":
            ReconcileService(session).process_match(
                uuid.UUID(job.payload["workspace_id"])
                if "workspace_id" in job.payload
                else job.workspace_id,
                uuid.UUID(job.payload["payment_id"]),
            )
        else:
            raise ValueError(f"unsupported job kind: {job.kind}")
        job.status = JobStatus.SUCCEEDED.value
        job.lease_owner = None
        job.lease_expires_at = None
        job.finished_at = now_utc()
        session.commit()
        return job
    except Exception as exc:
        session.rollback()
        current = session.get(Job, job.id)
        if current is not None:
            current.error = str(exc)[:2000]
            current.lease_owner = None
            current.lease_expires_at = None
            current.status = (
                JobStatus.FAILED.value
                if current.attempts >= current.max_attempts
                else JobStatus.PENDING.value
            )
            current.available_at = now_utc() + timedelta(seconds=min(current.attempts * 5, 60))
            session.commit()
            return current
        return None
