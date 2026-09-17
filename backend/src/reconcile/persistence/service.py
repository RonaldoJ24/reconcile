from __future__ import annotations

import hashlib
import itertools
import json
import uuid
from collections import defaultdict
from copy import deepcopy
from time import perf_counter
from typing import Any

from sqlalchemy import func, literal, or_, select
from sqlalchemy.orm import Session

from reconcile.domain.matching import _mentions, propose, validate_allocation
from reconcile.domain.types import (
    CashLine,
    CreditFact,
    CreditLine,
    Evidence,
    InvoiceFact,
    PaymentFact,
    ProposalResult,
    ProposalStatus,
)
from reconcile.ingest.parsers import (
    LIMITS,
    ParsedBatch,
    parse_credit_source,
    parse_csv_source,
)
from reconcile.ml.runtime import ACTIVE_RULES_IDENTITY, runtime_mode

from .models import (
    ApplicationGroup,
    AuditEvent,
    CashApplication,
    CreditApplication,
    CreditNote,
    IdempotencyKey,
    ImportBatch,
    Invoice,
    Job,
    Payment,
    Proposal,
    ProposalRevision,
    Source,
    Workspace,
    now_utc,
)


class ServiceError(Exception):
    def __init__(self, code: str, message: str, status: int = 409):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def _hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def _payment_fact(row: Payment) -> PaymentFact:
    return PaymentFact(
        row.source_account_id,
        row.transaction_id,
        row.booking_date,
        row.payer_name,
        row.reference,
        row.amount,
        row.customer_id,
        str(row.source_id),
        row.version,
    )


def _invoice_fact(row: Invoice) -> InvoiceFact:
    return InvoiceFact(
        row.customer_id,
        row.invoice_id,
        row.customer_name,
        row.issued_date,
        row.due_date,
        row.balance_as_of,
        row.outstanding_amount,
        row.version,
    )


def _credit_fact(row: CreditNote) -> CreditFact:
    return CreditFact(
        row.customer_id,
        row.credit_note_id,
        row.balance_as_of,
        row.available_amount,
        row.invoice_id,
        row.version,
    )


def _result_dict(result: ProposalResult) -> dict[str, Any]:
    return {
        "cash": [{"invoice_id": line.invoice_id, "amount": line.amount} for line in result.cash],
        "credits": [
            {
                "credit_note_id": line.credit_note_id,
                "invoice_id": line.invoice_id,
                "amount": line.amount,
            }
            for line in result.credits
        ],
        "evidence": [
            {"source_id": e.source_id, "start": e.start, "end": e.end, "quote": e.quote}
            for e in result.evidence
        ],
        "alternatives": [list(a) for a in result.alternatives],
        "signals": list(result.signals),
        "reason": result.reason,
        "status": result.status.value,
    }


def _token(
    payment: Payment, invoices: list[Invoice], credits: list[CreditNote], result: ProposalResult
) -> str:
    return _hash(
        {
            "payment": [str(payment.id), payment.version, str(payment.source_id)],
            "invoices": sorted([[i.invoice_id, i.version, str(i.source_id)] for i in invoices]),
            "credits": sorted([[c.credit_note_id, c.version, str(c.source_id)] for c in credits]),
            "result": _result_dict(result),
        }
    )


def _revision_from_result(
    proposal_id: uuid.UUID,
    number: int,
    result: ProposalResult,
    token: str,
    *,
    provenance: str = "rules-v2-conservative",
    model_trace: dict[str, object] | None = None,
    reviewer: str | None = None,
) -> ProposalRevision:
    return ProposalRevision(
        proposal_id=proposal_id,
        revision=number,
        cash_lines=[{"invoice_id": line.invoice_id, "amount": line.amount} for line in result.cash],
        credit_lines=[
            {
                "credit_note_id": line.credit_note_id,
                "invoice_id": line.invoice_id,
                "amount": line.amount,
            }
            for line in result.credits
        ],
        evidence=[
            {"source_id": e.source_id, "start": e.start, "end": e.end, "quote": e.quote}
            for e in result.evidence
        ],
        alternatives=[list(a) for a in result.alternatives],
        signals=list(result.signals),
        reason=result.reason,
        status=result.status.value,
        version_token=token,
        provenance=provenance,
        model_trace=model_trace or {},
        reviewer=reviewer,
    )


def _identifier_mentioned(identifier: str, text: str) -> bool:
    return _mentions(identifier, text)


def _shadow_group(
    payment: Payment,
    invoices: list[Invoice],
    credits: list[CreditNote],
    evidence: dict[str, str],
    rules_result: ProposalResult,
) -> dict[str, Any]:
    """Build bounded decision-time candidates without changing the rules result."""

    text = " ".join((payment.reference, *evidence.values()))
    eligible = [
        invoice
        for invoice in invoices
        if invoice.balance_as_of <= payment.booking_date
        and invoice.currency == payment.currency
        and (payment.customer_id is None or invoice.customer_id == payment.customer_id)
    ]
    eligible.sort(
        key=lambda invoice: (
            not _identifier_mentioned(invoice.invoice_id, text),
            invoice.outstanding_amount != payment.amount,
            abs((invoice.due_date - payment.booking_date).days),
            invoice.invoice_id,
        )
    )
    retrieval_truncated = len(eligible) > 10
    eligible = eligible[:10]
    eligible_ids = {invoice.invoice_id for invoice in eligible}
    eligible_by_id = {invoice.invoice_id: invoice for invoice in eligible}
    eligible_credit = next(
        (
            credit
            for credit in credits
            if credit.invoice_id in eligible_ids
            and credit.available_amount <= eligible_by_id[credit.invoice_id].outstanding_amount
            and credit.balance_as_of <= payment.booking_date
            and credit.currency == payment.currency
            and _identifier_mentioned(credit.credit_note_id, text)
        ),
        None,
    )

    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add_candidate(cash: list[dict[str, object]], credit_lines: list[dict[str, object]]) -> None:
        invoice_ids = sorted(
            {str(line["invoice_id"]) for line in [*cash, *credit_lines] if line.get("invoice_id")}
        )
        if not 1 <= len(invoice_ids) <= 3 or not set(invoice_ids) <= eligible_ids:
            return
        allocation = {"invoice_ids": invoice_ids, "cash": cash, "credits": credit_lines}
        digest = _hash(allocation)
        if digest in seen:
            return
        seen.add(digest)
        candidates.append({"candidate_id": f"online-{digest[:16]}", **allocation})

    if rules_result.status == ProposalStatus.PROPOSED:
        add_candidate(
            [{"invoice_id": line.invoice_id, "amount": line.amount} for line in rules_result.cash],
            [
                {
                    "credit_note_id": line.credit_note_id,
                    "invoice_id": line.invoice_id,
                    "amount": line.amount,
                }
                for line in rules_result.credits
            ],
        )

    for invoice in eligible:
        amount = min(payment.amount, invoice.outstanding_amount)
        if amount > 0:
            add_candidate([{"invoice_id": invoice.invoice_id, "amount": amount}], [])

    for size in (2, 3):
        for group in itertools.combinations(eligible, size):
            credit_amount = (
                eligible_credit.available_amount
                if eligible_credit is not None
                and any(invoice.invoice_id == eligible_credit.invoice_id for invoice in group)
                else 0
            )
            group_total = sum(invoice.outstanding_amount for invoice in group) - credit_amount
            if group_total != payment.amount:
                continue
            cash: list[dict[str, object]] = []
            for invoice in group:
                amount = invoice.outstanding_amount - (
                    credit_amount
                    if eligible_credit is not None
                    and invoice.invoice_id == eligible_credit.invoice_id
                    else 0
                )
                if amount:
                    cash.append({"invoice_id": invoice.invoice_id, "amount": amount})
            credit_lines: list[dict[str, object]] = (
                [
                    {
                        "credit_note_id": eligible_credit.credit_note_id,
                        "invoice_id": eligible_credit.invoice_id,
                        "amount": credit_amount,
                    }
                ]
                if eligible_credit is not None and credit_amount
                else []
            )
            add_candidate(cash, credit_lines)

    return {
        "group_id": str(payment.id),
        "payment": {
            "booking_date": payment.booking_date.isoformat(),
            "payer_name": payment.payer_name,
            "reference": payment.reference,
            "amount": payment.amount,
            "currency": payment.currency,
            "customer_id": payment.customer_id,
        },
        "invoices": [
            {
                "customer_id": invoice.customer_id,
                "customer_name": invoice.customer_name,
                "invoice_id": invoice.invoice_id,
                "issued_date": invoice.issued_date.isoformat(),
                "due_date": invoice.due_date.isoformat(),
                "balance_as_of": invoice.balance_as_of.isoformat(),
                "outstanding_amount": invoice.outstanding_amount,
                "currency": invoice.currency,
            }
            for invoice in eligible
        ],
        "credit": (
            {
                "customer_id": eligible_credit.customer_id,
                "credit_note_id": eligible_credit.credit_note_id,
                "balance_as_of": eligible_credit.balance_as_of.isoformat(),
                "available_amount": eligible_credit.available_amount,
                "currency": eligible_credit.currency,
                "invoice_id": eligible_credit.invoice_id,
            }
            if eligible_credit is not None
            else None
        ),
        "message": "\n".join(evidence.values()),
        "candidates": candidates[:10],
        "retrieval_truncated": retrieval_truncated or len(candidates) > 10,
    }


def _shadow_model_trace(
    payment: Payment,
    invoices: list[Invoice],
    credits: list[CreditNote],
    evidence: dict[str, str],
    rules_result: ProposalResult,
    group: dict[str, Any] | None = None,
) -> dict[str, object]:
    from reconcile.ml.artifact import ArtifactError
    from reconcile.ml.runtime import rank_candidates, runtime_mode

    if runtime_mode() != "shadow":
        return {}
    group = group or _shadow_group(payment, invoices, credits, evidence, rules_result)
    try:
        ranker = rank_candidates(group)
        ranker["status"] = "observed" if ranker.get("model_id") else "no_candidates"
    except ArtifactError:
        ranker = {
            "mode": "shadow",
            "status": "artifact_unavailable",
            "model_id": None,
            "model_version": None,
        }
    except (ArithmeticError, TypeError, ValueError):
        ranker = {
            "mode": "shadow",
            "status": "prediction_failed",
            "model_id": None,
            "model_version": None,
        }
    ranker["retrieval_truncated"] = group["retrieval_truncated"]
    ranker["candidate_count"] = len(group["candidates"])
    return {"ranker": ranker}


def _trace_stage(
    stage_id: str,
    name: str,
    status: str,
    summary: str,
    duration_ms: float | None,
    *,
    details: dict[str, Any] | None = None,
    evidence: list[str] | None = None,
) -> dict[str, object]:
    stage: dict[str, object] = {
        "id": stage_id,
        "name": name,
        "status": status,
        "summary": summary,
        "duration_ms": round(duration_ms, 3) if duration_ms is not None else None,
    }
    if details:
        stage["details"] = details
    if evidence:
        stage["evidence"] = evidence
    return stage


def _source_snapshot(source: Source) -> dict[str, object]:
    return {
        "source_id": str(source.id),
        "kind": source.kind,
        "sha256": source.sha256,
        "bytes": len(source.raw_bytes),
        "status": source.status,
        "version": source.source_metadata.get("version", 1),
        "case_id": source.source_metadata.get("case_id"),
        "scenario_version": source.source_metadata.get("scenario_version"),
        "parse": {
            "accepted_count": source.accepted_count,
            "rejected_count": source.rejected_count,
            "issues": source.source_metadata.get("issues", [])[:20],
        },
        "association": {
            key: source.source_metadata.get(key)
            for key in (
                "message_time",
                "payment_source_account_id",
                "payment_transaction_id",
            )
            if key in source.source_metadata
        },
        "batch_id": str(source.batch_id),
    }


def _trace_snapshot(
    payment: Payment,
    invoices: list[Invoice],
    credits: list[CreditNote],
    sources: list[Source],
    evidence: dict[str, str],
    group: dict[str, Any],
) -> dict[str, object]:
    return {
        "rules_identity": ACTIVE_RULES_IDENTITY,
        "payment": {
            "id": str(payment.id),
            "source_id": str(payment.source_id),
            "source_account_id": payment.source_account_id,
            "transaction_id": payment.transaction_id,
            "booking_date": payment.booking_date.isoformat(),
            "payer_name": payment.payer_name,
            "reference": payment.reference,
            "amount": payment.amount,
            "currency": payment.currency,
            "customer_id": payment.customer_id,
            "version": payment.version,
        },
        "invoices": [
            {
                "id": str(invoice.id),
                "source_id": str(invoice.source_id),
                "customer_id": invoice.customer_id,
                "invoice_id": invoice.invoice_id,
                "issued_date": invoice.issued_date.isoformat(),
                "due_date": invoice.due_date.isoformat(),
                "balance_as_of": invoice.balance_as_of.isoformat(),
                "outstanding_amount": invoice.outstanding_amount,
                "currency": invoice.currency,
                "version": invoice.version,
            }
            for invoice in invoices
        ],
        "credits": [
            {
                "id": str(credit.id),
                "source_id": str(credit.source_id),
                "customer_id": credit.customer_id,
                "credit_note_id": credit.credit_note_id,
                "balance_as_of": credit.balance_as_of.isoformat(),
                "available_amount": credit.available_amount,
                "invoice_id": credit.invoice_id,
                "currency": credit.currency,
                "version": credit.version,
            }
            for credit in credits
        ],
        "sources": [_source_snapshot(source) for source in sources],
        "evidence": dict(sorted(evidence.items())),
        "candidate_context": group,
    }


def _stable_trace_identity(snapshot: dict[str, object]) -> dict[str, object]:
    """Project a stored trace snapshot onto replayable input identity.

    The stored snapshot deliberately retains workspace-owned UUIDs for source
    and ownership checks.  Those values are excluded from the fingerprint so
    equivalent registered inputs in separate workspaces can share a cache or
    recording identity.
    """

    payment = snapshot.get("payment")
    invoices = snapshot.get("invoices")
    credits = snapshot.get("credits")
    sources = snapshot.get("sources")
    evidence = snapshot.get("evidence")
    candidate_context = snapshot.get("candidate_context")
    source_hashes: dict[str, str] = {}
    stable_sources: list[dict[str, object]] = []
    if isinstance(sources, list):
        for source in sources:
            if not isinstance(source, dict):
                continue
            source_id = source.get("source_id")
            source_hash = str(source.get("sha256", ""))
            if source_id is not None:
                source_hashes[str(source_id)] = source_hash
            stable_sources.append(
                {
                    "kind": source.get("kind"),
                    "sha256": source_hash,
                    "version": source.get("version", 1),
                    "case_id": source.get("case_id"),
                    "scenario_version": source.get("scenario_version"),
                }
            )
    stable_sources.sort(key=lambda item: (str(item.get("kind")), str(item.get("sha256"))))

    def natural(row: object, fields: tuple[str, ...]) -> dict[str, object]:
        if not isinstance(row, dict):
            return {}
        return {field: row.get(field) for field in fields}

    stable_evidence: dict[str, object] = {}
    if isinstance(evidence, dict):
        for source_id, text in sorted(evidence.items()):
            stable_evidence[source_hashes.get(str(source_id), str(source_id))] = text

    stable_candidates = candidate_context
    if isinstance(candidate_context, dict):
        stable_candidates = {
            key: value for key, value in candidate_context.items() if key != "group_id"
        }
        if "message" in stable_candidates:
            stable_candidates["message"] = "\n".join(
                str(value) for _, value in sorted(stable_evidence.items())
            )

    invoice_rows = invoices if isinstance(invoices, list) else []
    credit_rows = credits if isinstance(credits, list) else []

    return {
        "rules_identity": snapshot.get("rules_identity"),
        "payment": natural(
            payment,
            (
                "source_account_id",
                "transaction_id",
                "booking_date",
                "payer_name",
                "reference",
                "amount",
                "currency",
                "customer_id",
                "version",
            ),
        ),
        "invoices": sorted(
            (
                natural(
                    invoice,
                    (
                        "customer_id",
                        "invoice_id",
                        "issued_date",
                        "due_date",
                        "balance_as_of",
                        "outstanding_amount",
                        "currency",
                        "version",
                    ),
                )
                for invoice in invoice_rows
            ),
            key=lambda item: str(item.get("invoice_id")),
        ),
        "credits": sorted(
            (
                natural(
                    credit,
                    (
                        "customer_id",
                        "credit_note_id",
                        "balance_as_of",
                        "available_amount",
                        "invoice_id",
                        "currency",
                        "version",
                    ),
                )
                for credit in credit_rows
            ),
            key=lambda item: str(item.get("credit_note_id")),
        ),
        "sources": stable_sources,
        "evidence": stable_evidence,
        "candidate_context": stable_candidates,
    }


def _trace_with_history(
    model_trace: dict[str, Any], audits: list[AuditEvent]
) -> dict[str, object]:
    trace = deepcopy(model_trace)
    stages = list(trace.get("stages", []))
    for event in audits:
        if event.action == "application.apply":
            stages.append(
                _trace_stage(
                    "application-apply",
                    "Application",
                    "completed",
                    "A reviewer applied the persisted proposal revision.",
                    None,
                    details={"audit_event_id": str(event.id), **event.payload},
                )
            )
        elif event.action == "application.reverse":
            stages.append(
                _trace_stage(
                    "application-reverse",
                    "Reversal",
                    "completed",
                    "A reviewer reversed the persisted application.",
                    None,
                    details={"audit_event_id": str(event.id), **event.payload},
                )
            )
    trace["stages"] = stages
    return trace


class ReconcileService:
    def __init__(self, session: Session):
        self.session = session

    def create_workspace(self, mode: str = "local") -> Workspace:
        workspace = Workspace(mode=mode)
        self.session.add(workspace)
        self.session.flush()
        return workspace

    def _enqueue_match_job(self, workspace_id: uuid.UUID, payment_id: uuid.UUID) -> str | None:
        payment_key = str(payment_id)
        for existing in self.session.scalars(
            select(Job).where(
                Job.workspace_id == workspace_id,
                Job.kind == "match-payment",
                Job.status == "PENDING",
            )
        ):
            if existing.payload.get("payment_id") == payment_key:
                return None
        job = Job(
            workspace_id=workspace_id,
            kind="match-payment",
            payload={"payment_id": payment_key},
        )
        self.session.add(job)
        self.session.flush()
        return str(job.id)

    def validate_import(
        self, workspace_id: uuid.UUID, batch: ParsedBatch, profile: str
    ) -> ImportBatch:
        limits = LIMITS[profile]
        if (
            sum(
                len(source.raw)
                for source in (batch.bank, batch.invoices, batch.credits, batch.message)
                if source
            )
            > limits["batch"]
        ):
            raise ServiceError("quota", "batch exceeds byte limit", 413)
        entity_count = sum(
            len(source.rows) for source in (batch.bank, batch.invoices, batch.credits) if source
        )
        if entity_count > limits["entities"]:
            raise ServiceError("quota", "batch exceeds entity limit", 413)
        sources = (batch.bank, batch.invoices, batch.credits, batch.message)
        if batch.message is not None and batch.message_context is not None:
            existing_message = self.session.scalar(
                select(Source).where(
                    Source.workspace_id == workspace_id,
                    Source.kind == batch.message.kind,
                    Source.sha256 == batch.message.sha256,
                )
            )
            expected_metadata = {
                "message_time": batch.message_context.message_time.isoformat(),
                "payment_source_account_id": batch.message_context.payment_source_account_id,
                "payment_transaction_id": batch.message_context.payment_transaction_id,
            }
            if existing_message is not None and any(
                existing_message.source_metadata.get(key) != value
                for key, value in expected_metadata.items()
            ):
                raise ServiceError(
                    "source_conflict",
                    "message bytes are already associated with a different payment",
                    409,
                )
        current_workspace_bytes = int(
            self.session.scalar(
                select(func.coalesce(func.sum(func.length(Source.raw_bytes)), 0)).where(
                    Source.workspace_id == workspace_id
                )
            )
            or 0
        )
        new_bytes = 0
        for parsed in sources:
            if (
                parsed is not None
                and self.session.scalar(
                    select(Source.id).where(
                        Source.workspace_id == workspace_id,
                        Source.kind == parsed.kind,
                        Source.sha256 == parsed.sha256,
                    )
                )
                is None
            ):
                new_bytes += len(parsed.raw)
        if current_workspace_bytes + new_bytes > limits["workspace"]:
            raise ServiceError("quota", "workspace source retention limit exceeded", 413)
        if profile == "preview":
            global_bytes = int(
                self.session.scalar(
                    select(func.coalesce(func.sum(func.length(Source.raw_bytes)), 0))
                )
                or 0
            )
            if global_bytes + new_bytes > 50 * 1024 * 1024:
                raise ServiceError("quota", "global source retention limit exceeded", 413)
        record = ImportBatch(workspace_id=workspace_id, profile=profile, status="VALIDATED")
        self.session.add(record)
        self.session.flush()
        bound_sources: list[Source] = []
        for parsed in (batch.bank, batch.invoices, batch.credits, batch.message):
            if parsed is None:
                continue
            existing = self.session.scalar(
                select(Source).where(
                    Source.workspace_id == workspace_id,
                    Source.kind == parsed.kind,
                    Source.sha256 == parsed.sha256,
                )
            )
            if existing is None:
                metadata: dict[str, Any] = {}
                if batch.message_context and parsed.kind == "message":
                    metadata = {
                        "message_time": batch.message_context.message_time.isoformat(),
                        "payment_source_account_id": (
                            batch.message_context.payment_source_account_id
                        ),
                        "payment_transaction_id": batch.message_context.payment_transaction_id,
                    }
                if parsed.issues:
                    metadata["issues"] = [
                        {
                            "row": issue.row,
                            "field": issue.field,
                            "code": issue.code,
                            "message": issue.message,
                        }
                        for issue in parsed.issues
                    ]
                if parsed.row_locators:
                    metadata["row_locators"] = list(parsed.row_locators)
                existing = Source(
                    workspace_id=workspace_id,
                    batch_id=record.id,
                    kind=parsed.kind,
                    sha256=parsed.sha256,
                    raw_bytes=parsed.raw,
                    accepted_count=parsed.accepted_count,
                    rejected_count=parsed.rejected_count,
                    source_metadata=metadata,
                )
                self.session.add(existing)
            bound_sources.append(existing)
        self.session.flush()
        self.session.add(
            AuditEvent(
                workspace_id=workspace_id,
                action="import.validate",
                actor="system",
                entity_id=record.id,
                payload={"source_ids": [str(source.id) for source in bound_sources]},
            )
        )
        self.session.flush()
        return record

    def sources_for_batch(self, workspace_id: uuid.UUID, batch_id: uuid.UUID) -> list[Source]:
        """Return all immutable sources bound to one validation packet.

        A repeated validation may reuse a source already owned by an earlier
        batch. The validation audit preserves that association without moving
        the source row or rewriting its history.
        """

        sources = list(
            self.session.scalars(
                select(Source).where(
                    Source.workspace_id == workspace_id, Source.batch_id == batch_id
                )
            )
        )
        audit = self.session.scalar(
            select(AuditEvent)
            .where(
                AuditEvent.workspace_id == workspace_id,
                AuditEvent.action == "import.validate",
                AuditEvent.entity_id == batch_id,
            )
            .order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc())
        )
        if audit is None:
            return sources
        source_ids = [uuid.UUID(value) for value in audit.payload.get("source_ids", [])]
        bound = {
            source.id: source
            for source in self.session.scalars(
                select(Source).where(
                    Source.workspace_id == workspace_id, Source.id.in_(source_ids)
                )
            )
        }
        return [bound[source_id] for source_id in source_ids if source_id in bound]

    def _source_already_committed(self, source: Source) -> bool:
        """Recognize committed bytes even when their owning batch is legacy."""

        if source.status == "COMMITTED":
            return True
        return (
            self.session.scalar(
                select(ImportBatch.status).where(ImportBatch.id == source.batch_id)
            )
            == "COMMITTED"
        )

    def commit_import(
        self, workspace_id: uuid.UUID, batch_id: uuid.UUID, *, commit: bool = True
    ) -> dict[str, Any]:
        batch = self.session.scalar(
            select(ImportBatch)
            .where(ImportBatch.id == batch_id, ImportBatch.workspace_id == workspace_id)
            .with_for_update()
        )
        if batch is None:
            raise ServiceError("not_found", "import batch not found", 404)
        if batch.status == "COMMITTED":
            return {
                "batch_id": str(batch.id),
                "payments": 0,
                "invoices": 0,
                "credits": 0,
                "conflicts": [],
                "jobs": [],
            }
        sources = self.sources_for_batch(workspace_id, batch.id)
        by_kind = {s.kind: s for s in sources}
        source_committed = {
            source.id: self._source_already_committed(source) for source in sources
        }
        counts: dict[str, Any] = {"payments": 0, "invoices": 0, "credits": 0, "conflicts": []}
        jobs: list[str] = []
        created_payment_ids: set[uuid.UUID] = set()
        message_source = by_kind.get("message")
        message_is_new = bool(
            message_source is not None and not source_committed.get(message_source.id, False)
        )
        locked_message_payment: Payment | None = None
        locked_message_proposals: list[Proposal] = []
        if message_is_new and message_source is not None:
            metadata = message_source.source_metadata
            locked_message_payment = self.session.scalar(
                select(Payment).where(
                    Payment.workspace_id == workspace_id,
                    Payment.source_account_id == metadata.get("payment_source_account_id"),
                    Payment.transaction_id == metadata.get("payment_transaction_id"),
                )
            )
            if locked_message_payment is not None:
                # Apply locks Proposal -> Payment. Acquire the proposal locks before
                # any changed packet entity locks so a new message cannot deadlock.
                locked_message_proposals = self._lock_payment_proposals(
                    workspace_id, locked_message_payment.id
                )
        # Sources remain immutable; only accepted rows become business records.
        payment_source = by_kind.get("bank")
        if payment_source and not source_committed.get(payment_source.id, False):
            parsed = parse_csv_source("bank", payment_source.raw_bytes, profile=batch.profile)
            for row in parsed.rows:
                existing_payment = self.session.scalar(
                    select(Payment)
                    .where(
                        Payment.workspace_id == workspace_id,
                        Payment.source_account_id == row["source_account_id"],
                        Payment.transaction_id == row["transaction_id"],
                    )
                    .with_for_update()
                )
                if existing_payment:
                    if _payment_equal(existing_payment, row):
                        continue
                    payment_source.status = "REJECTED_CONFLICT"
                    payment_source.source_metadata = {
                        **payment_source.source_metadata,
                        "conflict": {"kind": "payment", "transaction_id": row["transaction_id"]},
                    }
                    existing_payment.conflicted = True
                    existing_payment.version += 1
                    self._stale_for_payment(workspace_id, existing_payment.id)
                    counts["conflicts"].append({"kind": "payment", "key": row["transaction_id"]})
                    continue
                payment = Payment(workspace_id=workspace_id, source_id=payment_source.id, **row)
                self.session.add(payment)
                self.session.flush()
                counts["payments"] += 1
                created_payment_ids.add(payment.id)
                job_id = self._enqueue_match_job(workspace_id, payment.id)
                if job_id is not None:
                    jobs.append(job_id)
        invoice_source = by_kind.get("invoice")
        if invoice_source and not source_committed.get(invoice_source.id, False):
            parsed = parse_csv_source("invoice", invoice_source.raw_bytes, profile=batch.profile)
            for row in parsed.rows:
                existing_invoice = self.session.scalar(
                    select(Invoice)
                    .where(
                        Invoice.workspace_id == workspace_id,
                        Invoice.customer_id == row["customer_id"],
                        Invoice.invoice_id == row["invoice_id"],
                    )
                    .with_for_update()
                )
                if existing_invoice:
                    if _invoice_equal(existing_invoice, row):
                        continue
                    existing_invoice.version += 1
                    existing_invoice.conflicted = True
                    invoice_source.status = "REJECTED_CONFLICT"
                    invoice_source.source_metadata = {
                        **invoice_source.source_metadata,
                        "conflict": {"kind": "invoice", "invoice_id": row["invoice_id"]},
                    }
                    counts["conflicts"].append({"kind": "invoice", "key": row["invoice_id"]})
                    self._stale_for_invoice(workspace_id, existing_invoice.invoice_id)
                    continue
                self.session.add(
                    Invoice(workspace_id=workspace_id, source_id=invoice_source.id, **row)
                )
                counts["invoices"] += 1
        credit_source = by_kind.get("credit")
        if credit_source and not source_committed.get(credit_source.id, False):
            parsed = parse_credit_source(credit_source.raw_bytes, profile=batch.profile)
            for row in parsed.rows:
                existing_credit = self.session.scalar(
                    select(CreditNote)
                    .where(
                        CreditNote.workspace_id == workspace_id,
                        CreditNote.customer_id == row["customer_id"],
                        CreditNote.credit_note_id == row["credit_note_id"],
                    )
                    .with_for_update()
                )
                if existing_credit:
                    if _credit_equal(existing_credit, row):
                        continue
                    existing_credit.version += 1
                    existing_credit.conflicted = True
                    credit_source.status = "REJECTED_CONFLICT"
                    credit_source.source_metadata = {
                        **credit_source.source_metadata,
                        "conflict": {"kind": "credit", "credit_note_id": row["credit_note_id"]},
                    }
                    counts["conflicts"].append({"kind": "credit", "key": row["credit_note_id"]})
                    self._stale_for_credit(workspace_id, existing_credit.credit_note_id)
                    continue
                self.session.add(
                    CreditNote(workspace_id=workspace_id, source_id=credit_source.id, **row)
                )
                counts["credits"] += 1
        self.session.flush()
        # A message association is context, never an inferred filename relationship.
        if message_is_new and message_source is not None:
            owner_batch_status = self.session.scalar(
                select(ImportBatch.status).where(ImportBatch.id == message_source.batch_id)
            )
            was_committed = source_committed.get(message_source.id, False) or (
                owner_batch_status == "COMMITTED"
            )
            message_source.status = "COMMITTED"
            metadata = message_source.source_metadata
            associated_payment = self.session.scalar(
                select(Payment).where(
                    Payment.workspace_id == workspace_id,
                    Payment.source_account_id == metadata.get("payment_source_account_id"),
                    Payment.transaction_id == metadata.get("payment_transaction_id"),
                )
            )
            payment_proposals = locked_message_proposals
            if associated_payment and not payment_proposals:
                payment_proposals = self._lock_payment_proposals(
                    workspace_id, associated_payment.id
                )
            if associated_payment:
                self.session.refresh(
                    associated_payment,
                    with_for_update=True,
                )
            if (
                associated_payment
                and associated_payment.id not in created_payment_ids
                and not was_committed
            ):
                # New evidence changes the payment snapshot. Pending decisions are
                # stale and an applied decision remains immutable but needs review.
                associated_payment.version += 1
                self._stale_for_payment(
                    workspace_id, associated_payment.id, proposals=payment_proposals
                )
            if associated_payment and not was_committed:
                job_id = self._enqueue_match_job(workspace_id, associated_payment.id)
                if job_id is not None:
                    jobs.append(job_id)
        for source in sources:
            if source.status == "VALIDATED":
                source.status = "COMMITTED"
        batch.status = "COMMITTED"
        if commit:
            self.session.commit()
        return {"batch_id": str(batch.id), **counts, "jobs": jobs}

    def reactivate_message_variant(
        self,
        workspace_id: uuid.UUID,
        payment_id: uuid.UUID,
        source_id: uuid.UUID,
        variant: str,
        *,
        commit: bool = True,
    ) -> list[str]:
        """Reactivate an immutable, previously superseded message source."""

        proposals = self._lock_payment_proposals(workspace_id, payment_id)
        payment = self.session.scalar(
            select(Payment)
            .where(Payment.id == payment_id, Payment.workspace_id == workspace_id)
            .with_for_update()
        )
        source = self.session.scalar(
            select(Source)
            .where(Source.id == source_id, Source.workspace_id == workspace_id)
            .with_for_update()
        )
        if payment is None or source is None:
            raise ServiceError("not_found", "variant source or payment not found", 404)
        if source.status != "SUPERSEDED":
            return []
        source.status = "COMMITTED"
        source.source_metadata = {
            **source.source_metadata,
            "variant": variant,
            "active": True,
        }
        payment.version += 1
        self._stale_for_payment(workspace_id, payment.id, proposals=proposals)
        jobs: list[str] = []
        job_id = self._enqueue_match_job(workspace_id, payment.id)
        if job_id is not None:
            jobs.append(job_id)
        if commit:
            self.session.commit()
        return jobs

    def process_match(self, workspace_id: uuid.UUID, payment_id: uuid.UUID) -> Proposal:
        payment = self.session.scalar(
            select(Payment).where(Payment.id == payment_id, Payment.workspace_id == workspace_id)
        )
        if payment is None:
            raise ServiceError("not_found", "payment not found", 404)
        existing_proposal = self.session.scalar(
            select(Proposal)
            .where(Proposal.workspace_id == workspace_id, Proposal.payment_id == payment.id)
            .order_by(Proposal.created_at.desc())
            .with_for_update()
        )
        if existing_proposal and existing_proposal.status in (
            ProposalStatus.APPLIED.value,
            ProposalStatus.REVERSED.value,
        ):
            # Source changes flag applied evidence for review; they never rewrite
            # the applied or reversed revision when a queued rematch runs.
            return existing_proposal
        evidence: dict[str, str] = {}
        message_sources: list[Source] = []
        for source in self.session.scalars(
            select(Source)
            .join(ImportBatch, Source.batch_id == ImportBatch.id)
            .where(
                Source.workspace_id == workspace_id,
                Source.kind == "message",
                or_(ImportBatch.status == "COMMITTED", Source.status == "COMMITTED"),
                Source.status != "REJECTED_CONFLICT",
                Source.status != "SUPERSEDED",
            )
        ):
            if (
                source.source_metadata.get("payment_source_account_id") == payment.source_account_id
                and source.source_metadata.get("payment_transaction_id") == payment.transaction_id
            ):
                evidence[str(source.id)] = source.raw_bytes.decode("utf-8")
                message_sources.append(source)
        payment_source = self.session.get(Source, payment.source_id)
        if payment_source is None:
            raise ServiceError("stale_source", "payment source is unavailable")
        case_id = payment_source.source_metadata.get("case_id")

        def scoped_source_ids(kind: str) -> set[uuid.UUID]:
            rows = self.session.scalars(
                select(Source)
                .join(ImportBatch, Source.batch_id == ImportBatch.id)
                .where(
                    Source.workspace_id == workspace_id,
                    Source.kind == kind,
                    Source.status != "REJECTED_CONFLICT",
                    Source.status != "SUPERSEDED",
                    or_(ImportBatch.status == "COMMITTED", Source.status == "COMMITTED"),
                )
            )
            if case_id is None:
                return {source.id for source in rows}
            return {
                source.id
                for source in rows
                if source.source_metadata.get("case_id") == case_id
            }

        invoice_source_ids = scoped_source_ids("invoice")
        credit_source_ids = scoped_source_ids("credit")
        all_text = " ".join((payment.reference, *evidence.values())).lower()
        invoice_filters = [
            Invoice.workspace_id == workspace_id,
            Invoice.outstanding_amount > 0,
            Invoice.balance_as_of <= payment.booking_date,
            Invoice.currency == payment.currency,
            Invoice.conflicted.is_(False),
        ]
        if payment.customer_id is not None:
            invoice_filters.append(Invoice.customer_id == payment.customer_id)
        if case_id is not None:
            invoice_filters.append(Invoice.source_id.in_(invoice_source_ids))

        # PostgreSQL performs the broad containment filter; the domain boundary matcher
        # below rejects substrings before they can become evidence.
        mentioned_rows = list(
            self.session.scalars(
                select(Invoice)
                .where(
                    *invoice_filters,
                    func.strpos(literal(all_text), func.lower(Invoice.invoice_id)) > 0,
                )
                .order_by(Invoice.invoice_id)
                .limit(40)
            )
        )
        invoices = [
            row for row in mentioned_rows if _identifier_mentioned(row.invoice_id, all_text)
        ][:10]
        invoice_keys = {(row.customer_id, row.invoice_id) for row in invoices}
        if len(invoices) < 10:
            exact_rows = self.session.scalars(
                select(Invoice)
                .where(*invoice_filters, Invoice.outstanding_amount == payment.amount)
                .order_by(Invoice.invoice_id)
                .limit(10)
            )
            for row in exact_rows:
                key = (row.customer_id, row.invoice_id)
                if key not in invoice_keys:
                    invoices.append(row)
                    invoice_keys.add(key)
                if len(invoices) == 10:
                    break

        selected_invoice_ids = {row.invoice_id for row in invoices}
        credit_filters = [
            CreditNote.workspace_id == workspace_id,
            CreditNote.available_amount > 0,
            CreditNote.balance_as_of <= payment.booking_date,
            CreditNote.currency == payment.currency,
            CreditNote.conflicted.is_(False),
            CreditNote.invoice_id.in_(selected_invoice_ids),
        ]
        if payment.customer_id is not None:
            credit_filters.append(CreditNote.customer_id == payment.customer_id)
        if case_id is not None:
            credit_filters.append(CreditNote.source_id.in_(credit_source_ids))
        credits = (
            [
                row
                for row in self.session.scalars(
                    select(CreditNote)
                    .where(
                        *credit_filters,
                        func.strpos(literal(all_text), func.lower(CreditNote.credit_note_id)) > 0,
                    )
                    .order_by(CreditNote.credit_note_id)
                    .limit(10)
                )
                if _identifier_mentioned(row.credit_note_id, all_text)
            ]
            if selected_invoice_ids
            else []
        )
        rules_started = perf_counter()
        result = propose(
            _payment_fact(payment),
            [_invoice_fact(i) for i in invoices],
            [_credit_fact(c) for c in credits],
            evidence,
        )
        decision_result = result
        rules_duration = (perf_counter() - rules_started) * 1000
        candidate_started = perf_counter()
        candidate_group = _shadow_group(payment, invoices, credits, evidence, result)
        candidate_duration = (perf_counter() - candidate_started) * 1000
        source_rows: dict[uuid.UUID, Source] = {}
        for source_id in [
            payment.source_id,
            *(invoice.source_id for invoice in invoices),
            *(credit.source_id for credit in credits),
            *(source.id for source in message_sources),
        ]:
            source_row = self.session.get(Source, source_id)
            if source_row is not None:
                source_rows[source_row.id] = source_row
        snapshot = _trace_snapshot(
            payment,
            invoices,
            credits,
            list(source_rows.values()),
            evidence,
            candidate_group,
        )
        input_fingerprint = _hash(_stable_trace_identity(snapshot))
        stages: list[dict[str, object]] = [
            _trace_stage(
                "input-snapshot",
                "Input snapshot",
                "completed",
                "Captured the bounded payment, source, candidate, and evidence snapshot.",
                None,
                details={
                    "payment_id": str(payment.id),
                    "invoice_count": len(invoices),
                    "credit_count": len(credits),
                    "source_count": len(source_rows),
                },
                evidence=sorted(evidence),
            ),
            _trace_stage(
                "parse-observations",
                "Parse observations",
                "completed",
                (
                    "Recorded parser acceptance, rejection, and issue observations "
                    "from persisted sources."
                ),
                None,
                details={
                    "sources": [
                        {
                            "source_id": str(source.id),
                            "kind": source.kind,
                            "accepted": source.accepted_count,
                            "rejected": source.rejected_count,
                            "issues": source.source_metadata.get("issues", [])[:20],
                        }
                        for source in sorted(source_rows.values(), key=lambda item: str(item.id))
                    ]
                },
                evidence=sorted(evidence),
            ),
            _trace_stage(
                "candidate-generation",
                "Candidate generation",
                "completed",
                "Generated bounded candidates from the persisted snapshot.",
                candidate_duration,
                details={
                    "candidate_count": len(candidate_group["candidates"]),
                    "retrieval_truncated": candidate_group["retrieval_truncated"],
                },
                evidence=sorted(evidence),
            ),
            _trace_stage(
                "rules-decision",
                "Deterministic rules",
                "completed",
                "Evaluated the active conservative rules against the snapshot.",
                rules_duration,
                details={"result": _result_dict(result)},
                evidence=sorted({item.source_id for item in result.evidence}),
            ),
        ]
        ranker_trace: dict[str, object] = {}
        if runtime_mode() == "shadow":
            ranker_started = perf_counter()
            ranker_trace = _shadow_model_trace(
                payment, invoices, credits, evidence, result, candidate_group
            )
            ranker_duration = (perf_counter() - ranker_started) * 1000
            ranker_value = ranker_trace.get("ranker", {})
            ranker = ranker_value if isinstance(ranker_value, dict) else {}
            ranker_status = str(ranker.get("status", "completed"))
            stages.append(
                _trace_stage(
                    "shadow-ranker",
                    "Shadow ranker",
                    (
                        "completed"
                        if ranker_status in {"observed", "no_candidates"}
                        else ranker_status
                    ),
                    "Observed the existing shadow ranker without changing financial authority.",
                    ranker_duration,
                    details=ranker,
                    evidence=sorted(evidence),
                )
            )
        else:
            stages.append(
                _trace_stage(
                    "provider-routing",
                    "Provider routing",
                    "skipped",
                    "Provider inference was skipped for the deterministic rules execution.",
                    None,
                    details={"reason": "active engine is deterministic rules"},
                )
            )
        if result.status == ProposalStatus.PROPOSED:
            validation_started = perf_counter()
            try:
                validate_allocation(
                    payment.amount,
                    {invoice.invoice_id: _invoice_fact(invoice) for invoice in invoices},
                    {credit.credit_note_id: _credit_fact(credit) for credit in credits},
                    result.cash,
                    result.credits,
                )
                validation_status = "completed"
                validation_summary = "Validated the proposed allocation with the shared validator."
                validation_details: dict[str, object] = {
                    "result": "valid",
                    "checks": ["opening_snapshot", "structural_allocation"],
                    "transactional_live_balance": "not_executed",
                }
            except (ValueError, KeyError) as exc:
                validation_status = "failed"
                validation_summary = "The shared validator rejected the proposed allocation."
                validation_details = {
                    "result": "invalid",
                    "reason": str(exc),
                    "checks": ["opening_snapshot", "structural_allocation"],
                    "transactional_live_balance": "not_executed",
                }
                decision_result = ProposalResult(
                    ProposalStatus.NEEDS_REVIEW,
                    (),
                    (),
                    result.evidence,
                    result.alternatives,
                    (*result.signals, "financial_validation_failed"),
                    "shared validator rejected the rules proposal",
                )
            stages.append(
                _trace_stage(
                    "financial-validation",
                    "Financial validation",
                    validation_status,
                    validation_summary,
                    (perf_counter() - validation_started) * 1000,
                    details=validation_details,
                )
            )
        else:
            stages.append(
                _trace_stage(
                    "financial-validation",
                    "Financial validation",
                    "skipped",
                    "No proposed allocation was available for financial validation.",
                    None,
                    details={"reason": "rules deferred or rejected"},
                )
            )
        stages.append(
            _trace_stage(
                "decision-result",
                "Decision result",
                "completed",
                "Recorded the deterministic rules result for review.",
                None,
                details={
                    "status": decision_result.status.value,
                    "reason": decision_result.reason,
                },
                evidence=sorted({item.source_id for item in decision_result.evidence}),
            )
        )
        model_trace: dict[str, object] = {
            "schema_version": "decision-trace-v1",
            "source": "rules",
            "rules_identity": ACTIVE_RULES_IDENTITY,
            "input_fingerprint": input_fingerprint,
            "final_status": decision_result.status.value,
            "snapshot": snapshot,
            "stages": stages,
            **ranker_trace,
        }
        proposal = existing_proposal
        if proposal is None:
            proposal = Proposal(
                workspace_id=workspace_id,
                payment_id=payment.id,
                status=decision_result.status.value,
                current_revision=1,
            )
            self.session.add(proposal)
            self.session.flush()
            proposal_revision = 1
        else:
            proposal.current_revision += 1
            proposal.status = decision_result.status.value
            proposal_revision = proposal.current_revision
        selected_ids = {line.invoice_id for line in decision_result.cash} | {
            line.invoice_id for line in decision_result.credits
        }
        selected_invoices = [i for i in invoices if i.invoice_id in selected_ids]
        selected_credits = [
            c
            for c in credits
            if c.credit_note_id in {line.credit_note_id for line in decision_result.credits}
        ]
        revision = _revision_from_result(
            proposal.id,
            proposal_revision,
            decision_result,
            _token(payment, selected_invoices, selected_credits, decision_result),
            model_trace=model_trace,
        )
        self.session.add(revision)
        self.session.commit()
        return proposal

    def record_interpretation(
        self,
        workspace_id: uuid.UUID,
        proposal_id: uuid.UUID,
        *,
        expected_revision: int,
        mode: str,
        source: str,
        candidate: dict[str, Any] | None,
        citations: list[dict[str, Any]],
        reason_code: str,
        trace: dict[str, object],
        expected_source_hashes: dict[str, str],
        expected_payment_version: int,
        expected_invoice_versions: dict[str, int],
        expected_credit_versions: dict[str, int],
    ) -> Proposal:
        if mode not in {"direct", "hybrid"} or source not in {"live", "cache"}:
            raise ServiceError("validation", "invalid interpretation provenance", 422)
        proposal = self.session.scalar(
            select(Proposal)
            .where(Proposal.id == proposal_id, Proposal.workspace_id == workspace_id)
            .with_for_update()
        )
        if proposal is None:
            raise ServiceError("not_found", "proposal not found", 404)
        if proposal.current_revision != expected_revision:
            raise ServiceError("stale_revision", "proposal changed during interpretation")
        if proposal.status != ProposalStatus.NEEDS_REVIEW.value:
            raise ServiceError(
                "interpretation_not_allowed", "only review-required proposals can be interpreted"
            )
        payment = self.session.scalar(
            select(Payment).where(
                Payment.id == proposal.payment_id,
                Payment.workspace_id == workspace_id,
            )
        )
        if payment is None or payment.version != expected_payment_version or payment.conflicted:
            raise ServiceError("stale_version", "payment changed during interpretation")
        source_ids = [uuid.UUID(value) for value in expected_source_hashes]
        current_sources = list(
            self.session.scalars(
                select(Source).where(
                    Source.workspace_id == workspace_id,
                    Source.id.in_(source_ids),
                )
            )
        )
        current_hashes = {str(item.id): item.sha256 for item in current_sources}
        if current_hashes != expected_source_hashes:
            raise ServiceError("stale_source", "source evidence changed during interpretation")

        cash = [
            CashLine(str(item["invoice_id"]), int(item["amount_centavos"]))
            for item in (candidate or {}).get("cash", [])
        ]
        credits = [
            CreditLine(
                str(item["credit_note_id"]),
                str(item["invoice_id"]),
                int(item["amount_centavos"]),
            )
            for item in (candidate or {}).get("credits", [])
        ]
        invoice_ids = {item.invoice_id for item in cash} | {item.invoice_id for item in credits}
        credit_ids = {item.credit_note_id for item in credits}
        invoices = list(
            self.session.scalars(
                select(Invoice).where(
                    Invoice.workspace_id == workspace_id,
                    Invoice.invoice_id.in_(invoice_ids),
                )
            )
        )
        credit_rows = list(
            self.session.scalars(
                select(CreditNote).where(
                    CreditNote.workspace_id == workspace_id,
                    CreditNote.credit_note_id.in_(credit_ids),
                )
            )
        )
        if any(
            expected_invoice_versions.get(item.invoice_id) != item.version or item.conflicted
            for item in invoices
        ) or len(invoices) != len(invoice_ids):
            raise ServiceError("stale_balance", "invoice balance changed during interpretation")
        if any(
            expected_credit_versions.get(item.credit_note_id) != item.version or item.conflicted
            for item in credit_rows
        ) or len(credit_rows) != len(credit_ids):
            raise ServiceError("stale_balance", "credit balance changed during interpretation")

        evidence = tuple(
            Evidence(
                str(item["source_id"]),
                int(item["start"]),
                int(item["end"]),
                str(item["quote"]),
            )
            for item in citations
        )
        if candidate is None:
            result = ProposalResult(
                ProposalStatus.NEEDS_REVIEW,
                evidence=evidence,
                reason=f"bounded interpretation: {reason_code}",
            )
        else:
            try:
                validate_allocation(
                    payment.amount,
                    {item.invoice_id: _invoice_fact(item) for item in invoices},
                    {item.credit_note_id: _credit_fact(item) for item in credit_rows},
                    cash,
                    credits,
                )
            except ValueError as exc:
                raise ServiceError("invalid_interpretation", str(exc), 422) from exc
            result = ProposalResult(
                ProposalStatus.PROPOSED,
                tuple(cash),
                tuple(credits),
                evidence=evidence,
                reason=f"bounded interpretation: {reason_code}",
            )
        proposal.current_revision += 1
        proposal.status = result.status.value
        proposal.review_required = True
        self.session.add(
            _revision_from_result(
                proposal.id,
                proposal.current_revision,
                result,
                _token(payment, invoices, credit_rows, result),
                provenance=f"llm-{mode}-v1",
                model_trace={"interpretation": {"source": source, "mode": mode, **trace}},
            )
        )
        self.session.add(
            AuditEvent(
                workspace_id=workspace_id,
                action="proposal.interpret",
                actor=f"interpreter:{source}",
                entity_id=proposal.id,
                payload={
                    "revision": proposal.current_revision,
                    "mode": mode,
                    "source": source,
                    "reason_code": reason_code,
                },
            )
        )
        self.session.commit()
        return proposal

    def correct(
        self,
        workspace_id: uuid.UUID,
        proposal_id: uuid.UUID,
        expected_revision: int,
        cash: list[CashLine],
        credits: list[CreditLine],
        reviewer: str,
    ) -> Proposal:
        proposal = self.session.scalar(
            select(Proposal)
            .where(Proposal.id == proposal_id, Proposal.workspace_id == workspace_id)
            .with_for_update()
        )
        if proposal is None:
            raise ServiceError("not_found", "proposal not found", 404)
        if proposal.current_revision != expected_revision:
            raise ServiceError("stale_revision", "proposal revision is stale")
        if not reviewer.strip():
            raise ServiceError("validation", "reviewer is required", 422)
        if proposal.status in (ProposalStatus.APPLIED.value, ProposalStatus.REVERSED.value):
            raise ServiceError("immutable_revision", "applied revisions cannot be corrected")
        previous_revision = self.session.scalar(
            select(ProposalRevision).where(
                ProposalRevision.proposal_id == proposal.id,
                ProposalRevision.revision == proposal.current_revision,
            )
        )
        payment = self.session.scalar(
            select(Payment)
            .where(Payment.id == proposal.payment_id, Payment.workspace_id == workspace_id)
            .with_for_update()
        )
        if payment is None:
            raise ServiceError("not_found", "payment not found", 404)
        invoices = list(
            self.session.scalars(
                select(Invoice)
                .where(
                    Invoice.workspace_id == workspace_id,
                    Invoice.invoice_id.in_(
                        {line.invoice_id for line in cash}
                        | {line.invoice_id for line in credits}
                    ),
                )
                .order_by(Invoice.id)
                .with_for_update()
            )
        )
        credits_db = list(
            self.session.scalars(
                select(CreditNote)
                .where(
                    CreditNote.workspace_id == workspace_id,
                    CreditNote.credit_note_id.in_({line.credit_note_id for line in credits}),
                )
                .order_by(CreditNote.id)
                .with_for_update()
            )
        )
        inv_map = {i.invoice_id: _invoice_fact(i) for i in invoices}
        credit_map = {c.credit_note_id: _credit_fact(c) for c in credits_db}
        try:
            validate_allocation(payment.amount, inv_map, credit_map, cash, credits)
        except ValueError as exc:
            raise ServiceError("validation", str(exc), 422) from exc
        result = ProposalResult(
            ProposalStatus.PROPOSED, tuple(cash), tuple(credits), reason="human correction"
        )
        proposal.current_revision += 1
        proposal.status = ProposalStatus.PROPOSED.value
        selected = [
            i
            for i in invoices
            if i.invoice_id
            in {line.invoice_id for line in cash} | {line.invoice_id for line in credits}
        ]
        selected_credits = [
            c for c in credits_db if c.credit_note_id in {line.credit_note_id for line in credits}
        ]
        correction_trace = deepcopy(previous_revision.model_trace) if previous_revision else {}
        correction_stages = list(correction_trace.get("stages", []))
        correction_stages.append(
            _trace_stage(
                f"human-correction-r{proposal.current_revision}",
                "Reviewer correction",
                "completed",
                "A reviewer supplied a new allocation for this proposal.",
                None,
                details={"reviewer": reviewer.strip()},
            )
        )
        correction_trace["stages"] = correction_stages
        correction_trace["reviewer_provenance"] = {
            "kind": "human-correction",
            "reviewer": reviewer.strip(),
        }
        self.session.add(
            _revision_from_result(
                proposal.id,
                proposal.current_revision,
                result,
                _token(payment, selected, selected_credits, result),
                provenance="human-correction",
                model_trace=correction_trace,
                reviewer=reviewer.strip(),
            )
        )
        self.session.add(
            AuditEvent(
                workspace_id=workspace_id,
                action="proposal.correct",
                actor=reviewer.strip(),
                entity_id=proposal.id,
                payload={"revision": proposal.current_revision},
            )
        )
        self.session.commit()
        return proposal

    def apply(
        self,
        workspace_id: uuid.UUID,
        proposal_id: uuid.UUID,
        revision_number: int,
        version_token: str,
        reviewer: str,
        idempotency_key: str,
    ) -> ApplicationGroup:
        if not reviewer.strip() or not idempotency_key.strip():
            raise ServiceError("validation", "reviewer and idempotency key are required", 422)
        proposal = self.session.scalar(
            select(Proposal)
            .where(Proposal.id == proposal_id, Proposal.workspace_id == workspace_id)
            .with_for_update()
        )
        if proposal is None:
            raise ServiceError("not_found", "proposal not found", 404)
        revision = self.session.scalar(
            select(ProposalRevision).where(
                ProposalRevision.proposal_id == proposal.id,
                ProposalRevision.revision == revision_number,
            )
        )
        if revision is None or proposal.current_revision != revision_number:
            raise ServiceError("stale_revision", "proposal revision is stale")
        payload = {
            "proposal": str(proposal_id),
            "revision": revision_number,
            "version_token": version_token,
            "reviewer": reviewer.strip(),
        }
        payload_hash = _hash(payload)
        idem = self.session.scalar(
            select(IdempotencyKey)
            .where(
                IdempotencyKey.workspace_id == workspace_id,
                IdempotencyKey.action == "apply",
                IdempotencyKey.key == idempotency_key,
            )
            .with_for_update()
        )
        if idem:
            if idem.payload_hash != payload_hash:
                raise ServiceError(
                    "idempotency_conflict", "idempotency key was used with a different payload"
                )
            existing = self.session.get(ApplicationGroup, idem.result_id)
            if existing is not None:
                return existing
        if revision.version_token != version_token:
            raise ServiceError("stale_version", "proposal version token is stale")
        if proposal.status != ProposalStatus.PROPOSED.value:
            raise ServiceError("not_proposed", "only a proposed revision can be applied")
        payment = self.session.scalar(
            select(Payment)
            .where(Payment.id == proposal.payment_id, Payment.workspace_id == workspace_id)
            .with_for_update()
        )
        if payment is None or payment.conflicted:
            raise ServiceError("stale_balance", "payment opening snapshot is conflicted")
        cash_lines = [CashLine(x["invoice_id"], int(x["amount"])) for x in revision.cash_lines]
        credit_lines = [
            CreditLine(x["credit_note_id"], x["invoice_id"], int(x["amount"]))
            for x in revision.credit_lines
        ]
        invoice_ids = {x.invoice_id for x in cash_lines} | {x.invoice_id for x in credit_lines}
        credit_ids = {x.credit_note_id for x in credit_lines}
        all_invoices = list(
            self.session.scalars(
                select(Invoice)
                .where(
                    Invoice.workspace_id == workspace_id,
                    Invoice.invoice_id.in_(invoice_ids),
                )
                .order_by(Invoice.id)
                .with_for_update()
            )
        )
        invoices = [invoice for invoice in all_invoices if invoice.invoice_id in invoice_ids]
        credits = (
            list(
                self.session.scalars(
                    select(CreditNote)
                    .where(
                        CreditNote.workspace_id == workspace_id,
                        CreditNote.credit_note_id.in_(credit_ids),
                    )
                    .order_by(CreditNote.id)
                    .with_for_update()
                )
            )
            if credit_ids
            else []
        )
        credits = [credit for credit in credits if credit.credit_note_id in credit_ids]
        inv_map = {i.invoice_id: _invoice_fact(i) for i in invoices}
        credit_map = {c.credit_note_id: _credit_fact(c) for c in credits}
        if any(i.conflicted for i in invoices) or any(c.conflicted for c in credits):
            raise ServiceError("stale_balance", "opening snapshot is conflicted")
        if len(inv_map) != len(invoices):
            raise ServiceError("stale_balance", "invoice identity is ambiguous")
        customer_ids = {invoice.customer_id for invoice in invoices} | {
            credit.customer_id for credit in credits
        }
        if len(customer_ids) > 1:
            raise ServiceError("validation", "selected records must belong to one customer", 422)
        if payment.customer_id and any(i.customer_id != payment.customer_id for i in invoices):
            raise ServiceError("validation", "payment and invoice customer do not agree", 422)
        current_result = ProposalResult(
            ProposalStatus(revision.status),
            tuple(cash_lines),
            tuple(credit_lines),
            tuple(
                Evidence(
                    item["source_id"], int(item["start"]), int(item["end"]), item.get("quote", "")
                )
                for item in revision.evidence
            ),
            tuple(tuple(item) for item in revision.alternatives),
            tuple(revision.signals),
            revision.reason,
        )
        if _token(payment, invoices, credits, current_result) != version_token:
            raise ServiceError("stale_version", "proposal version token is stale")
        already_cash: defaultdict[str, int] = defaultdict(int)
        cash_by_payment: defaultdict[uuid.UUID, int] = defaultdict(int)
        for cash_application in self.session.scalars(
            select(CashApplication).where(
                CashApplication.workspace_id == workspace_id,
                CashApplication.active.is_(True),
            )
        ):
            invoice = self.session.get(Invoice, cash_application.invoice_id)
            if invoice:
                already_cash[invoice.invoice_id] += cash_application.amount
            cash_by_payment[cash_application.payment_id] += cash_application.amount
        already_credit: defaultdict[str, int] = defaultdict(int)
        already_credit_by_invoice: defaultdict[str, int] = defaultdict(int)
        for credit_application in self.session.scalars(
            select(CreditApplication).where(
                CreditApplication.workspace_id == workspace_id, CreditApplication.active.is_(True)
            )
        ):
            invoice = self.session.get(Invoice, credit_application.invoice_id)
            credit = self.session.get(CreditNote, credit_application.credit_note_id)
            if invoice and credit:
                already_credit[credit.credit_note_id] += credit_application.amount
                already_credit_by_invoice[invoice.invoice_id] += credit_application.amount
        if cash_by_payment[payment.id] + sum(line.amount for line in cash_lines) > payment.amount:
            raise ServiceError("stale_balance", "cash allocation exceeds payment")
        try:
            validate_allocation(
                payment.amount,
                inv_map,
                credit_map,
                cash_lines,
                credit_lines,
                already_cash=already_cash,
                already_credit=already_credit,
                already_credit_by_invoice=already_credit_by_invoice,
            )
        except (ValueError, KeyError) as exc:
            raise ServiceError("stale_balance", str(exc)) from exc
        group = ApplicationGroup(
            workspace_id=workspace_id,
            proposal_id=proposal.id,
            proposal_revision=revision_number,
            payment_id=payment.id,
            reviewer=reviewer.strip(),
        )
        self.session.add(group)
        self.session.flush()
        invoice_by_key = {i.invoice_id: i for i in invoices}
        credit_by_key = {c.credit_note_id: c for c in credits}
        for cash_line in cash_lines:
            self.session.add(
                CashApplication(
                    workspace_id=workspace_id,
                    application_group_id=group.id,
                    payment_id=payment.id,
                    invoice_id=invoice_by_key[cash_line.invoice_id].id,
                    amount=cash_line.amount,
                )
            )
        for credit_line in credit_lines:
            self.session.add(
                CreditApplication(
                    workspace_id=workspace_id,
                    application_group_id=group.id,
                    credit_note_id=credit_by_key[credit_line.credit_note_id].id,
                    invoice_id=invoice_by_key[credit_line.invoice_id].id,
                    amount=credit_line.amount,
                )
            )
        payment.version += 1
        for invoice in invoices:
            invoice.version += 1
        for credit in credits:
            credit.version += 1
        proposal.status = ProposalStatus.APPLIED.value
        proposal.applied_revision = revision_number
        self.session.add(
            IdempotencyKey(
                workspace_id=workspace_id,
                action="apply",
                key=idempotency_key,
                payload_hash=payload_hash,
                result_id=group.id,
            )
        )
        self.session.add(
            AuditEvent(
                workspace_id=workspace_id,
                action="application.apply",
                actor=reviewer.strip(),
                entity_id=group.id,
                payload=payload,
            )
        )
        self.session.commit()
        return group

    def reverse(
        self,
        workspace_id: uuid.UUID,
        application_id: uuid.UUID,
        reviewer: str,
        reason: str,
        idempotency_key: str,
    ) -> ApplicationGroup:
        if not reviewer.strip() or not reason.strip() or not idempotency_key.strip():
            raise ServiceError(
                "validation", "reviewer, reason, and idempotency key are required", 422
            )
        group = self.session.scalar(
            select(ApplicationGroup)
            .where(
                ApplicationGroup.id == application_id, ApplicationGroup.workspace_id == workspace_id
            )
            .with_for_update()
        )
        if group is None:
            raise ServiceError("not_found", "application not found", 404)
        payload_hash = _hash(
            {
                "application": str(application_id),
                "reviewer": reviewer.strip(),
                "reason": reason.strip(),
            }
        )
        idem = self.session.scalar(
            select(IdempotencyKey)
            .where(
                IdempotencyKey.workspace_id == workspace_id,
                IdempotencyKey.action == "reverse",
                IdempotencyKey.key == idempotency_key,
            )
            .with_for_update()
        )
        if idem:
            if idem.payload_hash != payload_hash:
                raise ServiceError(
                    "idempotency_conflict", "idempotency key was used with a different payload"
                )
            existing = self.session.get(ApplicationGroup, idem.result_id)
            if existing:
                return existing
        if group.reversed_at is not None:
            raise ServiceError("already_reversed", "application is already reversed")
        cash_applications = list(
            self.session.scalars(
                select(CashApplication)
                .where(CashApplication.application_group_id == group.id)
                .with_for_update()
            )
        )
        credit_applications = list(
            self.session.scalars(
                select(CreditApplication)
                .where(CreditApplication.application_group_id == group.id)
                .with_for_update()
            )
        )
        payment = self.session.scalar(
            select(Payment)
            .where(Payment.id == group.payment_id, Payment.workspace_id == workspace_id)
            .with_for_update()
        )
        invoice_ids = {row.invoice_id for row in cash_applications + credit_applications}
        credit_ids = {row.credit_note_id for row in credit_applications}
        invoices = (
            list(
                self.session.scalars(
                    select(Invoice)
                    .where(Invoice.workspace_id == workspace_id, Invoice.id.in_(invoice_ids))
                    .order_by(Invoice.id)
                    .with_for_update()
                )
            )
            if invoice_ids
            else []
        )
        credits = (
            list(
                self.session.scalars(
                    select(CreditNote)
                    .where(CreditNote.workspace_id == workspace_id, CreditNote.id.in_(credit_ids))
                    .order_by(CreditNote.id)
                    .with_for_update()
                )
            )
            if credit_ids
            else []
        )
        for cash_application in cash_applications:
            cash_application.active = False
        for credit_application in credit_applications:
            credit_application.active = False
        if payment:
            payment.version += 1
        for invoice in invoices:
            invoice.version += 1
        for credit in credits:
            credit.version += 1
        group.reversed_at = now_utc()
        proposal = self.session.get(Proposal, group.proposal_id)
        if proposal:
            proposal.status = ProposalStatus.REVERSED.value
        self.session.add(
            IdempotencyKey(
                workspace_id=workspace_id,
                action="reverse",
                key=idempotency_key,
                payload_hash=payload_hash,
                result_id=group.id,
            )
        )
        self.session.add(
            AuditEvent(
                workspace_id=workspace_id,
                action="application.reverse",
                actor=reviewer.strip(),
                entity_id=group.id,
                payload={"reason": reason.strip()},
            )
        )
        self.session.commit()
        return group

    def _stale_for_invoice(self, workspace_id: uuid.UUID, invoice_id: str) -> None:
        for proposal in self.session.scalars(
            select(Proposal).where(
                Proposal.workspace_id == workspace_id,
                Proposal.status.in_(
                    [
                        ProposalStatus.PROPOSED.value,
                        ProposalStatus.NEEDS_REVIEW.value,
                        ProposalStatus.APPLIED.value,
                    ]
                ),
            )
        ):
            revision = self.session.scalar(
                select(ProposalRevision).where(
                    ProposalRevision.proposal_id == proposal.id,
                    ProposalRevision.revision == proposal.current_revision,
                )
            )
            if revision and any(
                x.get("invoice_id") == invoice_id
                for x in revision.cash_lines + revision.credit_lines
            ):
                if proposal.status == ProposalStatus.APPLIED.value:
                    proposal.review_required = True
                else:
                    proposal.status = ProposalStatus.STALE.value

    def _lock_payment_proposals(
        self, workspace_id: uuid.UUID, payment_id: uuid.UUID
    ) -> list[Proposal]:
        return list(
            self.session.scalars(
                select(Proposal)
                .where(
                    Proposal.workspace_id == workspace_id,
                    Proposal.payment_id == payment_id,
                    Proposal.status.in_(
                        [
                            ProposalStatus.PROPOSED.value,
                            ProposalStatus.NEEDS_REVIEW.value,
                            ProposalStatus.APPLIED.value,
                        ]
                    ),
                )
                .order_by(Proposal.id)
                .with_for_update()
            )
        )

    def _stale_for_payment(
        self,
        workspace_id: uuid.UUID,
        payment_id: uuid.UUID,
        *,
        proposals: list[Proposal] | None = None,
    ) -> None:
        proposals = proposals if proposals is not None else self._lock_payment_proposals(
            workspace_id, payment_id
        )
        for proposal in proposals:
            if proposal.status == ProposalStatus.APPLIED.value:
                proposal.review_required = True
            else:
                proposal.status = ProposalStatus.STALE.value

    def _stale_for_credit(self, workspace_id: uuid.UUID, credit_id: str) -> None:
        for proposal in self.session.scalars(
            select(Proposal).where(
                Proposal.workspace_id == workspace_id,
                Proposal.status.in_(
                    [
                        ProposalStatus.PROPOSED.value,
                        ProposalStatus.NEEDS_REVIEW.value,
                        ProposalStatus.APPLIED.value,
                    ]
                ),
            )
        ):
            revision = self.session.scalar(
                select(ProposalRevision).where(
                    ProposalRevision.proposal_id == proposal.id,
                    ProposalRevision.revision == proposal.current_revision,
                )
            )
            if revision and any(
                x.get("credit_note_id") == credit_id for x in revision.credit_lines
            ):
                if proposal.status == ProposalStatus.APPLIED.value:
                    proposal.review_required = True
                else:
                    proposal.status = ProposalStatus.STALE.value


def _payment_equal(row: Payment, data: dict[str, Any]) -> bool:
    return all(getattr(row, key) == value for key, value in data.items())


def _invoice_equal(row: Invoice, data: dict[str, Any]) -> bool:
    return all(getattr(row, key) == value for key, value in data.items())


def _credit_equal(row: CreditNote, data: dict[str, Any]) -> bool:
    return all(getattr(row, key) == value for key, value in data.items())
