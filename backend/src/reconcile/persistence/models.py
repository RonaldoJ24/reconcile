from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import Uuid

from reconcile.domain.types import JobStatus, ProposalStatus


def now_utc() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class Workspace(Base):
    __tablename__ = "workspaces"
    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    mode: Mapped[str] = mapped_column(String(20), default="local")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class Session(Base):
    __tablename__ = "sessions"
    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    csrf_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class ImportBatch(Base):
    __tablename__ = "import_batches"
    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), index=True)
    status: Mapped[str] = mapped_column(String(20), default="VALIDATED")
    profile: Mapped[str] = mapped_column(String(20), default="local")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class Source(Base):
    __tablename__ = "sources"
    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), index=True)
    batch_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("import_batches.id"), index=True)
    kind: Mapped[str] = mapped_column(String(20))
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    raw_bytes: Mapped[bytes] = mapped_column(LargeBinary)
    source_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(30), default="VALIDATED")
    accepted_count: Mapped[int] = mapped_column(Integer, default=0)
    rejected_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    __table_args__ = (UniqueConstraint("workspace_id", "kind", "sha256", name="uq_source_hash"),)


class Payment(Base):
    __tablename__ = "payments"
    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), index=True)
    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sources.id"))
    source_account_id: Mapped[str] = mapped_column(String(100))
    transaction_id: Mapped[str] = mapped_column(String(100))
    booking_date: Mapped[date] = mapped_column(Date)
    payer_name: Mapped[str] = mapped_column(String(200))
    reference: Mapped[str] = mapped_column(String(500))
    amount: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3), default="MXN")
    customer_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    conflicted: Mapped[bool] = mapped_column(Boolean, default=False)
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "source_account_id", "transaction_id", name="uq_payment_natural"
        ),
    )


class Invoice(Base):
    __tablename__ = "invoices"
    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), index=True)
    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sources.id"))
    customer_id: Mapped[str] = mapped_column(String(100))
    customer_name: Mapped[str] = mapped_column(String(200))
    invoice_id: Mapped[str] = mapped_column(String(100))
    issued_date: Mapped[date] = mapped_column(Date)
    due_date: Mapped[date] = mapped_column(Date)
    balance_as_of: Mapped[date] = mapped_column(Date)
    outstanding_amount: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3), default="MXN")
    version: Mapped[int] = mapped_column(Integer, default=1)
    conflicted: Mapped[bool] = mapped_column(Boolean, default=False)
    __table_args__ = (
        UniqueConstraint("workspace_id", "customer_id", "invoice_id", name="uq_invoice_natural"),
    )


class CreditNote(Base):
    __tablename__ = "credit_notes"
    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), index=True)
    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sources.id"))
    customer_id: Mapped[str] = mapped_column(String(100))
    credit_note_id: Mapped[str] = mapped_column(String(100))
    balance_as_of: Mapped[date] = mapped_column(Date)
    available_amount: Mapped[int] = mapped_column(BigInteger)
    invoice_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    currency: Mapped[str] = mapped_column(String(3), default="MXN")
    version: Mapped[int] = mapped_column(Integer, default=1)
    conflicted: Mapped[bool] = mapped_column(Boolean, default=False)
    __table_args__ = (
        UniqueConstraint("workspace_id", "customer_id", "credit_note_id", name="uq_credit_natural"),
    )


class Proposal(Base):
    __tablename__ = "proposals"
    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), index=True)
    payment_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("payments.id"), index=True)
    status: Mapped[str] = mapped_column(
        String(20), default=ProposalStatus.PROCESSING.value, index=True
    )
    current_revision: Mapped[int] = mapped_column(Integer, default=0)
    applied_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    review_required: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc, onupdate=now_utc
    )


class ProposalRevision(Base):
    __tablename__ = "proposal_revisions"
    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    proposal_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("proposals.id"), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    cash_lines: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    credit_lines: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    evidence: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    alternatives: Mapped[list[list[str]]] = mapped_column(JSON, default=list)
    signals: Mapped[list[str]] = mapped_column(JSON, default=list)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20))
    version_token: Mapped[str] = mapped_column(String(64))
    provenance: Mapped[str] = mapped_column(String(30), default="rules-v1")
    reviewer: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    __table_args__ = (UniqueConstraint("proposal_id", "revision", name="uq_proposal_revision"),)


class ApplicationGroup(Base):
    __tablename__ = "application_groups"
    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), index=True)
    proposal_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("proposals.id"), index=True)
    proposal_revision: Mapped[int] = mapped_column(Integer)
    payment_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("payments.id"), index=True)
    reviewer: Mapped[str] = mapped_column(String(200))
    reversed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class CashApplication(Base):
    __tablename__ = "cash_applications"
    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), index=True)
    application_group_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("application_groups.id"), index=True
    )
    payment_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("payments.id"))
    invoice_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("invoices.id"))
    amount: Mapped[int] = mapped_column(BigInteger)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class CreditApplication(Base):
    __tablename__ = "credit_applications"
    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), index=True)
    application_group_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("application_groups.id"), index=True
    )
    credit_note_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("credit_notes.id"))
    invoice_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("invoices.id"))
    amount: Mapped[int] = mapped_column(BigInteger)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class IdempotencyKey(Base):
    __tablename__ = "idempotency_keys"
    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), index=True)
    action: Mapped[str] = mapped_column(String(30))
    key: Mapped[str] = mapped_column(String(200))
    payload_hash: Mapped[str] = mapped_column(String(64))
    result_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    __table_args__ = (UniqueConstraint("workspace_id", "action", "key", name="uq_idempotency"),)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), index=True)
    action: Mapped[str] = mapped_column(String(50))
    actor: Mapped[str] = mapped_column(String(200))
    entity_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), index=True)
    kind: Mapped[str] = mapped_column(String(30))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20), default=JobStatus.PENDING.value, index=True)
    lease_owner: Mapped[str | None] = mapped_column(String(100), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc, index=True
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
