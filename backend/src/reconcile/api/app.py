from __future__ import annotations

import csv
import hashlib
import io
import json
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

from reconcile.api.cases import get_case, list_cases
from reconcile.api.comparison import compare_snapshot
from reconcile.api.sample import is_exact_sample_packet, sample_zip
from reconcile.api.schemas import (
    ApplyRequest,
    ComparisonRequest,
    CorrectionRequest,
    InterpretationRequestBody,
    ReverseRequest,
    SessionRequest,
)
from reconcile.config import interpretation_settings, provider_invite_hash, server_mode
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
from reconcile.ml.runtime import ACTIVE_RULES_IDENTITY
from reconcile.persistence.db import SessionLocal, readiness, session_scope
from reconcile.persistence.maintenance import (
    cleanup_expired_preview_workspaces,
    enforce_database_admission,
)
from reconcile.persistence.models import (
    ApplicationGroup,
    AuditEvent,
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
from reconcile.persistence.service import ReconcileService, ServiceError, _trace_with_history

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


def _interpretation_enabled(record: DbSession) -> bool:
    try:
        return record.provider_access and interpretation_settings().enabled
    except RuntimeError:
        return False


def _capabilities(
    record: DbSession,
    proposal: Proposal | None = None,
    application: ApplicationGroup | None = None,
) -> dict[str, bool]:
    status = proposal.status if proposal is not None else None
    return {
        "interpret": _interpretation_enabled(record)
        and status == "NEEDS_REVIEW",
        "correct": proposal is not None
        and status not in {"APPLIED", "REVERSED"},
        "apply": status == "PROPOSED",
        "reverse": application is not None
        and application.reversed_at is None
        and status == "APPLIED",
    }


def _session_response(
    record: DbSession, workspace: Workspace, csrf_token: str
) -> dict[str, object]:
    return {
        "mode": workspace.mode,
        "expires_at": record.expires_at.isoformat(),
        "csrf_token": csrf_token,
        "provider_access": record.provider_access,
        "active_engine": ACTIVE_RULES_IDENTITY,
        "capabilities": _capabilities(record),
    }


def _case_audit(
    db: Session, workspace_id: uuid.UUID, case_id: str
) -> AuditEvent | None:
    for event in db.scalars(
        select(AuditEvent)
        .where(
            AuditEvent.workspace_id == workspace_id,
            AuditEvent.action == "case.open",
        )
        .order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc())
    ):
        if event.payload.get("case_id") == case_id:
            return event
    return None


def _case_for_payment(
    db: Session, workspace_id: uuid.UUID, source_id: uuid.UUID
) -> dict[str, str] | None:
    for event in db.scalars(
        select(AuditEvent)
        .where(
            AuditEvent.workspace_id == workspace_id,
            AuditEvent.action == "case.open",
        )
        .order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc())
    ):
        source_ids = set(event.payload.get("source_ids", []))
        if str(source_id) in source_ids:
            return {
                "id": str(event.payload.get("case_id", "")),
                "version": str(event.payload.get("scenario_version", "")),
            }
    return None


def _latest_application(
    db: Session, workspace_id: uuid.UUID, proposal_id: uuid.UUID
) -> ApplicationGroup | None:
    return db.scalar(
        select(ApplicationGroup)
        .where(
            ApplicationGroup.workspace_id == workspace_id,
            ApplicationGroup.proposal_id == proposal_id,
        )
        .order_by(ApplicationGroup.created_at.desc(), ApplicationGroup.id.desc())
    )


def _application_audits(
    db: Session, workspace_id: uuid.UUID, proposal_id: uuid.UUID
) -> list[AuditEvent]:
    group_ids = list(
        db.scalars(
            select(ApplicationGroup.id).where(
                ApplicationGroup.workspace_id == workspace_id,
                ApplicationGroup.proposal_id == proposal_id,
            )
        )
    )
    if not group_ids:
        return []
    return list(
        db.scalars(
            select(AuditEvent)
            .where(
                AuditEvent.workspace_id == workspace_id,
                AuditEvent.action.in_(("application.apply", "application.reverse")),
                AuditEvent.entity_id.in_(group_ids),
            )
            .order_by(AuditEvent.created_at, AuditEvent.id)
        )
    )


def _latest_comparison(
    db: Session,
    workspace_id: uuid.UUID,
    proposal_id: uuid.UUID,
    revision: int,
    input_fingerprint: str | None,
) -> dict[str, object] | None:
    for event in db.scalars(
        select(AuditEvent)
        .where(
            AuditEvent.workspace_id == workspace_id,
            AuditEvent.action == "proposal.compare",
            AuditEvent.entity_id == proposal_id,
        )
        .order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc())
    ):
        if (
            event.payload.get("revision") == revision
            and event.payload.get("input_fingerprint") == input_fingerprint
        ):
            return event.payload
    return None


def _case_response(
    db: Session,
    workspace_id: uuid.UUID,
    packet: object,
    *,
    resumed: bool,
) -> dict[str, object]:
    """Return the persisted case handles without exposing registry answers."""

    case_id = getattr(packet, "case_id")
    scenario_version = getattr(packet, "scenario_version")
    account_id = getattr(packet, "payment_source_account_id")
    transaction_id = getattr(packet, "payment_transaction_id")
    payment = db.scalar(
        select(Payment).where(
            Payment.workspace_id == workspace_id,
            Payment.source_account_id == account_id,
            Payment.transaction_id == transaction_id,
        )
    )
    proposal = (
        db.scalar(
            select(Proposal)
            .where(Proposal.workspace_id == workspace_id, Proposal.payment_id == payment.id)
            .order_by(Proposal.updated_at.desc(), Proposal.id.desc())
        )
        if payment is not None
        else None
    )
    jobs = (
        [
            str(job.id)
            for job in db.scalars(
                select(Job)
                .where(Job.workspace_id == workspace_id, Job.kind == "match-payment")
                .order_by(Job.created_at, Job.id)
            )
            if job.payload.get("payment_id") == str(payment.id)
        ]
        if payment is not None
        else []
    )
    return {
        "case_id": case_id,
        "scenario_version": scenario_version,
        "payment_id": str(payment.id) if payment is not None else None,
        "proposal_id": str(proposal.id) if proposal is not None else None,
        "jobs": jobs,
        "resumed": resumed,
    }


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

    @app.get("/api/v1/cases")
    def cases(request: Request, db: Session = Depends(_db)) -> dict[str, object]:
        _session(request, db)
        return {"version": "v1", "cases": list_cases()}

    @app.get("/api/v1/evaluation")
    def evaluation(request: Request, db: Session = Depends(_db)) -> dict[str, object]:
        _session(request, db)
        summary_path = Path(__file__).with_name("evaluation_summary.json")
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise HTTPException(
                503,
                detail={
                    "code": "evaluation_unavailable",
                    "message": "evaluation summary unavailable",
                },
            ) from exc
        if not isinstance(summary, dict):
            raise HTTPException(
                503,
                detail={"code": "evaluation_unavailable", "message": "evaluation summary invalid"},
            )
        return {"active_engine": ACTIVE_RULES_IDENTITY, **summary}

    @app.post("/api/v1/cases/{case_id}/open")
    def open_case(case_id: str, request: Request, db: Session = Depends(_db)) -> dict[str, object]:
        _, workspace = _require_mutation(request, db)
        packet = get_case(case_id)
        if packet is None:
            raise HTTPException(404, "case not found")
        if workspace.mode == "preview":
            enforce_database_admission(db)
        locked_workspace = db.scalar(
            select(Workspace).where(Workspace.id == workspace.id).with_for_update()
        )
        if locked_workspace is None:
            raise HTTPException(404, "workspace not found")
        if _case_audit(db, locked_workspace.id, case_id) is not None:
            return _case_response(db, locked_workspace.id, packet, resumed=True)
        try:
            parsed = packet.parse(profile=locked_workspace.mode)
            service = ReconcileService(db)
            batch = service.validate_import(locked_workspace.id, parsed, locked_workspace.mode)
            bound_sources = service.sources_for_batch(locked_workspace.id, batch.id)
            source_ids = [str(source.id) for source in bound_sources]
            source_hashes = [source.sha256 for source in bound_sources]
            for source in bound_sources:
                if source.batch_id == batch.id:
                    source.source_metadata = {
                        **source.source_metadata,
                        "provenance": "case-registry",
                        "case_id": packet.case_id,
                        "scenario_version": packet.scenario_version,
                        "version": packet.scenario_version,
                    }
            db.add(
                AuditEvent(
                    workspace_id=locked_workspace.id,
                    action="case.open",
                    actor="system",
                    entity_id=batch.id,
                    payload={
                        "case_id": packet.case_id,
                        "scenario_version": packet.scenario_version,
                        "source_ids": source_ids,
                        "source_hashes": source_hashes,
                        "provenance": "case-registry",
                    },
                )
            )
            db.flush()
            service.commit_import(locked_workspace.id, batch.id)
            _wake_consumer(request)
            return _case_response(db, locked_workspace.id, packet, resumed=False)
        except ServiceError as exc:
            raise _error(exc) from exc
        except ValueError as exc:
            raise HTTPException(
                422, detail={"code": "validation", "message": str(exc)}
            ) from exc

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
                    response = JSONResponse(_session_response(session, workspace, csrf_token))
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
        response = JSONResponse(_session_response(session, workspace, csrf_token))
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
            for source in ReconcileService(db).sources_for_batch(workspace.id, record.id)
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
        raw_text = source.raw_bytes.decode("utf-8", errors="replace")[:100_000]
        text = None
        rows: list[dict[str, object]] = []
        issues = source.source_metadata.get("issues", [])
        try:
            if source.kind == "message":
                text = raw_text
            elif source.kind == "bank":
                rows = list(parse_csv_source("bank", source.raw_bytes, profile="local").rows[:2000])
            elif source.kind == "invoice":
                rows = list(
                    parse_csv_source("invoice", source.raw_bytes, profile="local").rows[:2000]
                )
            elif source.kind == "credit":
                rows = list(parse_credit_source(source.raw_bytes, profile="local").rows[:2000])
        except (UnicodeDecodeError, ValueError) as exc:
            issues = [*issues, {"code": "unreadable_source", "message": str(exc)}]
        return {
            "source_id": str(source.id),
            "kind": source.kind,
            "sha256": source.sha256,
            "bytes": len(source.raw_bytes),
            "text": text,
            "raw_text": raw_text,
            "version": source.source_metadata.get("version", 1),
            "rows": rows,
            "issues": issues,
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
        record, workspace = _session(request, db)
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
        latest_application = (
            db.get(ApplicationGroup, latest_application_id)
            if latest_application_id is not None
            else None
        )
        decision_trace = (
            _trace_with_history(
                revision.model_trace,
                _application_audits(db, workspace.id, proposal.id),
            )
            if revision is not None
            else None
        )
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
            "case": _case_for_payment(db, workspace.id, payment.source_id),
            "capabilities": _capabilities(record, proposal, latest_application),
            "decision_trace": decision_trace,
            "comparison": _latest_comparison(
                db,
                workspace.id,
                proposal.id,
                proposal.current_revision,
                (
                    revision.model_trace.get("input_fingerprint")
                    if revision is not None
                    else None
                ),
            ),
            "model_trace": revision.model_trace if revision else {},
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

    @app.post("/api/v1/proposals/{proposal_id}/compare")
    def compare_proposal(
        proposal_id: uuid.UUID,
        body: ComparisonRequest,
        request: Request,
        db: Session = Depends(_db),
    ) -> dict[str, object]:
        _, workspace = _require_mutation(request, db)
        if workspace.mode == "preview":
            enforce_database_admission(db)
        row = db.execute(
            select(Proposal, ProposalRevision, Payment)
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
        proposal, revision, payment = row
        if proposal.current_revision != body.expected_revision:
            raise HTTPException(
                409,
                detail={"code": "stale_revision", "message": "proposal revision is stale"},
            )
        trace = revision.model_trace if revision is not None else {}
        case = _case_for_payment(db, workspace.id, payment.source_id)
        comparison = compare_snapshot(
            trace,
            case_id=case["id"] if case is not None else None,
            case_version=case["version"] if case is not None else None,
        )
        payload = {
            "revision": proposal.current_revision,
            "input_fingerprint": comparison.get("input_fingerprint"),
            "methods": comparison["methods"],
        }
        db.add(
            AuditEvent(
                workspace_id=workspace.id,
                action="proposal.compare",
                actor="system",
                entity_id=proposal.id,
                payload=payload,
            )
        )
        db.commit()
        return payload

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
