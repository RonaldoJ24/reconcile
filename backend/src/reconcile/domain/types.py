from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum


class ProposalStatus(StrEnum):
    PROCESSING = "PROCESSING"
    PROPOSED = "PROPOSED"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    STALE = "STALE"
    REJECTED = "REJECTED"
    APPLIED = "APPLIED"
    REVERSED = "REVERSED"
    FAILED = "FAILED"


class JobStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


@dataclass(frozen=True, slots=True)
class PaymentFact:
    source_account_id: str
    transaction_id: str
    booking_date: date
    payer_name: str
    reference: str
    amount: int
    customer_id: str | None = None
    source_id: str | None = None
    version: int = 1


@dataclass(frozen=True, slots=True)
class InvoiceFact:
    customer_id: str
    invoice_id: str
    customer_name: str
    issued_date: date
    due_date: date
    balance_as_of: date
    outstanding_amount: int
    version: int = 1


@dataclass(frozen=True, slots=True)
class CreditFact:
    customer_id: str
    credit_note_id: str
    balance_as_of: date
    available_amount: int
    invoice_id: str | None = None
    version: int = 1


@dataclass(frozen=True, slots=True)
class CashLine:
    invoice_id: str
    amount: int


@dataclass(frozen=True, slots=True)
class CreditLine:
    credit_note_id: str
    invoice_id: str
    amount: int


@dataclass(frozen=True, slots=True)
class Evidence:
    source_id: str
    start: int
    end: int
    quote: str = ""


@dataclass(frozen=True, slots=True)
class ProposalResult:
    status: ProposalStatus
    cash: tuple[CashLine, ...] = ()
    credits: tuple[CreditLine, ...] = ()
    evidence: tuple[Evidence, ...] = ()
    alternatives: tuple[tuple[str, ...], ...] = ()
    signals: tuple[str, ...] = ()
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class Correction:
    cash: tuple[CashLine, ...]
    credits: tuple[CreditLine, ...] = ()
    reviewer: str = ""


@dataclass(slots=True)
class InMemoryState:
    payments: dict[str, PaymentFact] = field(default_factory=dict)
    invoices: dict[str, InvoiceFact] = field(default_factory=dict)
    credits: dict[str, CreditFact] = field(default_factory=dict)
