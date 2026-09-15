from __future__ import annotations

import csv
import hashlib
import io
import os
import secrets
import uuid
from collections.abc import AsyncIterator, Generator
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from reconcile.api.sample import is_exact_sample_packet, sample_zip
from reconcile.api.schemas import (
    ApplyRequest,
    CorrectionRequest,
    InterpretationRequestBody,
    ReverseRequest,
    SessionRequest,
)
from reconcile.config import provider_invite_hash, server_mode
from reconcile.domain.types import CashLine, CreditLine, JobStatus
from reconcile.ingest.parsers import (
    parse_batch,
    parse_credit_source,
    parse_csv_source,
    parse_message_context,
)
from reconcile.interpretation.workflow import compile_workflow
from reconcile.jobs.lifecycle import LifecycleConsumer
from reconcile.jobs.queue import run_once
from reconcile.persistence.db import SessionLocal, readiness, session_scope
from reconcile.persistence.maintenance import (
    cleanup_expired_preview_workspaces,
    enforce_database_admission,
)
from reconcile.persistence.models import (
    ApplicationGroup,
    CashApplication,
    CreditApplication,
    CreditNote,
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
from reconcile.persistence.models import (
    Session as DbSession,
)
from reconcile.persistence.service import ReconcileService, ServiceError

SESSION_COOKIE = "reconcile_session"
LOCAL_SESSION_TTL = timedelta(hours=8)
PREVIEW_SESSION_TTL = timedelta(hours=24)
INTERPRETATION_WORKFLOW = compile_workflow()


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _session_ttl(mode: str) -> timedelta:
    return PREVIEW_SESSION_TTL if mode == "preview" else LOCAL_SESSION_TTL


def _wake_consumer(request: Request) -> None:
    consumer = getattr(request.app.state, "consumer", None)
    if isinstance(consumer, LifecycleConsumer):
        consumer.wake()


def _session(request: Request, db: Session) -> tuple[DbSession, Workspace]:
    raw = request.cookies.get(SESSION_COOKIE)
    if not raw:
        raise HTTPException(401, "authentication required")
    row = db.execute(
        select(DbSession, Workspace)
        .join(Workspace, Workspace.id == DbSession.workspace_id)
        .where(DbSession.token_hash == _sha(raw), DbSession.expires_at > now_utc())
    ).one_or_none()
    if row is None:
        raise HTTPException(401, "authentication required")
    record, workspace = row
    now = now_utc()
    record.expires_at = now + _session_ttl(workspace.mode)
    workspace.last_active_at = now
    db.commit()
    _wake_consumer(request)
    return record, workspace


def _require_mutation(request: Request, db: Session) -> tuple[DbSession, Workspace]:
    record, workspace = _session(request, db)
    csrf = request.headers.get("X-CSRF-Token")
    if not csrf or not secrets.compare_digest(record.csrf_hash, _sha(csrf)):
        raise HTTPException(403, "CSRF token required")
    origin = request.headers.get("Origin")
    if origin:
        expected = str(request.base_url).rstrip("/")
        allowed = {
            expected,
            request.headers.get("X-Forwarded-Proto", "").split(",")[0]
            + "://"
            + request.headers.get("host", ""),
        }
        if origin.rstrip("/") not in allowed:
            raise HTTPException(403, "origin is not allowed")
    return record, workspace


def _db() -> Generator[Session, None, None]:
    yield from session_scope()


async def _read_bounded(upload: UploadFile, limit: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while chunk := await upload.read(min(64 * 1024, limit - total + 1)):
        total += len(chunk)
        if total > limit:
            raise HTTPException(
                413,
                detail={"code": "quota", "message": "source exceeds file-size limit"},
            )
        chunks.append(chunk)
    return b"".join(chunks)


def _error(exc: ServiceError) -> HTTPException:
    return HTTPException(exc.status, detail={"code": exc.code, "message": exc.message})


def create_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        consumer: LifecycleConsumer | None = None
        if server_mode() == "preview":
            with SessionLocal() as maintenance_session:
                cleanup_expired_preview_workspaces(maintenance_session)
            if os.getenv("RECONCILE_WORKER_MODE", "") == "web_lifecycle":
                consumer = LifecycleConsumer(SessionLocal)
                app.state.consumer = consumer
                consumer.start()
        yield
        if consumer is not None:
            consumer.stop()

    app = FastAPI(title="Reconcile API", version="1.0", lifespan=lifespan)

    @app.exception_handler(HTTPException)
    async def http_error(_: Request, exc: HTTPException) -> JSONResponse:
        detail = exc.detail
        if isinstance(detail, dict) and "code" in detail:
            error = detail
        else:
            error = {"code": "http_error", "message": str(detail)}
        return JSONResponse(status_code=exc.status_code, content={"error": error})

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "validation",
                    "message": "request validation failed",
                    "fields": exc.errors(),
                }
            },
        )

    @app.get("/api/v1/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/v1/ready")
    def ready() -> Response:
        available = readiness()
        return JSONResponse(
            {"status": "ready" if available else "unavailable"},
            status_code=200 if available else 503,
        )

    @app.post("/api/v1/session", response_model=dict)
    def create_session(
        request: Request,
        body: SessionRequest | None = None,
        db: Session = Depends(_db),
    ) -> Response:
        mode = server_mode()
        if mode == "preview":
            cleanup_expired_preview_workspaces(db)
        old = request.cookies.get(SESSION_COOKIE)
        workspace: Workspace | None = None
        if old:
            session = db.scalar(
                select(DbSession).where(
                    DbSession.token_hash == _sha(old), DbSession.expires_at > now_utc()
                )
            )
            if session:
                workspace = db.get(Workspace, session.workspace_id)
                if workspace:
                    if body and body.invite_token:
                        expected = provider_invite_hash()
                        if expected is None or not secrets.compare_digest(
                            expected, _sha(body.invite_token)
                        ):
                            raise HTTPException(403, "provider invite is invalid")
                        session.provider_access = True
                    raw_token, csrf_token = old, secrets.token_urlsafe(32)
                    session.csrf_hash = _sha(csrf_token)
                    session.expires_at = now_utc() + _session_ttl(workspace.mode)
                    workspace.last_active_at = now_utc()
                    db.commit()
                    response = JSONResponse(
                        {
                            "mode": workspace.mode,
                            "expires_at": session.expires_at.isoformat(),
                            "csrf_token": csrf_token,
                            "provider_access": session.provider_access,
                        }
                    )
                    response.set_cookie(
                        SESSION_COOKIE,
                        raw_token,
                        httponly=True,
                        secure=workspace.mode == "preview",
                        samesite="strict",
                        max_age=int(_session_ttl(workspace.mode).total_seconds()),
                    )
                    _wake_consumer(request)
                    return response
        if mode == "preview":
            enforce_database_admission(db)
        workspace = ReconcileService(db).create_workspace(mode)
        raw_token, csrf_token = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        provider_access = mode == "local"
        if body and body.invite_token:
            expected = provider_invite_hash()
            if expected is None or not secrets.compare_digest(expected, _sha(body.invite_token)):
                raise HTTPException(403, "provider invite is invalid")
            provider_access = True
        session = DbSession(
            workspace_id=workspace.id,
            token_hash=_sha(raw_token),
            csrf_hash=_sha(csrf_token),
            expires_at=now_utc() + _session_ttl(mode),
            provider_access=provider_access,
        )
        db.add(session)
        db.commit()
        response = JSONResponse(
            {
                "mode": workspace.mode,
                "expires_at": session.expires_at.isoformat(),
                "csrf_token": csrf_token,
                "provider_access": session.provider_access,
            }
        )
        response.set_cookie(
            SESSION_COOKIE,
            raw_token,
            httponly=True,
            secure=workspace.mode == "preview",
            samesite="strict",
            max_age=int(_session_ttl(workspace.mode).total_seconds()),
        )
        _wake_consumer(request)
        return response

    @app.post("/api/v1/imports/validate")
    async def validate_import(
        request: Request,
        bank: Annotated[UploadFile, File(...)],
        invoices: Annotated[UploadFile, File(...)],
        credits: Annotated[UploadFile | None, File()] = None,
        message: Annotated[UploadFile | None, File()] = None,
        message_time: Annotated[str | None, Form()] = None,
        payment_source_account_id: Annotated[str | None, Form()] = None,
        payment_transaction_id: Annotated[str | None, Form()] = None,
        db: Session = Depends(_db),
    ) -> dict[str, object]:
        _, workspace = _require_mutation(request, db)
        if workspace.mode == "preview":
            enforce_database_admission(db)
        from reconcile.ingest.parsers import LIMITS

        limit = LIMITS[workspace.mode]["file"]
        bank_bytes = await _read_bounded(bank, limit)
        invoice_bytes = await _read_bounded(invoices, limit)
        credit_bytes = await _read_bounded(credits, limit) if credits else None
        message_bytes = await _read_bounded(message, limit) if message else None
        if workspace.mode == "preview" and not is_exact_sample_packet(
            {
                "bank": bank_bytes,
                "invoices": invoice_bytes,
                "credits": credit_bytes or b"",
                "message": message_bytes or b"",
            }
        ):
            raise HTTPException(
                403,
                detail={
                    "code": "preview_only",
                    "message": (
                        "Public preview accepts only the unmodified demo packet. "
                        "Download it, unzip it, upload bank.csv, invoices.csv, "
                        "credits.csv, and message.txt, then use the context values "
                        "from README.txt."
                    ),
                },
            )
        try:
            context = (
                parse_message_context(
                    message_time or "",
                    payment_source_account_id or "",
                    payment_transaction_id or "",
                )
                if message
                else None
            )
            batch = parse_batch(
                bank_bytes,
                invoice_bytes,
                credit_bytes,
                message_bytes,
                message_context=context,
                profile=workspace.mode,
            )
            record = ReconcileService(db).validate_import(workspace.id, batch, workspace.mode)
            db.commit()
        except ServiceError as exc:
            raise _error(exc) from exc
        except ValueError as exc:
            raise HTTPException(422, detail={"code": "validation", "message": str(exc)}) from exc
        source_reports = [
            {
                "kind": source.kind,
                "source_id": str(source.id),
                "accepted": source.accepted_count,
                "rejected": source.rejected_count,
                "issues": source.source_metadata.get("issues", []),
            }
            for source in db.scalars(select(Source).where(Source.batch_id == record.id))
        ]
        return {
            "batch_id": str(record.id),
            "sources": source_reports,
            "accepted": sum(x["accepted"] for x in source_reports),
            "rejected": sum(x["rejected"] for x in source_reports),
        }

    @app.post("/api/v1/imports/{batch_id}/commit")
    def commit_import(
        batch_id: uuid.UUID, request: Request, db: Session = Depends(_db)
    ) -> dict[str, object]:
        _, workspace = _require_mutation(request, db)
        if workspace.mode == "preview":
            enforce_database_admission(db)
        try:
            result = ReconcileService(db).commit_import(workspace.id, batch_id)
            _wake_consumer(request)
            return result
        except ServiceError as exc:
            raise _error(exc)

    @app.get("/api/v1/imports")
    def list_imports(request: Request, db: Session = Depends(_db)) -> list[dict[str, object]]:
        _, workspace = _session(request, db)
        return [
            {"batch_id": str(x.id), "status": x.status, "created_at": x.created_at.isoformat()}
            for x in db.scalars(
                select(ImportBatch)
                .where(ImportBatch.workspace_id == workspace.id)
                .order_by(ImportBatch.created_at.desc())
            )
        ]

    @app.get("/api/v1/sources/{source_id}")
    def get_source(
        source_id: uuid.UUID, request: Request, db: Session = Depends(_db)
    ) -> dict[str, object]:
        _, workspace = _session(request, db)
        source = db.scalar(
            select(Source).where(Source.id == source_id, Source.workspace_id == workspace.id)
        )
        if source is None:
            raise HTTPException(404, "source not found")
        text = None
        rows: list[dict[str, object]] = []
        if source.kind == "message":
            text = source.raw_bytes.decode("utf-8")[:100_000]
        elif source.kind == "bank":
            rows = list(parse_csv_source("bank", source.raw_bytes, profile="local").rows[:2000])
        elif source.kind == "invoice":
            rows = list(parse_csv_source("invoice", source.raw_bytes, profile="local").rows[:2000])
        elif source.kind == "credit":
            rows = list(parse_credit_source(source.raw_bytes, profile="local").rows[:2000])
        return {
            "source_id": str(source.id),
            "kind": source.kind,
            "sha256": source.sha256,
            "bytes": len(source.raw_bytes),
            "text": text,
            "rows": rows,
            "issues": source.source_metadata.get("issues", []),
            "row_locators": source.source_metadata.get("row_locators", []),
            "metadata": source.source_metadata,
        }

    @app.post("/api/v1/jobs/run-once")
    def run_job(request: Request, db: Session = Depends(_db)) -> dict[str, object]:
        _, workspace = _require_mutation(request, db)
        job = run_once(db, owner=f"api:{workspace.id}", workspace_id=workspace.id)
        if job is None:
            # A lifecycle consumer may have claimed the job between the wake and this request.
            job = db.scalar(
                select(Job)
                .where(
                    Job.workspace_id == workspace.id,
                    Job.status.in_((JobStatus.PENDING.value, JobStatus.RUNNING.value)),
                )
                .order_by(Job.created_at)
            )
        return (
            {"job_id": str(job.id), "status": job.status}
            if job
            else {"job_id": None, "status": "IDLE"}
        )

    @app.get("/api/v1/jobs/status")
    def job_status(request: Request, db: Session = Depends(_db)) -> dict[str, str]:
        _session(request, db)
        consumer = getattr(request.app.state, "consumer", None)
        return {"status": consumer.state if isinstance(consumer, LifecycleConsumer) else "manual"}

    @app.get("/api/v1/proposals")
    def list_proposals(request: Request, db: Session = Depends(_db)) -> list[dict[str, object]]:
        _, workspace = _session(request, db)
        application_id = (
            select(ApplicationGroup.id)
            .where(ApplicationGroup.proposal_id == Proposal.id)
            .order_by(ApplicationGroup.created_at.desc(), ApplicationGroup.id.desc())
            .limit(1)
            .correlate(Proposal)
            .scalar_subquery()
        )
        rows: list[dict[str, object]] = []
        query = (
            select(Proposal, Payment, application_id.label("application_id"))
            .join(Payment, Payment.id == Proposal.payment_id)
            .where(Proposal.workspace_id == workspace.id, Payment.workspace_id == workspace.id)
            .order_by(Proposal.updated_at.desc())
        )
        for proposal, payment, latest_application_id in db.execute(query):
            rows.append(
                {
                    "proposal_id": str(proposal.id),
                    "status": proposal.status,
                    "revision": proposal.current_revision,
                    "payment_id": str(proposal.payment_id),
                    "amount": payment.amount,
                    "payer_name": payment.payer_name,
                    "source_account_id": payment.source_account_id,
                    "transaction_id": payment.transaction_id,
                    "booking_date": payment.booking_date,
                    "application_id": (
                        str(latest_application_id) if latest_application_id else None
                    ),
                }
            )
        return rows

    @app.get("/api/v1/proposals/{proposal_id}")
    def get_proposal(
        proposal_id: uuid.UUID, request: Request, db: Session = Depends(_db)
    ) -> dict[str, object]:
        _, workspace = _session(request, db)
        application_id = (
            select(ApplicationGroup.id)
            .where(ApplicationGroup.proposal_id == Proposal.id)
            .order_by(ApplicationGroup.created_at.desc(), ApplicationGroup.id.desc())
            .limit(1)
            .correlate(Proposal)
            .scalar_subquery()
        )
        used_cash = (
            select(func.coalesce(func.sum(CashApplication.amount), 0))
            .where(
                CashApplication.workspace_id == workspace.id,
                CashApplication.payment_id == Payment.id,
                CashApplication.active.is_(True),
            )
            .correlate(Payment)
            .scalar_subquery()
        )
        row = db.execute(
            select(
                Proposal,
                ProposalRevision,
                Payment,
                application_id.label("application_id"),
                used_cash.label("used_cash"),
            )
            .join(Payment, Payment.id == Proposal.payment_id)
            .outerjoin(
                ProposalRevision,
                and_(
                    ProposalRevision.proposal_id == Proposal.id,
                    ProposalRevision.revision == Proposal.current_revision,
                ),
            )
            .where(
                Proposal.id == proposal_id,
                Proposal.workspace_id == workspace.id,
                Payment.workspace_id == workspace.id,
            )
        ).one_or_none()
        if row is None:
            raise HTTPException(404, "proposal not found")
        proposal, revision, payment, latest_application_id, payment_used_cash = row
        invoice_ids = {
            item["invoice_id"]
            for item in (
                (revision.cash_lines if revision else [])
                + (revision.credit_lines if revision else [])
            )
        }
        cash_applied = (
            select(func.coalesce(func.sum(CashApplication.amount), 0))
            .where(
                CashApplication.workspace_id == workspace.id,
                CashApplication.invoice_id == Invoice.id,
                CashApplication.active.is_(True),
            )
            .correlate(Invoice)
            .scalar_subquery()
        )
        credit_applied = (
            select(func.coalesce(func.sum(CreditApplication.amount), 0))
            .where(
                CreditApplication.workspace_id == workspace.id,
                CreditApplication.invoice_id == Invoice.id,
                CreditApplication.active.is_(True),
            )
            .correlate(Invoice)
            .scalar_subquery()
        )
        invoice_rows = (
            list(
                db.execute(
                    select(
                        Invoice,
                        cash_applied.label("cash_applied"),
                        credit_applied.label("credit_applied"),
                    ).where(
                        Invoice.workspace_id == workspace.id,
                        Invoice.invoice_id.in_(invoice_ids),
                    )
                )
            )
            if invoice_ids
            else []
        )
        balances = {
            invoice.invoice_id: {
                "opening_amount": invoice.outstanding_amount,
                "cash_applied": int(invoice_cash),
                "credit_applied": int(invoice_credit),
                "remaining_amount": (
                    invoice.outstanding_amount - int(invoice_cash) - int(invoice_credit)
                ),
            }
            for invoice, invoice_cash, invoice_credit in invoice_rows
        }
        return {
            "proposal_id": str(proposal.id),
            "status": proposal.status,
            "revision": proposal.current_revision,
            "payment": {
                "id": str(payment.id),
                "amount": payment.amount,
                "reference": payment.reference,
                "payer_name": payment.payer_name,
                "source_account_id": payment.source_account_id,
                "transaction_id": payment.transaction_id,
                "booking_date": payment.booking_date,
            },
            "cash": revision.cash_lines if revision else [],
            "credits": revision.credit_lines if revision else [],
            "evidence": revision.evidence if revision else [],
            "alternatives": revision.alternatives if revision else [],
            "signals": revision.signals if revision else [],
            "reason": revision.reason if revision else None,
            "version_token": revision.version_token if revision else None,
            "balances": balances,
            "unapplied_cash": payment.amount - int(payment_used_cash),
            "application_id": (str(latest_application_id) if latest_application_id else None),
            "trace": {
                "mode": revision.provenance if revision else "rules-v1",
                **(revision.model_trace if revision else {}),
            },
            "interpretation": (
                revision.model_trace.get("interpretation")
                if revision and revision.model_trace.get("interpretation")
                else None
            ),
            "review_required": proposal.review_required,
        }

    @app.post("/api/v1/proposals/{proposal_id}/correct")
    def correct_proposal(
        proposal_id: uuid.UUID,
        body: CorrectionRequest,
        request: Request,
        db: Session = Depends(_db),
    ) -> dict[str, object]:
        _, workspace = _require_mutation(request, db)
        if workspace.mode == "preview":
            enforce_database_admission(db)
        try:
            proposal = ReconcileService(db).correct(
                workspace.id,
                proposal_id,
                body.expected_revision,
                [CashLine(x.invoice_id, x.amount) for x in body.cash],
                [CreditLine(x.credit_note_id, x.invoice_id, x.amount) for x in body.credits],
                body.reviewer,
            )
            return {
                "proposal_id": str(proposal.id),
                "revision": proposal.current_revision,
                "status": proposal.status,
            }
        except ServiceError as exc:
            raise _error(exc)

    @app.post("/api/v1/proposals/{proposal_id}/interpret")
    def interpret_proposal(
        proposal_id: uuid.UUID,
        body: InterpretationRequestBody,
        request: Request,
        db: Session = Depends(_db),
    ) -> dict[str, object]:
        record, workspace = _require_mutation(request, db)
        if workspace.mode == "preview" and not record.provider_access:
            raise HTTPException(403, "provider invite is required")
        if workspace.mode == "preview":
            enforce_database_admission(db)
        try:
            outcome = INTERPRETATION_WORKFLOW.run(
                db,
                workspace_id=workspace.id,
                session_id=record.id,
                proposal_id=proposal_id,
                mode=body.mode,
            )
        except ServiceError as exc:
            raise _error(exc)
        except RuntimeError as exc:
            raise HTTPException(
                503,
                detail={"code": "interpretation_unavailable", "message": str(exc)},
            ) from exc
        proposal = db.scalar(
            select(Proposal).where(
                Proposal.id == proposal_id,
                Proposal.workspace_id == workspace.id,
            )
        )
        if proposal is None:
            raise HTTPException(404, "proposal not found")
        return {
            "proposal_id": str(proposal.id),
            "revision": proposal.current_revision,
            "status": proposal.status,
            "interpretation": outcome.api_dict(),
        }

    @app.post("/api/v1/proposals/{proposal_id}/apply")
    def apply_proposal(
        proposal_id: uuid.UUID, body: ApplyRequest, request: Request, db: Session = Depends(_db)
    ) -> dict[str, object]:
        _, workspace = _require_mutation(request, db)
        if workspace.mode == "preview":
            enforce_database_admission(db)
        try:
            group = ReconcileService(db).apply(
                workspace.id,
                proposal_id,
                body.expected_revision,
                body.version_token,
                body.reviewer,
                body.idempotency_key,
            )
            return {
                "application_id": str(group.id),
                "proposal_id": str(group.proposal_id),
                "revision": group.proposal_revision,
                "reversed": group.reversed_at is not None,
            }
        except ServiceError as exc:
            raise _error(exc)

    @app.post("/api/v1/applications/{application_id}/reverse")
    def reverse_application(
        application_id: uuid.UUID,
        body: ReverseRequest,
        request: Request,
        db: Session = Depends(_db),
    ) -> dict[str, object]:
        _, workspace = _require_mutation(request, db)
        try:
            group = ReconcileService(db).reverse(
                workspace.id, application_id, body.reviewer, body.reason, body.idempotency_key
            )
            return {"application_id": str(group.id), "reversed": group.reversed_at is not None}
        except ServiceError as exc:
            raise _error(exc)

    @app.get("/api/v1/exports/applications.csv")
    def export_applications(request: Request, db: Session = Depends(_db)) -> Response:
        _, workspace = _session(request, db)
        stream = io.StringIO(newline="")
        writer = csv.writer(stream, lineterminator="\r\n")
        writer.writerow(
            [
                "application_id",
                "type",
                "payment_id",
                "invoice_id",
                "credit_note_id",
                "amount",
                "active",
                "created_at",
            ]
        )
        groups = db.scalars(
            select(ApplicationGroup)
            .where(ApplicationGroup.workspace_id == workspace.id)
            .order_by(ApplicationGroup.created_at)
        )

        def safe(value: object) -> object:
            text = str(value)
            return "'" + text if text[:1] in "=+-@" else value

        for group in groups:
            for cash_row in db.scalars(
                select(CashApplication).where(CashApplication.application_group_id == group.id)
            ):
                invoice = db.get(Invoice, cash_row.invoice_id)
                for values in [
                    [
                        group.id,
                        "cash",
                        group.payment_id,
                        invoice.invoice_id if invoice else "",
                        "",
                        cash_row.amount,
                        cash_row.active,
                        group.created_at.isoformat(),
                    ]
                ]:
                    writer.writerow([safe(v) for v in values])
            for credit_row in db.scalars(
                select(CreditApplication).where(CreditApplication.application_group_id == group.id)
            ):
                invoice, credit = (
                    db.get(Invoice, credit_row.invoice_id),
                    db.get(CreditNote, credit_row.credit_note_id),
                )
                writer.writerow(
                    [
                        safe(v)
                        for v in [
                            group.id,
                            "credit",
                            group.payment_id,
                            invoice.invoice_id if invoice else "",
                            credit.credit_note_id if credit else "",
                            credit_row.amount,
                            credit_row.active,
                            group.created_at.isoformat(),
                        ]
                    ]
                )
        return Response(
            stream.getvalue().encode(),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=applications.csv"},
        )

    @app.get("/api/v1/sample")
    def sample() -> Response:
        return Response(
            sample_zip(),
            media_type="application/zip",
            headers={"Content-Disposition": "attachment; filename=reconcile-sample.zip"},
        )

    frontend_dir = Path(os.getenv("RECONCILE_FRONTEND_DIR", "frontend/dist"))
    if frontend_dir.is_dir():
        app.mount("/", StaticFiles(directory=frontend_dir, html=True), name="frontend")

    return app


app = create_app()
