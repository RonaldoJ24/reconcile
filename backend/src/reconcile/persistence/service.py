from __future__ import annotations

import hashlib
import json
import uuid
from collections import defaultdict
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from reconcile.domain.matching import propose, validate_allocation
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
    provenance: str = "rules-v1",
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


class ReconcileService:
    def __init__(self, session: Session):
        self.session = session

    def create_workspace(self, mode: str = "local") -> Workspace:
        workspace = Workspace(mode=mode)
        self.session.add(workspace)
        self.session.flush()
        return workspace

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
                self.session.add(
                    Source(
                        workspace_id=workspace_id,
                        batch_id=record.id,
                        kind=parsed.kind,
                        sha256=parsed.sha256,
                        raw_bytes=parsed.raw,
                        accepted_count=parsed.accepted_count,
                        rejected_count=parsed.rejected_count,
                        source_metadata=metadata,
                    )
                )
        self.session.flush()
        return record

    def commit_import(self, workspace_id: uuid.UUID, batch_id: uuid.UUID) -> dict[str, Any]:
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
        sources = list(
            self.session.scalars(
                select(Source).where(
                    Source.workspace_id == workspace_id, Source.batch_id == batch.id
                )
            )
        )
        by_kind = {s.kind: s for s in sources}
        counts: dict[str, Any] = {"payments": 0, "invoices": 0, "credits": 0, "conflicts": []}
        jobs: list[str] = []
        # Sources remain immutable; only accepted rows become business records.
        payment_source = by_kind.get("bank")
        if payment_source:
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
                job = Job(
                    workspace_id=workspace_id,
                    kind="match-payment",
                    payload={"payment_id": str(payment.id)},
                )
                self.session.add(job)
                self.session.flush()
                jobs.append(str(job.id))
        invoice_source = by_kind.get("invoice")
        if invoice_source:
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
        if credit_source:
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
        message_source = by_kind.get("message")
        if message_source:
            metadata = message_source.source_metadata
            associated_payment = self.session.scalar(
                select(Payment).where(
                    Payment.workspace_id == workspace_id,
                    Payment.source_account_id == metadata.get("payment_source_account_id"),
                    Payment.transaction_id == metadata.get("payment_transaction_id"),
                )
            )
            if associated_payment:
                associated_payment.customer_id = associated_payment.customer_id
        batch.status = "COMMITTED"
        self.session.commit()
        return {"batch_id": str(batch.id), **counts, "jobs": jobs}

    def process_match(self, workspace_id: uuid.UUID, payment_id: uuid.UUID) -> Proposal:
        payment = self.session.scalar(
            select(Payment).where(Payment.id == payment_id, Payment.workspace_id == workspace_id)
        )
        if payment is None:
            raise ServiceError("not_found", "payment not found", 404)
        invoices = list(
            self.session.scalars(
                select(Invoice)
                .where(Invoice.workspace_id == workspace_id, Invoice.outstanding_amount > 0)
                .order_by(Invoice.invoice_id)
            )
        )
        credits = list(
            self.session.scalars(
                select(CreditNote)
                .where(CreditNote.workspace_id == workspace_id, CreditNote.available_amount > 0)
                .order_by(CreditNote.credit_note_id)
            )
        )
        evidence: dict[str, str] = {}
        for source in self.session.scalars(
            select(Source).where(Source.workspace_id == workspace_id, Source.kind == "message")
        ):
            if (
                source.source_metadata.get("payment_source_account_id") == payment.source_account_id
                and source.source_metadata.get("payment_transaction_id") == payment.transaction_id
            ):
                evidence[str(source.id)] = source.raw_bytes.decode("utf-8")
        result = propose(
            _payment_fact(payment),
            [_invoice_fact(i) for i in invoices],
            [_credit_fact(c) for c in credits],
            evidence,
        )
        proposal = self.session.scalar(
            select(Proposal)
            .where(Proposal.workspace_id == workspace_id, Proposal.payment_id == payment.id)
            .order_by(Proposal.created_at.desc())
        )
        if proposal is None:
            proposal = Proposal(
                workspace_id=workspace_id,
                payment_id=payment.id,
                status=result.status.value,
                current_revision=1,
            )
            self.session.add(proposal)
            self.session.flush()
            proposal_revision = 1
        else:
            proposal.current_revision += 1
            proposal.status = result.status.value
            proposal_revision = proposal.current_revision
        selected_ids = {line.invoice_id for line in result.cash} | {
            line.invoice_id for line in result.credits
        }
        selected_invoices = [i for i in invoices if i.invoice_id in selected_ids]
        selected_credits = [
            c
            for c in credits
            if c.credit_note_id in {line.credit_note_id for line in result.credits}
        ]
        revision = _revision_from_result(
            proposal.id,
            proposal_revision,
            result,
            _token(payment, selected_invoices, selected_credits, result),
        )
        self.session.add(revision)
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
                .where(Invoice.workspace_id == workspace_id)
                .order_by(Invoice.id)
                .with_for_update()
            )
        )
        credits_db = list(
            self.session.scalars(
                select(CreditNote)
                .where(CreditNote.workspace_id == workspace_id)
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
        self.session.add(
            _revision_from_result(
                proposal.id,
                proposal.current_revision,
                result,
                _token(payment, selected, selected_credits, result),
                provenance="human-correction",
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
                .where(Invoice.workspace_id == workspace_id)
                .order_by(Invoice.id)
                .with_for_update()
            )
        )
        invoices = [invoice for invoice in all_invoices if invoice.invoice_id in invoice_ids]
        credits = (
            list(
                self.session.scalars(
                    select(CreditNote)
                    .where(CreditNote.workspace_id == workspace_id)
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

    def _stale_for_payment(self, workspace_id: uuid.UUID, payment_id: uuid.UUID) -> None:
        proposals = self.session.scalars(
            select(Proposal).where(
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
