"""Strict, provider-independent schemas for bounded interpretation."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime
from enum import StrEnum
from math import isfinite
from typing import Literal

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

PROMPT_VERSION = "reconcile-interpretation-prompt-v1"
SCHEMA_VERSION = "reconcile-interpretation-schema-v1"


class StrictModel(BaseModel):
    """All wire models reject fields not explicitly in the contract."""

    model_config = ConfigDict(extra="forbid")


def _nonblank(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("value must not be blank")
    return value


def _currency(value: str) -> str:
    value = value.strip().upper()
    if len(value) != 3 or not value.isascii() or not value.isalpha():
        raise ValueError("currency must be a three-letter code")
    return value


class PaymentObservation(StrictModel):
    """The decision-time payment facts supplied to the interpreter."""

    payment_id: str = Field(min_length=1, max_length=200)
    amount_centavos: int = Field(
        validation_alias=AliasChoices("amount_centavos", "amount"),
        gt=0,
    )
    currency: str
    booking_date: date | None = None
    payer_name: str = ""
    reference: str = ""
    source_id: str | None = Field(default=None, max_length=200)
    source_hash: str | None = Field(default=None, max_length=200)
    version: int = Field(
        validation_alias=AliasChoices("version", "record_version"), default=1, ge=1
    )

    _payment_id = field_validator("payment_id")(_nonblank)
    _currency = field_validator("currency")(_currency)


class InvoiceObservation(StrictModel):
    """One imported invoice observation available to candidate validation."""

    invoice_id: str = Field(min_length=1, max_length=200)
    outstanding_amount_centavos: int = Field(
        validation_alias=AliasChoices(
            "outstanding_amount_centavos",
            "amount_centavos",
            "outstanding_amount",
            "amount",
        ),
        gt=0,
    )
    currency: str
    customer_id: str | None = Field(default=None, max_length=200)
    customer_name: str = ""
    issued_date: date | None = None
    due_date: date | None = None
    balance_as_of: date | None = None
    source_id: str | None = Field(default=None, max_length=200)
    source_hash: str | None = Field(default=None, max_length=200)
    source_version: int | None = Field(default=None, ge=1)
    version: int = Field(
        validation_alias=AliasChoices("version", "record_version"), default=1, ge=1
    )

    _invoice_id = field_validator("invoice_id")(_nonblank)
    _currency = field_validator("currency")(_currency)


class CreditObservation(StrictModel):
    """Optional credit context, kept separate from cash and invoice balances."""

    credit_note_id: str = Field(min_length=1, max_length=200)
    invoice_id: str | None = Field(default=None, max_length=200)
    available_amount_centavos: int = Field(
        validation_alias=AliasChoices(
            "available_amount_centavos",
            "amount_centavos",
            "available_amount",
            "amount",
        ),
        gt=0,
    )
    currency: str
    customer_id: str | None = Field(default=None, max_length=200)
    balance_as_of: date | None = None
    source_id: str | None = Field(default=None, max_length=200)
    source_hash: str | None = Field(default=None, max_length=200)
    source_version: int | None = Field(default=None, ge=1)
    version: int = Field(
        validation_alias=AliasChoices("version", "record_version"), default=1, ge=1
    )

    _credit_note_id = field_validator("credit_note_id")(_nonblank)
    _invoice_id = field_validator("invoice_id")(
        lambda value: _nonblank(value) if value is not None else value
    )
    _currency = field_validator("currency")(_currency)


class AllocationLine(StrictModel):
    """Cash allocation line carried by an already enumerated candidate."""

    invoice_id: str = Field(min_length=1, max_length=200)
    amount_centavos: int = Field(validation_alias=AliasChoices("amount_centavos", "amount"), gt=0)

    _invoice_id = field_validator("invoice_id")(_nonblank)


class CreditAllocationLine(StrictModel):
    """Credit allocation line carried by an already enumerated candidate."""

    credit_note_id: str = Field(min_length=1, max_length=200)
    invoice_id: str = Field(min_length=1, max_length=200)
    amount_centavos: int = Field(validation_alias=AliasChoices("amount_centavos", "amount"), gt=0)

    _credit_note_id = field_validator("credit_note_id")(_nonblank)
    _invoice_id = field_validator("invoice_id")(_nonblank)


class CandidateAllocation(StrictModel):
    """Complete pre-enumerated allocation; the model can select its ID only."""

    candidate_id: str = Field(min_length=1, max_length=200)
    invoice_ids: tuple[str, ...] = Field(min_length=1, max_length=3)
    cash: tuple[AllocationLine, ...] = Field(default=(), max_length=3)
    credits: tuple[CreditAllocationLine, ...] = Field(default=(), max_length=1)

    _candidate_id = field_validator("candidate_id")(_nonblank)

    @field_validator("invoice_ids")
    @classmethod
    def invoice_ids_nonblank(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(values)) != len(values) or any(not _nonblank(value) for value in values):
            raise ValueError("candidate invoice IDs must be unique and nonblank")
        return values


class SourceSpan(StrictModel):
    """An immutable source text plus the relevant bounds supplied to the model."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_id: str = Field(min_length=1, max_length=200)
    start: int = Field(ge=0)
    end: int = Field(ge=1)
    content: str = Field(
        validation_alias=AliasChoices("content", "text", "source_text"),
        max_length=100_000,
    )
    source_hash: str = Field(default="", max_length=200)
    source_version: int = Field(default=1, ge=1)

    _source_id = field_validator("source_id")(_nonblank)

    @model_validator(mode="after")
    def valid_bounds(self) -> SourceSpan:
        if self.end > len(self.content) or self.start >= self.end:
            raise ValueError("source span bounds must fit its immutable content")
        return self


class RankedCandidate(StrictModel):
    """A ranker's context; its score is never treated as confidence."""

    candidate_id: str = Field(min_length=1, max_length=200)
    raw_score: float = Field(validation_alias=AliasChoices("raw_score", "score"))

    _candidate_id = field_validator("candidate_id")(_nonblank)

    @field_validator("raw_score")
    @classmethod
    def finite_score(cls, value: float) -> float:
        if not isfinite(value):
            raise ValueError("rank score must be finite")
        return value


class InterpretationRequest(StrictModel):
    """Bounded direct or hybrid input to one interpretation call."""

    workspace_id: str = Field(min_length=1, max_length=200)
    payment: PaymentObservation
    invoices: tuple[InvoiceObservation, ...] = Field(
        validation_alias=AliasChoices("invoices", "invoice_observations"),
        min_length=1,
        max_length=10,
    )
    candidates: tuple[CandidateAllocation, ...] = Field(
        validation_alias=AliasChoices("candidates", "candidate_allocations"),
        min_length=1,
        max_length=10,
    )
    credits: tuple[CreditObservation, ...] = Field(
        validation_alias=AliasChoices("credits", "credit"), default=(), max_length=1
    )
    source_spans: tuple[SourceSpan, ...] = Field(
        validation_alias=AliasChoices("source_spans", "evidence", "evidence_spans"),
        max_length=5,
    )
    mode: Literal["direct", "hybrid"] = "direct"
    ranked_candidates: tuple[RankedCandidate, ...] = Field(default=(), max_length=10)
    prompt_version: str = PROMPT_VERSION
    schema_version: str = SCHEMA_VERSION
    budget_policy_version: str = "reconcile-budget-v1"
    decision_timestamp: datetime

    _workspace_id = field_validator("workspace_id")(_nonblank)

    @model_validator(mode="after")
    def semantic_bounds(self) -> InterpretationRequest:
        invoice_ids = {invoice.invoice_id for invoice in self.invoices}
        if len(invoice_ids) != len(self.invoices):
            raise ValueError("invoice IDs must be unique")
        credit_ids = {credit.credit_note_id for credit in self.credits}
        if len(credit_ids) != len(self.credits):
            raise ValueError("credit note IDs must be unique")
        candidate_ids = [candidate.candidate_id for candidate in self.candidates]
        if len(set(candidate_ids)) != len(candidate_ids):
            raise ValueError("candidate IDs must be unique")
        source_ids = [span.source_id for span in self.source_spans]
        if len(set(source_ids)) != len(source_ids):
            raise ValueError("source IDs must be unique within a request")
        if self.mode == "direct" and self.ranked_candidates:
            raise ValueError("direct requests must not contain rank context")
        if self.mode == "hybrid":
            if not self.ranked_candidates:
                raise ValueError("hybrid requests require rank context")
            ranked_ids = [item.candidate_id for item in self.ranked_candidates]
            if len(set(ranked_ids)) != len(ranked_ids):
                raise ValueError("ranked candidate IDs must be unique")
            if not set(ranked_ids).issubset(candidate_ids):
                raise ValueError("rank context contains an unknown candidate")
        for candidate in self.candidates:
            if not set(candidate.invoice_ids).issubset(invoice_ids):
                raise ValueError("candidate contains an unknown invoice")
            cash_ids = [line.invoice_id for line in candidate.cash]
            if len(set(cash_ids)) != len(cash_ids):
                raise ValueError("candidate cash invoice IDs must be unique")
            if not set(cash_ids).issubset(candidate.invoice_ids):
                raise ValueError("candidate cash line targets an unlisted invoice")
            credit_line_ids = [line.credit_note_id for line in candidate.credits]
            if len(set(credit_line_ids)) != len(credit_line_ids):
                raise ValueError("candidate credit IDs must be unique")
            if not {line.invoice_id for line in candidate.credits}.issubset(candidate.invoice_ids):
                raise ValueError("candidate credit line targets an unlisted invoice")
            for line in candidate.credits:
                credit = (
                    next(
                        item for item in self.credits if item.credit_note_id == line.credit_note_id
                    )
                    if line.credit_note_id in credit_ids
                    else None
                )
                if credit is None:
                    raise ValueError("candidate contains an unknown credit note")
                if credit.invoice_id != line.invoice_id:
                    raise ValueError("candidate credit line violates explicit credit linkage")
        for invoice in self.invoices:
            if invoice.currency != self.payment.currency:
                raise ValueError("invoice currency must match payment currency")
        for credit in self.credits:
            if credit.currency != self.payment.currency:
                raise ValueError("credit currency must match payment currency")
        return self

    @property
    def evidence(self) -> tuple[SourceSpan, ...]:
        return self.source_spans


class Citation(StrictModel):
    """A model citation; exact source-slice validity is checked against a request."""

    source_id: str = Field(min_length=1, max_length=200)
    start: int = Field(ge=0)
    end: int = Field(ge=1)
    quote: str = Field(min_length=1, max_length=100_000)

    _source_id = field_validator("source_id")(_nonblank)

    @model_validator(mode="after")
    def ordered(self) -> Citation:
        if self.start >= self.end:
            raise ValueError("citation start must be less than end")
        return self


class Decision(StrEnum):
    SELECT = "select"
    NEEDS_REVIEW = "needs_review"


class ReasonCode(StrEnum):
    EVIDENCE_SUPPORTED = "evidence_supported"
    AMBIGUOUS = "ambiguous"
    CONTRADICTORY = "contradictory"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class InterpretationResult(StrictModel):
    """Only a known candidate may be selected; this model never carries money."""

    decision: Decision
    candidate_id: str | None = Field(max_length=200)
    reason_code: ReasonCode
    citations: tuple[Citation, ...] = Field(max_length=5)

    @field_validator("candidate_id")
    @classmethod
    def candidate_nonblank(cls, value: str | None) -> str | None:
        return _nonblank(value) if value is not None else None

    @model_validator(mode="after")
    def decision_shape(self) -> InterpretationResult:
        if self.decision is Decision.SELECT:
            if self.candidate_id is None:
                raise ValueError("select requires a candidate_id")
            if not self.citations:
                raise ValueError("select requires at least one citation")
        elif self.candidate_id is not None:
            raise ValueError("needs_review requires a null candidate_id")
        return self


class SemanticValidationError(ValueError):
    """A syntactically valid model result violates the supplied request."""

    def __init__(self, message: str, *, kind: str = "result") -> None:
        super().__init__(message)
        self.kind = kind


def validate_result(
    request: InterpretationRequest,
    result: InterpretationResult,
    *,
    allocation_validator: (
        Callable[[InterpretationRequest, InterpretationResult], object] | None
    ) = None,
) -> None:
    """Check candidate/source identity and exact citation slices.

    A deterministic allocation validator can be injected by the workflow. The
    provider boundary deliberately does not construct domain money lines.
    """

    candidate_ids = {candidate.candidate_id for candidate in request.candidates}
    if result.candidate_id is not None and result.candidate_id not in candidate_ids:
        raise SemanticValidationError("result names an unknown candidate", kind="candidate")

    sources = {span.source_id: span for span in request.source_spans}
    for citation in result.citations:
        source = sources.get(citation.source_id)
        if source is None:
            raise SemanticValidationError("citation names an unknown source", kind="citation")
        if citation.start < source.start or citation.end > source.end:
            raise SemanticValidationError(
                "citation is outside the supplied source span", kind="citation"
            )
        if source.content[citation.start : citation.end] != citation.quote:
            raise SemanticValidationError(
                "citation quote is not the exact source slice", kind="citation"
            )

    if result.decision is Decision.SELECT:
        selected = next(
            candidate
            for candidate in request.candidates
            if candidate.candidate_id == result.candidate_id
        )
        invoices = {invoice.invoice_id: invoice for invoice in request.invoices}
        credits = {credit.credit_note_id: credit for credit in request.credits}
        if not selected.cash and not selected.credits:
            raise SemanticValidationError(
                "selected candidate contains no allocation lines", kind="allocation"
            )
        if sum(line.amount_centavos for line in selected.cash) > request.payment.amount_centavos:
            raise SemanticValidationError(
                "selected cash allocation exceeds the payment", kind="allocation"
            )
        invoice_totals: dict[str, int] = {}
        for cash_line in selected.cash:
            invoice_totals[cash_line.invoice_id] = (
                invoice_totals.get(cash_line.invoice_id, 0) + cash_line.amount_centavos
            )
        for credit_line in selected.credits:
            credit = credits[credit_line.credit_note_id]
            if credit_line.amount_centavos > credit.available_amount_centavos:
                raise SemanticValidationError(
                    "selected credit allocation exceeds available credit", kind="allocation"
                )
            invoice_totals[credit_line.invoice_id] = (
                invoice_totals.get(credit_line.invoice_id, 0) + credit_line.amount_centavos
            )
        for invoice_id, total in invoice_totals.items():
            if total > invoices[invoice_id].outstanding_amount_centavos:
                raise SemanticValidationError(
                    "selected allocation exceeds invoice balance", kind="allocation"
                )

    if allocation_validator is not None and result.decision is Decision.SELECT:
        verdict = allocation_validator(request, result)
        if isinstance(verdict, AllocationValidatorResult):
            if not verdict.valid:
                raise SemanticValidationError(
                    verdict.reason or "allocation validation failed", kind="allocation"
                )
        elif verdict is False:
            raise SemanticValidationError("allocation validation failed", kind="allocation")


class Usage(StrictModel):
    """Usage fields absent from a provider response remain unknown (None)."""

    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    provider_cache_tokens: int | None = Field(default=None, ge=0)
    reasoning_tokens: int | None = Field(default=None, ge=0)


class FailureCode(StrEnum):
    DISABLED = "disabled"
    MISSING_CREDENTIAL = "missing_credential"
    BUDGET_UNAVAILABLE = "budget_unavailable"
    TIMEOUT = "timeout"
    TRANSPORT_ERROR = "transport_error"
    RATE_LIMITED = "rate_limited"
    SERVER_ERROR = "server_error"
    BAD_REQUEST = "bad_request"
    UNAUTHORIZED = "unauthorized"
    PAYMENT_REQUIRED = "payment_required"
    FORBIDDEN = "forbidden"
    NOT_FOUND = "not_found"
    UNPROCESSABLE = "unprocessable"
    HTTP_ERROR = "http_error"
    INVALID_RESPONSE = "invalid_response"
    INVALID_CITATION = "invalid_citation"
    INVALID_ALLOCATION = "invalid_allocation"


class InterpretationFailure(StrictModel):
    """Safe telemetry for an unavailable interpretation; no response body is stored."""

    code: FailureCode
    message: str = Field(min_length=1, max_length=300)
    retryable: bool
    http_status: int | None = Field(default=None, ge=100, le=599)
    attempt: int = Field(ge=1)
    retry_number: int = Field(ge=0)
    requested_model: str = Field(min_length=1, max_length=200)
    response_model: str | None = Field(default=None, max_length=200)
    usage: Usage = Field(default_factory=Usage)
    latency_ms: float = Field(ge=0)
    reservation_state: Literal["not_reserved", "reserved", "released", "unknown", "reconciled"] = (
        "not_reserved"
    )


class AttemptTelemetry(StrictModel):
    """One reservation/network attempt, including retry number and observed usage."""

    attempt: int = Field(ge=1)
    retry_number: int = Field(ge=0)
    requested_model: str = Field(min_length=1, max_length=200)
    response_model: str | None = Field(default=None, max_length=200)
    usage: Usage = Field(default_factory=Usage)
    latency_ms: float = Field(ge=0)
    http_status: int | None = Field(default=None, ge=100, le=599)
    failure_code: FailureCode | None = None
    reservation_state: Literal["not_reserved", "reserved", "released", "unknown", "reconciled"] = (
        "not_reserved"
    )


class AllocationValidatorResult(StrictModel):
    """Optional typed return shape for an injected deterministic validator."""

    valid: bool
    reason: str = Field(default="", max_length=300)


# Compatibility names for callers that describe imported records as facts.
InvoiceCandidate = InvoiceObservation
CreditCandidate = CreditObservation


__all__ = [
    "AllocationValidatorResult",
    "AttemptTelemetry",
    "Citation",
    "CreditCandidate",
    "Decision",
    "AllocationLine",
    "CandidateAllocation",
    "CreditAllocationLine",
    "CreditObservation",
    "FailureCode",
    "InterpretationFailure",
    "InterpretationRequest",
    "InterpretationResult",
    "InvoiceCandidate",
    "InvoiceObservation",
    "PaymentObservation",
    "PROMPT_VERSION",
    "RankedCandidate",
    "ReasonCode",
    "SCHEMA_VERSION",
    "SourceSpan",
    "StrictModel",
    "Usage",
    "validate_result",
]
