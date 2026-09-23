"""PostgreSQL-only integration checks for locking/idempotency behavior."""

from __future__ import annotations

import hashlib
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from threading import Barrier, Event

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

import reconcile.api.app as app_module
from reconcile.api.app import _db, create_app
from reconcile.api.sample import SAMPLE_CONTEXT, SAMPLE_FILES
from reconcile.ingest.parsers import parse_batch, parse_message_context
from reconcile.interpretation.budget import (
    BudgetExceeded,
    BudgetPolicy,
    RateCard,
    Usage,
    finalize_attempt,
    reconcile_unknown_attempt,
    reserve_attempt,
)
from reconcile.interpretation.workflow import WorkflowOutcome, WorkflowProgress, compile_workflow
from reconcile.jobs.queue import claim_one, run_once
from reconcile.persistence.db import normalize_database_url
from reconcile.persistence.maintenance import (
    cleanup_expired_preview_workspaces,
    enforce_database_admission,
)
from reconcile.persistence.models import (
    ApplicationGroup,
    AuditEvent,
    Base,
    CashApplication,
    CreditApplication,
    CreditNote,
    ImportBatch,
    InterpretationBudgetCounter,
    InterpretationCall,
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

pytestmark = pytest.mark.postgres


@pytest.fixture(scope="module")
def db_engine():
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL is not set")
    if url.startswith("sqlite"):
        pytest.fail("TEST_DATABASE_URL must be PostgreSQL")
    if os.getenv("ALLOW_DESTRUCTIVE_TEST_DB") != "1":
        pytest.fail(
            "PostgreSQL tests recreate their dedicated schema; "
            "set ALLOW_DESTRUCTIVE_TEST_DB=1 to opt in"
        )
    # Every test object lives below this explicitly dedicated schema.
    engine = create_engine(
        normalize_database_url(url),
        pool_pre_ping=True,
    ).execution_options(schema_translate_map={None: "reconcile_test"})
    with engine.begin() as conn:
        conn.execute(text("CREATE SCHEMA IF NOT EXISTS reconcile_test"))
    Base.metadata.create_all(engine)
    yield engine
    Base.metadata.drop_all(engine)
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA IF EXISTS reconcile_test CASCADE"))
    engine.dispose()


@pytest.fixture()
def session(db_engine):
    factory = sessionmaker(bind=db_engine, expire_on_commit=False)
    with factory() as db:
        yield db


def _api_client(session, monkeypatch: pytest.MonkeyPatch) -> tuple[object, TestClient]:
    monkeypatch.setenv("RECONCILE_MODE", "local")
    monkeypatch.setenv("RECONCILE_LLM_ENABLED", "0")
    api = create_app()
    api.dependency_overrides[_db] = lambda: session
    client = TestClient(api)
    response = client.post("/api/v1/session", json={})
    assert response.status_code == 200, response.text
    client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
    return api, client


def _run_all_jobs(client: TestClient) -> None:
    for _ in range(20):
        response = client.post("/api/v1/jobs/run-once")
        assert response.status_code == 200, response.text
        if response.json()["job_id"] is None:
            return
    raise AssertionError("bounded case job drain did not reach idle")


def _manual_packet() -> tuple[bytes, bytes, bytes]:
    bank = (
        b"source_account_id,transaction_id,booking_date,payer_name,reference,amount,currency\n"
        b"manual-account,manual-payment,2026-01-15,Manual Customer,"
        + "—".encode()
        + b",1000.00,MXN\n"
    )
    invoices = (
        b"customer_id,customer_name,invoice_id,issued_date,due_date,balance_as_of,"
        b"outstanding_amount,currency\n"
        b"manual-customer,Manual Customer,manual-invoice,2025-12-01,2026-01-01,"
        b"2026-01-15,1000.00,MXN\n"
    )
    return bank, invoices, b"Please review this payment."


def _open_case(client: TestClient, case_id: str) -> dict[str, object]:
    response = client.post(f"/api/v1/cases/{case_id}/open")
    assert response.status_code == 200, response.text
    return response.json()


def test_workspace_scoped_and_application_idempotency(session) -> None:
    service = ReconcileService(session)
    workspace = service.create_workspace()
    batch = ImportBatch(workspace_id=workspace.id)
    session.add(batch)
    session.flush()
    source = Source(
        workspace_id=workspace.id,
        batch_id=batch.id,
        kind="bank",
        sha256=uuid.uuid4().hex,
        raw_bytes=b"",
    )
    session.add(source)
    session.flush()
    payment = Payment(
        workspace_id=workspace.id,
        source_id=source.id,
        source_account_id="acct",
        transaction_id="tx",
        booking_date=date(2026, 1, 15),
        payer_name="C",
        reference="invoice i",
        amount=10000,
        currency="MXN",
    )
    invoice = Invoice(
        workspace_id=workspace.id,
        source_id=source.id,
        customer_id="c",
        customer_name="C",
        invoice_id="i",
        issued_date=date(2026, 1, 1),
        due_date=date(2026, 1, 15),
        balance_as_of=date(2026, 1, 15),
        outstanding_amount=10000,
        currency="MXN",
    )
    session.add_all([payment, invoice])
    session.commit()
    proposal = service.process_match(workspace.id, payment.id)
    revision = session.get(type(proposal), proposal.id).current_revision
    token = (
        session.query(ProposalRevision)
        .filter_by(proposal_id=proposal.id, revision=revision)
        .one()
        .version_token
    )
    applied = service.apply(workspace.id, proposal.id, revision, token, "reviewer", "key-1")
    assert (
        service.apply(workspace.id, proposal.id, revision, token, "reviewer", "key-1").id
        == applied.id
    )
    with pytest.raises(ServiceError, match="different payload"):
        service.apply(workspace.id, proposal.id, revision, token, "other", "key-1")
    reversed_group = service.reverse(
        workspace.id, applied.id, "reviewer", "correction", "reverse-1"
    )
    assert reversed_group.reversed_at is not None
    assert (
        service.reverse(workspace.id, applied.id, "reviewer", "correction", "reverse-1").id
        == applied.id
    )


def test_reimport_conflict_does_not_overwrite_opening_balance(session) -> None:
    service = ReconcileService(session)
    workspace = service.create_workspace()
    bank = (
        b"source_account_id,transaction_id,booking_date,payer_name,reference,amount,currency\n"
        b"a,t,2026-01-15,C,invoice i,100,MXN\n"
    )
    invoices = (
        b"customer_id,customer_name,invoice_id,issued_date,due_date,balance_as_of,"
        b"outstanding_amount,currency\n"
        b"c,C,i,2026-01-01,2026-01-15,2026-01-15,100,MXN\n"
    )
    first_batch = service.validate_import(workspace.id, parse_batch(bank, invoices), "local")
    session.commit()
    service.commit_import(workspace.id, first_batch.id)
    changed_bank = bank.replace(b",100,MXN", b",200,MXN")
    changed_invoices = invoices.replace(b",100,MXN", b",200,MXN")
    second_batch = service.validate_import(
        workspace.id, parse_batch(changed_bank, changed_invoices), "local"
    )
    session.commit()
    result = service.commit_import(workspace.id, second_batch.id)
    assert result["conflicts"]
    invoice = session.query(Invoice).filter_by(workspace_id=workspace.id, invoice_id="i").one()
    assert invoice.outstanding_amount == 10000
    assert invoice.conflicted is True


def test_committed_message_stales_pending_match_and_requeues_with_new_evidence(session) -> None:
    service = ReconcileService(session)
    workspace = service.create_workspace()
    bank = (
        b"source_account_id,transaction_id,booking_date,payer_name,reference,amount,currency\n"
        b"acct,message-pending,2026-01-15,C,unidentified payment,100,MXN\n"
    )
    invoices = (
        b"customer_id,customer_name,invoice_id,issued_date,due_date,balance_as_of,"
        b"outstanding_amount,currency\n"
        b"c,C,i,2026-01-01,2026-01-15,2026-01-15,100,MXN\n"
    )
    first = service.validate_import(workspace.id, parse_batch(bank, invoices), "local")
    session.commit()
    service.commit_import(workspace.id, first.id)
    payment = session.scalar(select(Payment).where(Payment.workspace_id == workspace.id))
    assert payment is not None
    proposal = service.process_match(workspace.id, payment.id)
    assert proposal.status == "NEEDS_REVIEW"
    assert session.scalar(select(Job).where(Job.workspace_id == workspace.id)) is not None

    second = service.validate_import(
        workspace.id,
        parse_batch(
            bank,
            invoices,
            message=b"Apply invoice i.",
            message_context=parse_message_context(
                "2026-01-15T12:00:00+00:00", "acct", "message-pending"
            ),
        ),
        "local",
    )
    session.commit()
    service.commit_import(workspace.id, second.id)
    session.refresh(payment)
    session.refresh(proposal)
    assert payment.version == 2
    assert proposal.status == "STALE"

    job = run_once(session, owner="message-rematch", workspace_id=workspace.id)
    assert job is not None and job.status == "SUCCEEDED"
    session.refresh(proposal)
    assert proposal.current_revision == 2
    assert proposal.status == "NEEDS_REVIEW"


def test_committed_message_flags_applied_evidence_without_rewriting_history(session) -> None:
    service = ReconcileService(session)
    workspace = service.create_workspace()
    bank = (
        b"source_account_id,transaction_id,booking_date,payer_name,reference,amount,currency\n"
        b"acct,message-applied,2026-01-15,C,invoice i,100,MXN\n"
    )
    invoices = (
        b"customer_id,customer_name,invoice_id,issued_date,due_date,balance_as_of,"
        b"outstanding_amount,currency\n"
        b"c,C,i,2026-01-01,2026-01-15,2026-01-15,100,MXN\n"
    )
    first = service.validate_import(workspace.id, parse_batch(bank, invoices), "local")
    session.commit()
    service.commit_import(workspace.id, first.id)
    payment = session.scalar(select(Payment).where(Payment.workspace_id == workspace.id))
    assert payment is not None
    proposal = service.process_match(workspace.id, payment.id)
    revision = session.scalar(
        select(ProposalRevision).where(
            ProposalRevision.proposal_id == proposal.id,
            ProposalRevision.revision == 1,
        )
    )
    assert revision is not None
    service.apply(
        workspace.id,
        proposal.id,
        revision.revision,
        revision.version_token,
        "reviewer",
        "applied-message",
    )
    running = claim_one(session, owner="old-match", workspace_id=workspace.id)
    assert running is not None and running.status == "RUNNING"

    second = service.validate_import(
        workspace.id,
        parse_batch(
            bank,
            invoices,
            message=b"The customer also sent a note.",
            message_context=parse_message_context(
                "2026-01-15T12:00:00+00:00", "acct", "message-applied"
            ),
        ),
        "local",
    )
    session.commit()
    result = service.commit_import(workspace.id, second.id)
    session.refresh(payment)
    session.refresh(proposal)
    assert payment.version == 3
    assert proposal.status == "APPLIED"
    assert proposal.review_required is True
    assert proposal.current_revision == 1
    assert result["jobs"]
    assert session.query(ProposalRevision).filter_by(proposal_id=proposal.id).count() == 1

    rematch = run_once(session, owner="new-match", workspace_id=workspace.id)
    assert rematch is not None and rematch.status == "SUCCEEDED"
    session.refresh(proposal)
    assert proposal.status == "APPLIED"
    assert proposal.current_revision == 1
    assert session.query(ApplicationGroup).filter_by(proposal_id=proposal.id).count() == 1


def test_duplicate_message_bytes_with_changed_context_are_rejected(session) -> None:
    service = ReconcileService(session)
    workspace = service.create_workspace()
    bank = (
        b"source_account_id,transaction_id,booking_date,payer_name,reference,amount,currency\n"
        b"acct,context-one,2026-01-15,C,invoice i,100,MXN\n"
    )
    invoices = (
        b"customer_id,customer_name,invoice_id,issued_date,due_date,balance_as_of,"
        b"outstanding_amount,currency\n"
        b"c,C,i,2026-01-01,2026-01-15,2026-01-15,100,MXN\n"
    )
    message = b"The payment reference is i."
    first = service.validate_import(
        workspace.id,
        parse_batch(
            bank,
            invoices,
            message=message,
            message_context=parse_message_context(
                "2026-01-15T12:00:00+00:00", "acct", "context-one"
            ),
        ),
        "local",
    )
    session.commit()
    service.commit_import(workspace.id, first.id)

    with pytest.raises(ServiceError, match="different payment"):
        service.validate_import(
            workspace.id,
            parse_batch(
                bank,
                invoices,
                message=message,
                message_context=parse_message_context(
                    "2026-01-15T12:00:00+00:00", "acct", "context-two"
                ),
            ),
            "local",
        )


def test_repeated_validation_before_commit_preserves_bound_sources(session) -> None:
    service = ReconcileService(session)
    workspace = service.create_workspace()
    bank = (
        b"source_account_id,transaction_id,booking_date,payer_name,reference,amount,currency\n"
        b"acct,bound-before-commit,2026-01-15,C,invoice bound,100,MXN\n"
    )
    invoices = (
        b"customer_id,customer_name,invoice_id,issued_date,due_date,balance_as_of,"
        b"outstanding_amount,currency\n"
        b"c,C,bound,2026-01-01,2026-01-15,2026-01-15,100,MXN\n"
    )
    parsed = parse_batch(
        bank,
        invoices,
        message=b"Apply invoice bound.",
        message_context=parse_message_context(
            "2026-01-15T12:00:00+00:00", "acct", "bound-before-commit"
        ),
    )
    first = service.validate_import(workspace.id, parsed, "local")
    second = service.validate_import(workspace.id, parsed, "local")
    session.commit()

    result = service.commit_import(workspace.id, second.id)

    assert result["payments"] == 1
    assert result["invoices"] == 1
    assert session.query(Source).filter_by(workspace_id=workspace.id).count() == 3
    assert session.get(ImportBatch, first.id).status == "VALIDATED"
    assert session.get(ImportBatch, second.id).status == "COMMITTED"
    payment = session.scalar(
        select(Payment).where(
            Payment.workspace_id == workspace.id,
            Payment.transaction_id == "bound-before-commit",
        )
    )
    assert payment is not None
    message_source = session.scalar(
        select(Source).where(Source.workspace_id == workspace.id, Source.kind == "message")
    )
    assert message_source is not None
    assert message_source.batch_id == first.id
    assert message_source.status == "COMMITTED"
    proposal = service.process_match(workspace.id, payment.id)
    assert proposal.status == "PROPOSED"
    request, _ = compile_workflow()._request(
        session,
        workspace_id=workspace.id,
        proposal=proposal,
        mode="direct",
        policy=BudgetPolicy(),
    )
    assert request.source_spans


def test_identical_committed_message_reimport_is_idempotent(session) -> None:
    service = ReconcileService(session)
    workspace = service.create_workspace()
    bank = (
        b"source_account_id,transaction_id,booking_date,payer_name,reference,amount,currency\n"
        b"acct,identical-message,2026-01-15,C,invoice identical,100,MXN\n"
    )
    invoices = (
        b"customer_id,customer_name,invoice_id,issued_date,due_date,balance_as_of,"
        b"outstanding_amount,currency\n"
        b"c,C,identical,2026-01-01,2026-01-15,2026-01-15,100,MXN\n"
    )
    parsed = parse_batch(
        bank,
        invoices,
        message=b"Apply invoice identical.",
        message_context=parse_message_context(
            "2026-01-15T12:00:00+00:00", "acct", "identical-message"
        ),
    )
    first = service.validate_import(workspace.id, parsed, "local")
    session.commit()
    service.commit_import(workspace.id, first.id)
    payment = session.scalar(
        select(Payment).where(
            Payment.workspace_id == workspace.id,
            Payment.transaction_id == "identical-message",
        )
    )
    assert payment is not None
    proposal = service.process_match(workspace.id, payment.id)
    assert proposal.status == "PROPOSED"
    completed = run_once(session, owner="identical-message", workspace_id=workspace.id)
    assert completed is not None and completed.status == "SUCCEEDED"
    session.refresh(payment)
    jobs_before = session.query(Job).filter_by(workspace_id=workspace.id).count()

    second = service.validate_import(workspace.id, parsed, "local")
    session.commit()
    result = service.commit_import(workspace.id, second.id)
    session.refresh(payment)

    assert payment.version == 1
    assert result["jobs"] == []
    assert session.query(Job).filter_by(workspace_id=workspace.id).count() == jobs_before


def test_new_message_import_and_apply_keep_proposal_first_lock_order(session) -> None:
    service = ReconcileService(session)
    workspace = service.create_workspace()
    bank = (
        b"source_account_id,transaction_id,booking_date,payer_name,reference,amount,currency\n"
        b"acct,lock-order,2026-01-15,C,invoice lock-invoice,100,MXN\n"
    )
    invoices = (
        b"customer_id,customer_name,invoice_id,issued_date,due_date,balance_as_of,"
        b"outstanding_amount,currency\n"
        b"c,C,lock-invoice,2026-01-01,2026-01-15,2026-01-15,100,MXN\n"
    )
    first = service.validate_import(workspace.id, parse_batch(bank, invoices), "local")
    session.commit()
    service.commit_import(workspace.id, first.id)
    payment = session.scalar(
        select(Payment).where(
            Payment.workspace_id == workspace.id, Payment.transaction_id == "lock-order"
        )
    )
    assert payment is not None
    initial_job = run_once(session, owner="initial-lock-order", workspace_id=workspace.id)
    assert initial_job is not None and initial_job.status == "SUCCEEDED"
    proposal = session.scalar(
        select(Proposal).where(
            Proposal.workspace_id == workspace.id, Proposal.payment_id == payment.id
        )
    )
    assert proposal is not None
    revision = session.scalar(
        select(ProposalRevision).where(
            ProposalRevision.proposal_id == proposal.id,
            ProposalRevision.revision == proposal.current_revision,
        )
    )
    assert revision is not None
    session.commit()

    factory = sessionmaker(bind=session.get_bind(), expire_on_commit=False)
    proposal_ready = Event()
    import_reached = Barrier(2)
    allow_import = Event()

    class CoordinatedService(ReconcileService):
        def _lock_payment_proposals(self, workspace_id, payment_id):
            import_reached.wait(timeout=60)
            assert allow_import.wait(timeout=60)
            return super()._lock_payment_proposals(workspace_id, payment_id)

    changed_bank = bank.replace(b"\n", b"\r\n")
    changed_parsed = parse_batch(
        changed_bank,
        invoices,
        message=b"Evidence changed after application.",
        message_context=parse_message_context(
            "2026-01-15T12:00:00+00:00", "acct", "lock-order"
        ),
    )
    pending_batch = service.validate_import(workspace.id, changed_parsed, "local")
    session.commit()

    def apply_worker() -> ApplicationGroup:
        db = factory()
        try:
            db.execute(text("SET LOCAL lock_timeout = '5s'"))
            db.execute(
                select(Proposal)
                .where(Proposal.id == proposal.id)
                .with_for_update()
            ).scalar_one()
            proposal_ready.set()
            import_reached.wait(timeout=60)
            result = ReconcileService(db).apply(
                workspace.id,
                proposal.id,
                revision.revision,
                revision.version_token,
                "reviewer",
                "lock-order-apply",
            )
            return result
        finally:
            allow_import.set()
            db.close()

    def import_worker() -> dict[str, object]:
        db = factory()
        try:
            import_service = CoordinatedService(db)
            return import_service.commit_import(workspace.id, pending_batch.id)
        finally:
            db.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        apply_future = pool.submit(apply_worker)
        assert proposal_ready.wait(timeout=60)
        import_future = pool.submit(import_worker)
        applied = apply_future.result(timeout=90)
        imported = import_future.result(timeout=90)

    assert applied.id
    assert imported["jobs"]
    session.refresh(payment)
    session.refresh(proposal)
    assert payment.version == 3
    assert proposal.status == "APPLIED"
    assert proposal.review_required is True


def test_expired_job_lease_is_reclaimed(session) -> None:
    service = ReconcileService(session)
    workspace = service.create_workspace()
    session.add(Job(workspace_id=workspace.id, kind="unknown", payload={}))
    session.commit()
    first = claim_one(session, owner="first", lease_seconds=0, workspace_id=workspace.id)
    second = claim_one(session, owner="second", lease_seconds=60, workspace_id=workspace.id)
    assert first is not None and second is not None
    assert second.id == first.id
    assert second.attempts == 2


def test_preview_cleanup_is_bounded_and_never_deletes_local_workspaces(session) -> None:
    expired = ReconcileService(session).create_workspace("preview")
    retained = ReconcileService(session).create_workspace("local")
    expired.last_active_at = now_utc() - timedelta(hours=25)
    retained.last_active_at = now_utc() - timedelta(hours=25)
    batch = ImportBatch(workspace_id=expired.id)
    session.add(batch)
    session.flush()
    session.add(
        Source(
            workspace_id=expired.id,
            batch_id=batch.id,
            kind="bank",
            sha256=uuid.uuid4().hex,
            raw_bytes=b"synthetic",
        )
    )
    session.commit()

    assert cleanup_expired_preview_workspaces(session, batch_size=1) == 1
    assert session.get(Workspace, expired.id) is None
    assert session.get(Workspace, retained.id) is not None
    assert session.scalar(select(Source).where(Source.workspace_id == expired.id)) is None


def test_database_admission_stops_at_configured_limit(session, monkeypatch) -> None:
    monkeypatch.setenv("RECONCILE_MAX_DATABASE_BYTES", "1")
    with pytest.raises(ServiceError, match="storage admission is paused"):
        enforce_database_admission(session)


def test_interpretation_budget_reservation_is_transactional_and_reconciled(session) -> None:
    service = ReconcileService(session)
    workspace = service.create_workspace()
    session.commit()
    visitor = uuid.uuid4()
    policy = BudgetPolicy(execution_attempts=1)
    rate = RateCard()

    call_id = reserve_attempt(
        session,
        workspace_id=workspace.id,
        session_id=visitor,
        execution_id="phase4-test",
        mode="direct",
        requested_model="deepseek-flash",
        attempt=1,
        policy=policy,
        rate_card=rate,
    )
    call = session.get(InterpretationCall, call_id)
    assert call is not None
    assert call.status == "RESERVED"
    assert call.reservation_microdollars == 4_258

    with pytest.raises(BudgetExceeded, match="attempts budget exhausted"):
        reserve_attempt(
            session,
            workspace_id=workspace.id,
            session_id=visitor,
            execution_id="phase4-test",
            mode="direct",
            requested_model="deepseek-flash",
            attempt=2,
            policy=policy,
            rate_card=rate,
        )

    finalized = finalize_attempt(
        session,
        call_id=call_id,
        policy=policy,
        rate_card=rate,
        status="SUCCEEDED",
        usage=Usage(input_tokens=1_000, output_tokens=100, cached_input_tokens=200),
        response_model="deepseek-v4.1-flash",
        latency_ms=50,
    )
    assert finalized.status == "SUCCEEDED"
    assert finalized.estimated_microdollars == 362
    assert finalized.reservation_retained is False
    counters = session.query(InterpretationBudgetCounter).all()
    assert all(counter.reserved_microdollars == 0 for counter in counters)
    assert all(counter.committed_microdollars == 362 for counter in counters)


def test_interpretation_timeout_retains_possible_billing_reservation(session) -> None:
    service = ReconcileService(session)
    workspace = service.create_workspace()
    session.commit()
    policy = BudgetPolicy()
    rate = RateCard()
    call_id = reserve_attempt(
        session,
        workspace_id=workspace.id,
        session_id=uuid.uuid4(),
        execution_id=f"timeout-{uuid.uuid4()}",
        mode="hybrid",
        requested_model="deepseek-flash",
        attempt=1,
        policy=policy,
        rate_card=rate,
    )

    finalized = finalize_attempt(
        session,
        call_id=call_id,
        policy=policy,
        rate_card=rate,
        status="FAILED",
        error_code="timeout",
        billing_unknown=True,
    )

    assert finalized.status == "UNKNOWN_BILLING"
    assert finalized.reservation_retained is True
    execution = session.get(InterpretationBudgetCounter, f"execution:{finalized.execution_id}")
    assert execution is not None
    assert execution.reserved_microdollars == 4_258

    reconciled = reconcile_unknown_attempt(
        session,
        call_id=call_id,
        policy=policy,
        rate_card=rate,
        usage=Usage(input_tokens=100, output_tokens=20),
    )

    assert reconciled.status == "FAILED_BILLED"
    assert reconciled.reservation_retained is False
    assert reconciled.estimated_microdollars == 54
    session.refresh(execution)
    assert execution.reserved_microdollars == 0
    assert execution.committed_microdollars == 54


def test_two_payments_cannot_consume_one_invoice(session) -> None:
    service = ReconcileService(session)
    workspace = service.create_workspace()
    batch = ImportBatch(workspace_id=workspace.id)
    session.add(batch)
    session.flush()
    source = Source(
        workspace_id=workspace.id,
        batch_id=batch.id,
        kind="bank",
        sha256=uuid.uuid4().hex,
        raw_bytes=b"",
    )
    session.add(source)
    session.flush()
    invoice = Invoice(
        workspace_id=workspace.id,
        source_id=source.id,
        customer_id="c",
        customer_name="C",
        invoice_id="shared",
        issued_date=date(2026, 1, 1),
        due_date=date(2026, 1, 15),
        balance_as_of=date(2026, 1, 15),
        outstanding_amount=10000,
        currency="MXN",
    )
    first = Payment(
        workspace_id=workspace.id,
        source_id=source.id,
        source_account_id="acct",
        transaction_id="one",
        booking_date=date(2026, 1, 15),
        payer_name="C",
        reference="invoice shared",
        amount=10000,
        currency="MXN",
    )
    second = Payment(
        workspace_id=workspace.id,
        source_id=source.id,
        source_account_id="acct",
        transaction_id="two",
        booking_date=date(2026, 1, 15),
        payer_name="C",
        reference="invoice shared",
        amount=10000,
        currency="MXN",
    )
    session.add_all([invoice, first, second])
    session.commit()
    proposal_one = service.process_match(workspace.id, first.id)
    proposal_two = service.process_match(workspace.id, second.id)
    token_one = (
        session.query(ProposalRevision)
        .filter_by(proposal_id=proposal_one.id, revision=1)
        .one()
        .version_token
    )
    token_two = (
        session.query(ProposalRevision)
        .filter_by(proposal_id=proposal_two.id, revision=1)
        .one()
        .version_token
    )
    service.apply(workspace.id, proposal_one.id, 1, token_one, "reviewer", "one")
    with pytest.raises(ServiceError, match="stale"):
        service.apply(workspace.id, proposal_two.id, 1, token_two, "reviewer", "two")


def test_two_payments_can_settle_different_invoices(session) -> None:
    service = ReconcileService(session)
    workspace = service.create_workspace()
    batch = ImportBatch(workspace_id=workspace.id)
    session.add(batch)
    session.flush()
    source = Source(
        workspace_id=workspace.id,
        batch_id=batch.id,
        kind="bank",
        sha256=uuid.uuid4().hex,
        raw_bytes=b"",
    )
    session.add(source)
    session.flush()
    invoices = [
        Invoice(
            workspace_id=workspace.id,
            source_id=source.id,
            customer_id="c",
            customer_name="C",
            invoice_id=identifier,
            issued_date=date(2026, 1, 1),
            due_date=date(2026, 1, 15),
            balance_as_of=date(2026, 1, 15),
            outstanding_amount=10000,
            currency="MXN",
        )
        for identifier in ("first", "second")
    ]
    payments = [
        Payment(
            workspace_id=workspace.id,
            source_id=source.id,
            source_account_id="acct",
            transaction_id=identifier,
            booking_date=date(2026, 1, 15),
            payer_name="C",
            reference=f"invoice {identifier}",
            amount=10000,
            currency="MXN",
        )
        for identifier in ("first", "second")
    ]
    session.add_all([*invoices, *payments])
    session.commit()

    for payment in payments:
        proposal = service.process_match(workspace.id, payment.id)
        revision = session.query(ProposalRevision).filter_by(proposal_id=proposal.id).one()
        service.apply(
            workspace.id,
            proposal.id,
            revision.revision,
            revision.version_token,
            "reviewer",
            f"apply-{payment.transaction_id}",
        )


def test_shadow_prediction_trace_persists_without_changing_rules(
    session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RECONCILE_RANKER_MODE", "shadow")
    service = ReconcileService(session)
    workspace = service.create_workspace()
    batch = ImportBatch(workspace_id=workspace.id)
    session.add(batch)
    session.flush()
    source = Source(
        workspace_id=workspace.id,
        batch_id=batch.id,
        kind="bank",
        sha256=uuid.uuid4().hex,
        raw_bytes=b"",
    )
    session.add(source)
    session.flush()
    payment = Payment(
        workspace_id=workspace.id,
        source_id=source.id,
        source_account_id="acct",
        transaction_id="shadow",
        booking_date=date(2026, 1, 15),
        payer_name="C",
        reference="invoice shadow-invoice",
        amount=10_000,
        currency="MXN",
    )
    invoice = Invoice(
        workspace_id=workspace.id,
        source_id=source.id,
        customer_id="c",
        customer_name="C",
        invoice_id="shadow-invoice",
        issued_date=date(2026, 1, 1),
        due_date=date(2026, 1, 15),
        balance_as_of=date(2026, 1, 15),
        outstanding_amount=10_000,
        currency="MXN",
    )
    session.add_all([payment, invoice])
    session.commit()

    proposal = service.process_match(workspace.id, payment.id)
    revision = session.query(ProposalRevision).filter_by(proposal_id=proposal.id).one()
    assert proposal.status == "PROPOSED"
    assert revision.provenance == "rules-v2-conservative"
    assert revision.cash_lines == [{"invoice_id": "shadow-invoice", "amount": 10_000}]
    assert revision.model_trace["ranker"]["status"] == "observed"
    assert revision.model_trace["ranker"]["model_id"] == "ranker-ml-v1-logistic"


def test_api_session_uses_server_mode_and_csrf(session, monkeypatch) -> None:
    monkeypatch.setenv("RECONCILE_MODE", "local")
    api = create_app()
    api.dependency_overrides[_db] = lambda: session
    client = TestClient(api)
    response = client.post("/api/v1/session", json={})
    assert response.status_code == 200
    payload = response.json()
    assert "workspace_id" not in payload
    assert payload["mode"] == "local"
    assert client.get("/api/v1/proposals").status_code == 200
    assert client.post("/api/v1/jobs/run-once").status_code == 403
    client.headers["X-CSRF-Token"] = payload["csrf_token"]
    assert client.post("/api/v1/jobs/run-once").status_code == 200
    api.dependency_overrides.clear()


def test_api_run_once_reports_job_claimed_by_lifecycle_consumer(session, monkeypatch) -> None:
    monkeypatch.setenv("RECONCILE_MODE", "local")
    api = create_app()
    api.dependency_overrides[_db] = lambda: session
    client = TestClient(api)
    session_response = client.post("/api/v1/session", json={})
    csrf = session_response.json()["csrf_token"]
    workspace = session.scalar(select(Workspace).order_by(Workspace.created_at.desc()))
    assert workspace is not None
    claimed = Job(
        workspace_id=workspace.id,
        kind="match-payment",
        status="RUNNING",
    )
    session.add(claimed)
    session.commit()
    monkeypatch.setattr(app_module, "run_once", lambda *args, **kwargs: None)
    client.headers["X-CSRF-Token"] = csrf

    response = client.post("/api/v1/jobs/run-once")

    assert response.status_code == 200
    assert response.json() == {"job_id": str(claimed.id), "status": "RUNNING"}
    api.dependency_overrides.clear()


def test_registered_cases_open_and_run_through_the_real_api(session, monkeypatch) -> None:
    api, client = _api_client(session, monkeypatch)
    try:
        listing = client.get("/api/v1/cases")
        assert listing.status_code == 200
        cases = listing.json()["cases"]
        assert [item["id"] for item in cases] == [
            "straightforward",
            "bundle",
            "correction",
            "insufficient",
            "adversarial",
        ]
        opened = {
            case_id: _open_case(client, case_id)
            for case_id in [item["id"] for item in cases]
        }
        assert all(
            item["payment_id"] and item["proposal_id"] is None for item in opened.values()
        )
        _run_all_jobs(client)

        expected_status = {
            "straightforward": "PROPOSED",
            "bundle": "PROPOSED",
            "correction": "NEEDS_REVIEW",
            "insufficient": "NEEDS_REVIEW",
            "adversarial": "NEEDS_REVIEW",
        }
        details: dict[str, dict[str, object]] = {}
        for case_id, handle in opened.items():
            proposals = client.get("/api/v1/proposals").json()
            proposal = next(
                item for item in proposals if item["payment_id"] == handle["payment_id"]
            )
            detail_response = client.get(f"/api/v1/proposals/{proposal['proposal_id']}")
            assert detail_response.status_code == 200, detail_response.text
            detail = detail_response.json()
            details[case_id] = detail
            assert detail["status"] == expected_status[case_id]
            assert detail["case"] == {"id": case_id, "version": "v1", "variant": "original"}
            assert detail["decision_trace"]["source"] == "rules"
            assert any(
                stage["id"] == "parse-observations"
                for stage in detail["decision_trace"]["stages"]
            )

        bundle = details["bundle"]
        assert bundle["payment"]["amount"] == 5_400_000
        assert {row["invoice_id"] for row in bundle["cash"]} == {
            "case-bundle-target-a",
            "case-bundle-target-b",
        }
        assert {row["amount"] for row in bundle["cash"]} == {3_000_000, 2_400_000}
        assert bundle["credits"] == [
            {
                "credit_note_id": "case-bundle-credit",
                "invoice_id": "case-bundle-target-b",
                "amount": 100_000,
            }
        ]
        assert "case-bundle-decoy" not in {row["invoice_id"] for row in bundle["cash"]}
        validation = next(
            stage
            for stage in bundle["decision_trace"]["stages"]
            if stage["id"] == "financial-validation"
        )
        assert validation["details"]["checks"] == ["opening_snapshot", "structural_allocation"]
        assert validation["details"]["transactional_live_balance"] == "not_executed"
    finally:
        api.dependency_overrides.clear()


def test_case_open_resumes_without_new_entities_or_jobs(session, monkeypatch) -> None:
    api, client = _api_client(session, monkeypatch)
    try:
        first = _open_case(client, "straightforward")
        _run_all_jobs(client)
        workspace_id = session.scalar(select(Workspace.id).order_by(Workspace.created_at.desc()))
        assert workspace_id is not None
        counts_before = {
            model.__name__: session.query(model).filter_by(workspace_id=workspace_id).count()
            for model in (Source, Payment, Invoice, CreditNote, Proposal, Job)
        }
        second = _open_case(client, "straightforward")
        assert second["resumed"] is True
        assert second["payment_id"] == first["payment_id"]
        assert second["proposal_id"] == first["proposal_id"] or second["proposal_id"] is not None
        assert second["jobs"] == first["jobs"]
        for model in (Source, Payment, Invoice, CreditNote, Proposal, Job):
            assert (
                session.query(model).filter_by(workspace_id=workspace_id).count()
                == counts_before[model.__name__]
            )
        assert (
            session.query(AuditEvent)
            .filter_by(workspace_id=workspace_id, action="case.open")
            .count()
            == 1
        )
    finally:
        api.dependency_overrides.clear()


def test_case_api_csrf_and_workspace_isolation_cover_sources_and_details(
    session, monkeypatch
) -> None:
    api, client_a = _api_client(session, monkeypatch)
    try:
        handle = _open_case(client_a, "straightforward")
        _run_all_jobs(client_a)
        proposal = client_a.get("/api/v1/proposals").json()[0]
        detail = client_a.get(f"/api/v1/proposals/{proposal['proposal_id']}").json()
        source_id = detail["decision_trace"]["snapshot"]["sources"][0]["source_id"]
        source = client_a.get(f"/api/v1/sources/{source_id}")
        assert source.status_code == 200
        assert source.json()["raw_text"]

        _, client_b = _api_client(session, monkeypatch)
        csrf = client_b.headers.pop("X-CSRF-Token")
        denied = client_b.post("/api/v1/cases/adversarial/open")
        assert denied.status_code == 403
        client_b.headers["X-CSRF-Token"] = csrf
        variant_denied = client_b.post(
            "/api/v1/cases/straightforward/variant",
            json={"expected_revision": 1, "variant": "ambiguous"},
        )
        assert variant_denied.status_code == 409
        assert variant_denied.json()["error"]["code"] == "variant_unavailable"
        assert client_b.get(f"/api/v1/proposals/{proposal['proposal_id']}").status_code == 404
        assert client_b.get(f"/api/v1/sources/{source_id}").status_code == 404
        assert handle["payment_id"]
    finally:
        api.dependency_overrides.clear()


def test_case_fingerprint_is_stable_across_clutter_and_workspaces(session, monkeypatch) -> None:
    api, cluttered = _api_client(session, monkeypatch)
    try:
        _open_case(cluttered, "straightforward")
        _open_case(cluttered, "bundle")
        manual_bank, manual_invoices, manual_message = _manual_packet()
        files = {
            "bank": ("manual-bank.csv", manual_bank, "text/csv"),
            "invoices": ("manual-invoices.csv", manual_invoices, "text/csv"),
            "message": ("manual-message.txt", manual_message, "text/plain"),
        }
        validated = cluttered.post(
            "/api/v1/imports/validate",
            files=files,
            data={
                "message_time": "2026-01-15T12:00:00+00:00",
                "payment_source_account_id": "manual-account",
                "payment_transaction_id": "manual-payment",
            },
        )
        assert validated.status_code == 200, validated.text
        batch_id = validated.json()["batch_id"]
        assert cluttered.post(f"/api/v1/imports/{batch_id}/commit").status_code == 200
        insufficient = _open_case(cluttered, "insufficient")
        _run_all_jobs(cluttered)
        cluttered_detail = next(
            client_item
            for client_item in cluttered.get("/api/v1/proposals").json()
            if client_item["payment_id"] == insufficient["payment_id"]
        )
        cluttered_trace = cluttered.get(
            f"/api/v1/proposals/{cluttered_detail['proposal_id']}"
        ).json()["decision_trace"]

        _, fresh = _api_client(session, monkeypatch)
        fresh_handle = _open_case(fresh, "insufficient")
        _run_all_jobs(fresh)
        fresh_summary = next(
            client_item
            for client_item in fresh.get("/api/v1/proposals").json()
            if client_item["payment_id"] == fresh_handle["payment_id"]
        )
        fresh_trace = fresh.get(
            f"/api/v1/proposals/{fresh_summary['proposal_id']}"
        ).json()["decision_trace"]
        assert cluttered_trace["input_fingerprint"] == fresh_trace["input_fingerprint"]
    finally:
        api.dependency_overrides.clear()


def test_case_trace_survives_correction_apply_reverse_with_audits(session, monkeypatch) -> None:
    api, client = _api_client(session, monkeypatch)
    try:
        handle = _open_case(client, "straightforward")
        _run_all_jobs(client)
        proposal_row = next(
            item
            for item in client.get("/api/v1/proposals").json()
            if item["payment_id"] == handle["payment_id"]
        )
        proposal_id = proposal_row["proposal_id"]
        initial = client.get(f"/api/v1/proposals/{proposal_id}").json()
        original_trace = initial["model_trace"]
        source_id = original_trace["snapshot"]["sources"][0]["source_id"]

        correction = client.post(
            f"/api/v1/proposals/{proposal_id}/correct",
            json={
                "expected_revision": 1,
                "cash": [{"invoice_id": "case-straightforward-invoice", "amount": 100_000}],
                "credits": [],
                "reviewer": "case-reviewer",
            },
        )
        assert correction.status_code == 200, correction.text
        corrected = client.get(f"/api/v1/proposals/{proposal_id}").json()
        assert corrected["revision"] == 2
        assert (
            corrected["model_trace"]["input_fingerprint"]
            == original_trace["input_fingerprint"]
        )
        assert any(
            stage["id"] == "human-correction-r2"
            for stage in corrected["decision_trace"]["stages"]
        )
        old_revision = session.scalar(
            select(ProposalRevision).where(
                ProposalRevision.proposal_id == uuid.UUID(proposal_id),
                ProposalRevision.revision == 1,
            )
        )
        assert old_revision is not None
        assert old_revision.model_trace == original_trace

        apply_response = client.post(
            f"/api/v1/proposals/{proposal_id}/apply",
            json={
                "expected_revision": 2,
                "version_token": corrected["version_token"],
                "reviewer": "case-reviewer",
                "idempotency_key": "case-apply",
            },
        )
        assert apply_response.status_code == 200, apply_response.text
        application_id = apply_response.json()["application_id"]
        applied = client.get(f"/api/v1/proposals/{proposal_id}").json()
        assert applied["status"] == "APPLIED"
        assert applied["capabilities"] == {
            "interpret": False,
            "correct": False,
            "apply": False,
            "reverse": True,
        }
        assert any(
            stage["id"] == "application-apply"
            for stage in applied["decision_trace"]["stages"]
        )
        applied_variant = client.post(
            "/api/v1/cases/straightforward/variant",
            json={"expected_revision": 2, "variant": "ambiguous"},
        )
        assert applied_variant.status_code == 409
        assert applied_variant.json()["error"]["code"] == "immutable_revision"

        reverse_response = client.post(
            f"/api/v1/applications/{application_id}/reverse",
            json={
                "reviewer": "case-reviewer",
                "reason": "case lifecycle test",
                "idempotency_key": "case-reverse",
            },
        )
        assert reverse_response.status_code == 200, reverse_response.text
        reversed_detail = client.get(f"/api/v1/proposals/{proposal_id}").json()
        assert reversed_detail["status"] == "REVERSED"
        assert reversed_detail["capabilities"]["reverse"] is False
        assert any(
            stage["id"] == "application-reverse"
            for stage in reversed_detail["decision_trace"]["stages"]
        )
        audits = list(
            session.scalars(
                select(AuditEvent).where(
                    AuditEvent.workspace_id == session.scalar(
                        select(Workspace.id).order_by(Workspace.created_at.desc())
                    ),
                    AuditEvent.action.in_(
                        ("proposal.correct", "application.apply", "application.reverse")
                    ),
                )
            )
        )
        assert {event.action for event in audits} == {
            "proposal.correct",
            "application.apply",
            "application.reverse",
        }
        assert source_id in {item["source_id"] for item in original_trace["snapshot"]["sources"]}
        variant_response = client.post(
            "/api/v1/cases/straightforward/variant",
            json={"expected_revision": 2, "variant": "ambiguous"},
        )
        assert variant_response.status_code == 409
        assert variant_response.json()["error"]["code"] == "immutable_revision"
    finally:
        api.dependency_overrides.clear()


def test_case_variants_supersede_and_restore_immutable_evidence(session, monkeypatch) -> None:
    api, client = _api_client(session, monkeypatch)
    try:
        handle = _open_case(client, "straightforward")
        _run_all_jobs(client)
        proposal_id = next(
            item
            for item in client.get("/api/v1/proposals").json()
            if item["payment_id"] == handle["payment_id"]
        )["proposal_id"]
        detail = client.get(f"/api/v1/proposals/{proposal_id}").json()
        original_revision = detail["revision"]
        workspace_id = session.scalar(select(Workspace.id).order_by(Workspace.created_at.desc()))
        assert workspace_id is not None
        source_count = session.query(Source).filter_by(workspace_id=workspace_id).count()
        payment_id = uuid.UUID(handle["payment_id"])
        payment_before = session.get(Payment, payment_id)
        assert payment_before is not None
        version_before = payment_before.version
        financial_counts_before = {
            model.__name__: session.query(model)
            .filter_by(workspace_id=payment_before.workspace_id)
            .count()
            for model in (ApplicationGroup, CashApplication, CreditApplication)
        }
        original_fingerprint = detail["decision_trace"]["input_fingerprint"]
        compared = client.post(
            f"/api/v1/proposals/{proposal_id}/compare",
            json={"expected_revision": original_revision},
        )
        assert compared.status_code == 200, compared.text
        csrf_token = client.headers.pop("X-CSRF-Token")
        csrf_denied = client.post(
            "/api/v1/cases/straightforward/variant",
            json={"expected_revision": original_revision, "variant": "ambiguous"},
        )
        assert csrf_denied.status_code == 403
        client.headers["X-CSRF-Token"] = csrf_token
        stale_variant = client.post(
            "/api/v1/cases/straightforward/variant",
            json={"expected_revision": original_revision + 1, "variant": "ambiguous"},
        )
        assert stale_variant.status_code == 409
        assert stale_variant.json()["error"]["code"] == "stale_revision"

        changed = client.post(
            "/api/v1/cases/straightforward/variant",
            json={"expected_revision": original_revision, "variant": "ambiguous"},
        )
        assert changed.status_code == 200, changed.text
        assert changed.json()["variant"] == "ambiguous"
        assert changed.json()["scenario_version"] == "v1:ambiguous"
        pending_detail = client.get(f"/api/v1/proposals/{proposal_id}").json()
        assert pending_detail["status"] == "STALE"
        assert pending_detail["comparison"] is None
        pending_resume = client.post(
            "/api/v1/cases/straightforward/variant",
            json={"expected_revision": original_revision, "variant": "ambiguous"},
        )
        assert pending_resume.status_code == 200, pending_resume.text
        assert pending_resume.json()["resumed"] is True
        pending_compare = client.post(
            f"/api/v1/proposals/{proposal_id}/compare",
            json={"expected_revision": original_revision},
        )
        assert pending_compare.status_code == 409
        assert pending_compare.json()["error"]["code"] == "comparison_unavailable"
        assert (
            session.query(Source).filter_by(workspace_id=payment_before.workspace_id).count()
            == source_count + 1
        )
        payment_after = session.get(Payment, payment_id)
        assert payment_after is not None
        assert payment_after.version == version_before + 1
        _run_all_jobs(client)
        ambiguous = client.get(f"/api/v1/proposals/{proposal_id}").json()
        assert ambiguous["status"] == "NEEDS_REVIEW"
        assert ambiguous["decision_trace"]["input_fingerprint"] != original_fingerprint
        assert ambiguous["comparison"] is None
        ambiguous_revision = ambiguous["revision"]
        ambiguous_source = next(
            source
            for source in session.scalars(select(Source))
            if source.kind == "message"
            and source.source_metadata.get("variant") == "ambiguous"
            and source.workspace_id == payment_before.workspace_id
        )
        ambiguous_hash = ambiguous_source.sha256
        ambiguous_bytes = bytes(ambiguous_source.raw_bytes)
        counts_after_change = {
            model.__name__: session.query(model)
            .filter_by(workspace_id=payment_before.workspace_id)
            .count()
            for model in (Source, Payment, Invoice, CreditNote, Proposal, Job)
        }
        resumed = client.post(
            "/api/v1/cases/straightforward/variant",
            json={"expected_revision": ambiguous_revision, "variant": "ambiguous"},
        )
        assert resumed.status_code == 200
        assert resumed.json()["resumed"] is True
        for model in (Source, Payment, Invoice, CreditNote, Proposal, Job):
            assert (
                session.query(model).filter_by(workspace_id=payment_before.workspace_id).count()
                == counts_after_change[model.__name__]
            )

        prompt_first = client.post(
            "/api/v1/cases/straightforward/variant",
            json={"expected_revision": ambiguous_revision, "variant": "prompt_like"},
        )
        assert prompt_first.status_code == 200, prompt_first.text
        assert prompt_first.json()["variant"] == "prompt_like"
        assert prompt_first.json()["scenario_version"] == "v1:prompt_like"
        _run_all_jobs(client)
        prompt_first_detail = client.get(f"/api/v1/proposals/{proposal_id}").json()
        prompt_first_revision = prompt_first_detail["revision"]
        source_count_after_prompt = session.query(Source).filter_by(
            workspace_id=payment_before.workspace_id
        ).count()
        assert source_count_after_prompt == source_count + 2

        back_to_ambiguous = client.post(
            "/api/v1/cases/straightforward/variant",
            json={"expected_revision": prompt_first_revision, "variant": "ambiguous"},
        )
        assert back_to_ambiguous.status_code == 200, back_to_ambiguous.text
        assert back_to_ambiguous.json()["variant"] == "ambiguous"
        assert (
            session.query(Source).filter_by(workspace_id=payment_before.workspace_id).count()
            == source_count_after_prompt
        )
        payment_back = session.get(Payment, payment_id)
        assert payment_back is not None
        assert payment_back.version == version_before + 3
        ambiguous_again_source = session.get(Source, ambiguous_source.id)
        assert ambiguous_again_source is not None
        assert ambiguous_again_source.sha256 == ambiguous_hash
        assert ambiguous_again_source.raw_bytes == ambiguous_bytes
        for model in (ApplicationGroup, CashApplication, CreditApplication):
            assert (
                session.query(model).filter_by(workspace_id=payment_before.workspace_id).count()
                == financial_counts_before[model.__name__]
            )
        _run_all_jobs(client)
        ambiguous_again = client.get(f"/api/v1/proposals/{proposal_id}").json()
        assert ambiguous_again["status"] == "NEEDS_REVIEW"

        restored = client.post(
            "/api/v1/cases/straightforward/variant",
            json={"expected_revision": ambiguous_again["revision"], "variant": "original"},
        )
        assert restored.status_code == 200, restored.text
        assert restored.json()["variant"] == "original"
        assert restored.json()["scenario_version"] == "v1"
        payment_restored = session.get(Payment, payment_id)
        assert payment_restored is not None
        assert payment_restored.version == version_before + 4
        _run_all_jobs(client)
        restored_detail = client.get(f"/api/v1/proposals/{proposal_id}").json()
        restored_revision = restored_detail["revision"]
        prompt = client.post(
            "/api/v1/cases/straightforward/variant",
            json={"expected_revision": restored_revision, "variant": "prompt_like"},
        )
        assert prompt.status_code == 200, prompt.text
        assert prompt.json()["variant"] == "prompt_like"
        assert prompt.json()["scenario_version"] == "v1:prompt_like"
        assert (
            session.query(Source).filter_by(workspace_id=payment_before.workspace_id).count()
            == source_count_after_prompt
        )
        payment_final = session.get(Payment, payment_id)
        assert payment_final is not None
        assert payment_final.version == version_before + 5
        _run_all_jobs(client)
        final_detail = client.get(f"/api/v1/proposals/{proposal_id}").json()
        assert final_detail["status"] == "NEEDS_REVIEW"
        assert final_detail["revision"] > restored_revision
        assert (
            session.query(ProposalRevision)
            .filter_by(proposal_id=uuid.UUID(proposal_id))
            .count()
            >= 4
        )
        messages = list(
            session.scalars(
                select(Source).where(
                    Source.workspace_id == payment_before.workspace_id,
                    Source.kind == "message",
                )
            )
        )
        assert sum(source.status == "SUPERSEDED" for source in messages) >= 1
        assert any(source.status == "COMMITTED" for source in messages)
    finally:
        api.dependency_overrides.clear()


def test_reliability_lab_uses_real_validators_and_idempotency(session, monkeypatch) -> None:
    api, client = _api_client(session, monkeypatch)
    try:
        handle = _open_case(client, "straightforward")
        _run_all_jobs(client)
        proposal_id = next(
            item
            for item in client.get("/api/v1/proposals").json()
            if item["payment_id"] == handle["payment_id"]
        )["proposal_id"]
        detail = client.get(f"/api/v1/proposals/{proposal_id}").json()
        before_duplicate = client.post(
            f"/api/v1/proposals/{proposal_id}/reliability",
            json={"expected_revision": detail["revision"], "experiment": "duplicate_apply"},
        )
        assert before_duplicate.status_code == 409
        assert before_duplicate.json()["error"]["code"] == "unavailable_condition"
        for experiment, validator in (
            ("invalid_allocation", "validate_allocation"),
            ("invalid_citation", "validate_result"),
            ("stale_apply", "ReconcileService.apply"),
        ):
            response = client.post(
                f"/api/v1/proposals/{proposal_id}/reliability",
                json={"expected_revision": detail["revision"], "experiment": experiment},
            )
            assert response.status_code == 200, response.text
            payload = response.json()
            assert payload["synthetic"] is True
            assert payload["validator"] == validator
            assert payload["passed"] is True
            assert payload["effects_before"] == payload["effects_after"]
            assert payload["application_id"] is None

        applied = client.post(
            f"/api/v1/proposals/{proposal_id}/apply",
            json={
                "expected_revision": detail["revision"],
                "version_token": detail["version_token"],
                "reviewer": "lab-reviewer",
                "idempotency_key": "lab-apply",
            },
        )
        assert applied.status_code == 200, applied.text
        application_id = applied.json()["application_id"]
        application_count = session.query(ApplicationGroup).filter_by(
            workspace_id=session.scalar(select(Workspace.id).order_by(Workspace.created_at.desc()))
        ).count()
        duplicate = client.post(
            f"/api/v1/proposals/{proposal_id}/reliability",
            json={"expected_revision": detail["revision"], "experiment": "duplicate_apply"},
        )
        assert duplicate.status_code == 200, duplicate.text
        duplicate_payload = duplicate.json()
        assert duplicate_payload["passed"] is True
        assert duplicate_payload["application_id"] == application_id
        assert duplicate_payload["effects_before"] == duplicate_payload["effects_after"]
        assert (
            session.query(ApplicationGroup).filter_by(
                workspace_id=session.scalar(select(Workspace.id).order_by(Workspace.created_at.desc()))
            ).count()
            == application_count
        )
    finally:
        api.dependency_overrides.clear()


def test_case_variant_rolls_back_sources_and_jobs_as_one_transaction(session, monkeypatch) -> None:
    api, client = _api_client(session, monkeypatch)
    try:
        handle = _open_case(client, "straightforward")
        _run_all_jobs(client)
        workspace_id = session.scalar(select(Workspace.id).order_by(Workspace.created_at.desc()))
        assert workspace_id is not None
        payment_id = uuid.UUID(handle["payment_id"])
        payment = session.get(Payment, payment_id)
        assert payment is not None
        counts_before = {
            model.__name__: session.query(model).filter_by(workspace_id=workspace_id).count()
            for model in (Source, Payment, Invoice, CreditNote, Proposal, Job, AuditEvent)
        }
        version_before = payment.version
        original_commit = ReconcileService.commit_import

        def fail_commit(self, *args, **kwargs):
            original_commit(self, *args, **kwargs)
            raise ServiceError("injected_variant_failure", "injected failure", 409)

        monkeypatch.setattr(ReconcileService, "commit_import", fail_commit)
        failed = client.post(
            "/api/v1/cases/straightforward/variant",
            json={"expected_revision": 1, "variant": "ambiguous"},
        )
        assert failed.status_code == 409
        assert failed.json()["error"]["code"] == "injected_variant_failure"
        monkeypatch.setattr(ReconcileService, "commit_import", original_commit)
        session.rollback()
        for model in (Source, Payment, Invoice, CreditNote, Proposal, Job, AuditEvent):
            assert (
                session.query(model).filter_by(workspace_id=workspace_id).count()
                == counts_before[model.__name__]
            )
        payment_after = session.get(Payment, payment_id)
        assert payment_after is not None
        assert payment_after.version == version_before
        assert (
            session.query(AuditEvent)
            .filter_by(workspace_id=workspace_id, action="case.variant")
            .count()
            == 0
        )
    finally:
        api.dependency_overrides.clear()


def test_comparison_is_authenticated_non_actionable_and_revision_scoped(
    session, monkeypatch
) -> None:
    api, client = _api_client(session, monkeypatch)
    try:
        handle = _open_case(client, "straightforward")
        _run_all_jobs(client)
        proposal_row = next(
            item
            for item in client.get("/api/v1/proposals").json()
            if item["payment_id"] == handle["payment_id"]
        )
        proposal_id = proposal_row["proposal_id"]
        detail = client.get(f"/api/v1/proposals/{proposal_id}").json()
        fingerprint = detail["model_trace"]["input_fingerprint"]
        payment = session.get(Payment, uuid.UUID(handle["payment_id"]))
        assert payment is not None
        versions_before = {
            "payment": payment.version,
            "invoice": session.scalar(
                select(Invoice.version).where(
                    Invoice.workspace_id == payment.workspace_id,
                    Invoice.invoice_id == "case-straightforward-invoice",
                )
            ),
        }

        compared = client.post(
            f"/api/v1/proposals/{proposal_id}/compare",
            json={"expected_revision": detail["revision"]},
        )
        assert compared.status_code == 200, compared.text
        payload = compared.json()
        assert payload["revision"] == 1
        assert payload["input_fingerprint"] == fingerprint
        assert [item["method"] for item in payload["methods"]] == [
            "rules",
            "bounded_correction",
            "shadow_ranker",
            "direct",
            "hybrid",
        ]
        assert all(item["actionable"] is False for item in payload["methods"])
        assert payload["methods"][0]["source"] == "rules"
        assert payload["methods"][2]["source"] == "local"
        assert payload["methods"][3]["status"] == "unavailable"
        assert payload["methods"][4]["status"] == "unavailable"
        assert session.get(Payment, payment.id).version == versions_before["payment"]
        assert (
            session.scalar(
                select(Invoice.version).where(
                    Invoice.workspace_id == payment.workspace_id,
                    Invoice.invoice_id == "case-straightforward-invoice",
                )
            )
            == versions_before["invoice"]
        )
        assert client.get(f"/api/v1/proposals/{proposal_id}").json()["comparison"] == payload
        assert (
            session.query(AuditEvent)
            .filter_by(action="proposal.compare", entity_id=uuid.UUID(proposal_id))
            .count()
            == 1
        )
        _, client_b = _api_client(session, monkeypatch)
        denied = client_b.post(
            f"/api/v1/proposals/{proposal_id}/compare", json={"expected_revision": 1}
        )
        assert denied.status_code == 404
        csrf = client.headers.pop("X-CSRF-Token")
        missing_csrf = client.post(
            f"/api/v1/proposals/{proposal_id}/compare", json={"expected_revision": 1}
        )
        assert missing_csrf.status_code == 403
        assert missing_csrf.json()["error"]["code"] == "csrf_required"
        client.headers["X-CSRF-Token"] = csrf

        corrected = client.post(
            f"/api/v1/proposals/{proposal_id}/correct",
            json={
                "expected_revision": 1,
                "cash": [{"invoice_id": "case-straightforward-invoice", "amount": 100_000}],
                "credits": [],
                "reviewer": "comparison-reviewer",
            },
        )
        assert corrected.status_code == 200, corrected.text
        assert client.get(f"/api/v1/proposals/{proposal_id}").json()["comparison"] is None
        stale = client.post(
            f"/api/v1/proposals/{proposal_id}/compare",
            json={"expected_revision": 1},
        )
        assert stale.status_code == 409
        assert stale.json()["error"]["code"] == "stale_revision"
    finally:
        api.dependency_overrides.clear()


def test_evaluation_route_serves_packaged_summary_with_active_engine(session, monkeypatch) -> None:
    api, client = _api_client(session, monkeypatch)
    try:
        response = client.get("/api/v1/evaluation")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["active_engine"] == "rules-v2-conservative"
        assert payload["schema_version"] == "evaluation-summary-v1"
        assert payload["v2"]["status"] == "not_evaluated"
        assert "dataset" not in payload
    finally:
        api.dependency_overrides.clear()


def test_preview_public_provider_access_is_reversible_and_invites_persist(
    session, monkeypatch
) -> None:
    monkeypatch.setenv("RECONCILE_MODE", "preview")
    monkeypatch.setenv("RECONCILE_LLM_ENABLED", "1")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "server-only-test-key")
    monkeypatch.setenv("RECONCILE_LLM_EXECUTION_ID", "public-access-test")
    monkeypatch.setenv("RECONCILE_LLM_EXECUTION_BUDGET_USD", "0.01")
    monkeypatch.delenv("RECONCILE_PUBLIC_PROVIDER_ACCESS", raising=False)
    invite = "preview-provider-invite"
    monkeypatch.setenv(
        "RECONCILE_PROVIDER_INVITE_SHA256", hashlib.sha256(invite.encode()).hexdigest()
    )
    api = create_app()
    api.dependency_overrides[_db] = lambda: session
    client = TestClient(api, base_url="https://testserver")
    try:
        invite_only = client.post("/api/v1/session", json={})
        assert invite_only.status_code == 200, invite_only.text
        assert invite_only.json()["provider_access"] is False
        assert invite_only.json()["capabilities"]["interpret"] is False

        monkeypatch.setenv("RECONCILE_PUBLIC_PROVIDER_ACCESS", "1")
        new_client = TestClient(api, base_url="https://testserver")
        public_new = new_client.post("/api/v1/session", json={})
        assert public_new.status_code == 200, public_new.text
        assert public_new.json()["provider_access"] is True
        public_existing = client.post("/api/v1/session", json={})
        assert public_existing.status_code == 200, public_existing.text
        assert public_existing.json()["provider_access"] is True
        stored = session.scalar(select(DbSession).order_by(DbSession.created_at.desc()))
        assert stored is not None and stored.provider_access is False

        monkeypatch.setenv("RECONCILE_PUBLIC_PROVIDER_ACCESS", "0")
        revoked = client.post("/api/v1/session", json={})
        assert revoked.status_code == 200, revoked.text
        assert revoked.json()["provider_access"] is False

        invited = client.post("/api/v1/session", json={"invite_token": invite})
        assert invited.status_code == 200, invited.text
        assert invited.json()["provider_access"] is True
        monkeypatch.delenv("RECONCILE_PUBLIC_PROVIDER_ACCESS", raising=False)
        retained_invite = client.post("/api/v1/session", json={})
        assert retained_invite.status_code == 200, retained_invite.text
        assert retained_invite.json()["provider_access"] is True
    finally:
        api.dependency_overrides.clear()


def test_preview_public_access_routes_direct_and_hybrid_and_revokes_post(
    session, monkeypatch
) -> None:
    monkeypatch.setenv("RECONCILE_MODE", "preview")
    monkeypatch.setenv("RECONCILE_PUBLIC_PROVIDER_ACCESS", "1")
    monkeypatch.setenv("RECONCILE_LLM_ENABLED", "1")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "server-only-test-key")
    monkeypatch.setenv("RECONCILE_LLM_EXECUTION_ID", "public-access-test")
    monkeypatch.setenv("RECONCILE_LLM_EXECUTION_BUDGET_USD", "0.01")
    api = create_app()
    api.dependency_overrides[_db] = lambda: session
    client = TestClient(api, base_url="https://testserver")
    monkeypatch.setattr(
        app_module,
        "SessionLocal",
        sessionmaker(bind=session.get_bind(), expire_on_commit=False),
    )
    modes: list[str] = []

    class RecordingWorkflow:
        def run(self, _db_session, **kwargs):
            modes.append(kwargs["mode"])
            progress = kwargs.get("progress")
            if progress is not None:
                progress(
                    WorkflowProgress(
                        "reserve_and_call",
                        "running",
                        "provider is waiting",
                    )
                )
            return WorkflowOutcome("unavailable", "none", kwargs["mode"], failure_code="test")

    monkeypatch.setattr(app_module, "INTERPRETATION_WORKFLOW", RecordingWorkflow())
    try:
        session_response = client.post("/api/v1/session", json={})
        assert session_response.status_code == 200, session_response.text
        client.headers["X-CSRF-Token"] = session_response.json()["csrf_token"]
        opened = _open_case(client, "correction")
        _run_all_jobs(client)
        proposal = next(
            item
            for item in client.get("/api/v1/proposals").json()
            if item["payment_id"] == opened["payment_id"]
        )
        detail = client.get(f"/api/v1/proposals/{proposal['proposal_id']}")
        assert detail.status_code == 200, detail.text
        assert detail.json()["capabilities"]["interpret"] is True

        missing_csrf = client.post(
            f"/api/v1/proposals/{proposal['proposal_id']}/interpret/stream",
            json={"mode": "direct"},
            headers={"X-CSRF-Token": "stale-token"},
        )
        assert missing_csrf.status_code == 403
        assert missing_csrf.json()["error"]["code"] == "csrf_required"

        for mode in ("direct", "hybrid"):
            response = client.post(
                f"/api/v1/proposals/{proposal['proposal_id']}/interpret",
                json={"mode": mode},
            )
            assert response.status_code == 200, response.text
            assert response.json()["interpretation"]["mode"] == mode
        stream_response = client.post(
            f"/api/v1/proposals/{proposal['proposal_id']}/interpret/stream",
            json={"mode": "direct"},
        )
        assert stream_response.status_code == 200, stream_response.text
        assert "event: progress" in stream_response.text
        assert "event: complete" in stream_response.text
        assert modes == ["direct", "hybrid", "direct"]

        monkeypatch.setenv("RECONCILE_PUBLIC_PROVIDER_ACCESS", "0")
        refreshed = client.post("/api/v1/session", json={})
        assert refreshed.status_code == 200, refreshed.text
        assert refreshed.json()["provider_access"] is False
        client.headers["X-CSRF-Token"] = refreshed.json()["csrf_token"]
        revoked_detail = client.get(f"/api/v1/proposals/{proposal['proposal_id']}")
        assert revoked_detail.status_code == 200, revoked_detail.text
        assert revoked_detail.json()["capabilities"]["interpret"] is False
        denied = client.post(
            f"/api/v1/proposals/{proposal['proposal_id']}/interpret",
            json={"mode": "direct"},
        )
        assert denied.status_code == 403
        assert modes == ["direct", "hybrid", "direct"]
    finally:
        api.dependency_overrides.clear()


def test_preview_public_access_stays_disabled_when_runtime_is_killed(session, monkeypatch) -> None:
    monkeypatch.setenv("RECONCILE_MODE", "preview")
    monkeypatch.setenv("RECONCILE_PUBLIC_PROVIDER_ACCESS", "1")
    monkeypatch.setenv("RECONCILE_LLM_ENABLED", "0")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("RECONCILE_LLM_EXECUTION_ID", raising=False)
    monkeypatch.setenv("RECONCILE_LLM_EXECUTION_BUDGET_USD", "0")
    api = create_app()
    api.dependency_overrides[_db] = lambda: session
    client = TestClient(api, base_url="https://testserver")
    try:
        response = client.post("/api/v1/session", json={})
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["provider_access"] is False
        assert payload["capabilities"]["interpret"] is False
    finally:
        api.dependency_overrides.clear()


def test_preview_rejects_non_sample_upload(session, monkeypatch) -> None:
    monkeypatch.setenv("RECONCILE_MODE", "preview")
    invite = "phase-six-preview-invite"
    monkeypatch.setenv(
        "RECONCILE_PROVIDER_INVITE_SHA256", hashlib.sha256(invite.encode()).hexdigest()
    )
    api = create_app()
    api.dependency_overrides[_db] = lambda: session
    client = TestClient(api, base_url="https://testserver")
    session_response = client.post("/api/v1/session", json={})
    assert session_response.json()["provider_access"] is False
    unlocked = client.post("/api/v1/session", json={"invite_token": invite})
    assert unlocked.json()["provider_access"] is True
    csrf = unlocked.json()["csrf_token"]
    files = {
        "bank": ("bank.csv", b"not the built-in packet", "text/csv"),
        "invoices": ("invoices.csv", SAMPLE_FILES["invoices"], "text/csv"),
        "credits": ("credits.csv", SAMPLE_FILES["credits"], "text/csv"),
        "message": ("message.txt", SAMPLE_FILES["message"], "text/plain"),
    }
    response = client.post(
        "/api/v1/imports/validate",
        files=files,
        data=SAMPLE_CONTEXT,
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 403
    assert "upload bank.csv, invoices.csv, credits.csv, and message.txt" in response.json()[
        "error"
    ]["message"]
    api.dependency_overrides.clear()


def test_interpretation_rejects_changed_source_hash(session) -> None:
    service = ReconcileService(session)
    workspace = service.create_workspace()
    bank = (
        b"source_account_id,transaction_id,booking_date,payer_name,reference,amount,currency\n"
        b"acct,source-stale,2026-01-15,C,unidentified payment,100,MXN\n"
    )
    invoices = (
        b"customer_id,customer_name,invoice_id,issued_date,due_date,balance_as_of,"
        b"outstanding_amount,currency\n"
        b"c,C,i,2026-01-01,2026-01-15,2026-01-15,100,MXN\n"
    )
    parsed = parse_batch(
        bank,
        invoices,
        message=b"Please review this payment.",
        message_context=parse_message_context("2026-01-15T12:00:00+00:00", "acct", "source-stale"),
    )
    batch = service.validate_import(workspace.id, parsed, "local")
    session.commit()
    service.commit_import(workspace.id, batch.id)
    payment = session.scalar(select(Payment).where(Payment.workspace_id == workspace.id))
    assert payment is not None
    proposal = service.process_match(workspace.id, payment.id)
    message_source = session.scalar(
        select(Source).where(Source.workspace_id == workspace.id, Source.kind == "message")
    )
    assert message_source is not None
    expected_hash = message_source.sha256
    message_source.sha256 = uuid.uuid4().hex
    session.commit()

    with pytest.raises(ServiceError, match="source evidence changed"):
        service.record_interpretation(
            workspace.id,
            proposal.id,
            expected_revision=1,
            mode="direct",
            source="live",
            candidate=None,
            citations=[],
            reason_code="source_changed",
            trace={},
            expected_source_hashes={str(message_source.id): expected_hash},
            expected_payment_version=payment.version,
            expected_invoice_versions={},
            expected_credit_versions={},
        )


def test_cross_workspace_application_and_reversal_are_denied(session) -> None:
    service = ReconcileService(session)
    owner = service.create_workspace()
    outsider = service.create_workspace()
    batch = ImportBatch(workspace_id=owner.id)
    session.add(batch)
    session.flush()
    source = Source(
        workspace_id=owner.id,
        batch_id=batch.id,
        kind="bank",
        sha256=uuid.uuid4().hex,
        raw_bytes=b"",
    )
    session.add(source)
    session.flush()
    invoice = Invoice(
        workspace_id=owner.id,
        source_id=source.id,
        customer_id="c",
        customer_name="C",
        invoice_id="workspace-invoice",
        issued_date=date(2026, 1, 1),
        due_date=date(2026, 1, 15),
        balance_as_of=date(2026, 1, 15),
        outstanding_amount=10_000,
        currency="MXN",
    )
    payment = Payment(
        workspace_id=owner.id,
        source_id=source.id,
        source_account_id="acct",
        transaction_id="workspace-payment",
        booking_date=date(2026, 1, 15),
        payer_name="C",
        reference="invoice workspace-invoice",
        amount=10_000,
        currency="MXN",
    )
    session.add_all([invoice, payment])
    session.commit()
    proposal = service.process_match(owner.id, payment.id)
    revision = session.scalar(
        select(ProposalRevision).where(
            ProposalRevision.proposal_id == proposal.id,
            ProposalRevision.revision == 1,
        )
    )
    assert revision is not None
    applied = service.apply(
        owner.id,
        proposal.id,
        revision.revision,
        revision.version_token,
        "owner",
        "owner-application",
    )

    with pytest.raises(ServiceError, match="proposal not found"):
        service.apply(
            outsider.id,
            proposal.id,
            revision.revision,
            revision.version_token,
            "outsider",
            "outsider-application",
        )
    with pytest.raises(ServiceError, match="application not found"):
        service.reverse(outsider.id, applied.id, "outsider", "no access", "outsider-reversal")
    assert session.query(CashApplication).filter_by(application_group_id=applied.id).count() == 1


def test_unrelated_invoice_lock_does_not_block_application(db_engine) -> None:
    session_factory = sessionmaker(bind=db_engine, expire_on_commit=False)
    with session_factory() as setup:
        service = ReconcileService(setup)
        workspace = service.create_workspace()
        batch = ImportBatch(workspace_id=workspace.id, status="COMMITTED")
        setup.add(batch)
        setup.flush()
        source = Source(
            workspace_id=workspace.id,
            batch_id=batch.id,
            kind="bank",
            sha256=uuid.uuid4().hex,
            raw_bytes=b"",
            status="COMMITTED",
        )
        setup.add(source)
        setup.flush()
        invoices = [
            Invoice(
                workspace_id=workspace.id,
                source_id=source.id,
                customer_id="c",
                customer_name="C",
                invoice_id=identifier,
                issued_date=date(2026, 1, 1),
                due_date=date(2026, 1, 15),
                balance_as_of=date(2026, 1, 15),
                outstanding_amount=10_000,
                currency="MXN",
            )
            for identifier in ("unrelated-lock", "independent-apply")
        ]
        payment = Payment(
            workspace_id=workspace.id,
            source_id=source.id,
            source_account_id="acct",
            transaction_id="independent-payment",
            booking_date=date(2026, 1, 15),
            payer_name="C",
            reference="invoice independent-apply",
            amount=10_000,
            currency="MXN",
        )
        setup.add_all([*invoices, payment])
        setup.commit()
        proposal = service.process_match(workspace.id, payment.id)
        revision = setup.scalar(
            select(ProposalRevision).where(
                ProposalRevision.proposal_id == proposal.id,
                ProposalRevision.revision == 1,
            )
        )
        assert revision is not None

    holder = session_factory()
    holder.execute(
        select(Invoice)
        .where(Invoice.workspace_id == workspace.id, Invoice.invoice_id == "unrelated-lock")
        .with_for_update()
    ).scalar_one()
    worker = session_factory()
    executor = ThreadPoolExecutor(max_workers=1)
    future = executor.submit(
        ReconcileService(worker).apply,
        workspace.id,
        proposal.id,
        revision.revision,
        revision.version_token,
        "reviewer",
        "independent-apply",
    )
    try:
        # The holder keeps an unrelated invoice locked while the worker must
        # complete; a broad workspace lock would make this timeout.
        group = future.result(timeout=60)
    finally:
        holder.rollback()
        holder.close()
        if not future.done():
            future.result(timeout=60)
        executor.shutdown(wait=True)
        worker.close()
    assert group.id is not None


@pytest.mark.parametrize("race_number", range(25))
def test_contested_balance_race_has_one_winner(db_engine, race_number: int) -> None:
    session_factory = sessionmaker(bind=db_engine, expire_on_commit=False)
    amount = 10_000
    with session_factory() as setup:
        service = ReconcileService(setup)
        workspace = service.create_workspace()
        batch = ImportBatch(workspace_id=workspace.id)
        setup.add(batch)
        setup.flush()
        source = Source(
            workspace_id=workspace.id,
            batch_id=batch.id,
            kind="bank",
            sha256=uuid.uuid4().hex,
            raw_bytes=b"",
        )
        setup.add(source)
        setup.flush()
        invoice = Invoice(
            workspace_id=workspace.id,
            source_id=source.id,
            customer_id="c",
            customer_name="C",
            invoice_id=f"race-invoice-{race_number}",
            issued_date=date(2026, 1, 1),
            due_date=date(2026, 1, 15),
            balance_as_of=date(2026, 1, 15),
            outstanding_amount=amount,
            currency="MXN",
        )
        payments = [
            Payment(
                workspace_id=workspace.id,
                source_id=source.id,
                source_account_id="acct",
                transaction_id=f"race-payment-{race_number}-{suffix}",
                booking_date=date(2026, 1, 15),
                payer_name="C",
                reference=f"invoice race-invoice-{race_number}",
                amount=amount,
                currency="MXN",
            )
            for suffix in ("one", "two")
        ]
        setup.add_all([invoice, *payments])
        setup.commit()
        proposals = [service.process_match(workspace.id, item.id) for item in payments]
        payloads = []
        for index, proposal in enumerate(proposals):
            revision = setup.scalar(
                select(ProposalRevision).where(
                    ProposalRevision.proposal_id == proposal.id,
                    ProposalRevision.revision == 1,
                )
            )
            assert revision is not None
            payloads.append(
                (
                    workspace.id,
                    proposal.id,
                    revision.revision,
                    revision.version_token,
                    "reviewer",
                    f"race-{race_number}-{index}",
                )
            )

    barrier = Barrier(2)

    def attempt(payload) -> tuple[str, str]:
        local = session_factory()
        try:
            barrier.wait(timeout=10)
            group = ReconcileService(local).apply(*payload)
            return "won", str(group.id)
        except ServiceError as exc:
            local.rollback()
            return "lost", exc.code
        finally:
            local.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(attempt, payloads))

    assert [result[0] for result in results].count("won") == 1
    assert [result[0] for result in results].count("lost") == 1
    assert results[0][1] != results[1][1]
    with session_factory() as check:
        active = list(
            check.scalars(
                select(CashApplication).where(
                    CashApplication.workspace_id == workspace.id,
                    CashApplication.active.is_(True),
                )
            )
        )
        assert len(active) == 1
        assert sum(row.amount for row in active) == amount
        assert active[0].payment_id in {payments[0].id, payments[1].id}


def test_folio_fragments_widen_candidates_without_letting_rules_propose(
    session, monkeypatch
) -> None:
    from reconcile.domain.matching import propose
    from reconcile.persistence.service import (
        _credit_fact,
        _invoice_fact,
        _payment_fact,
        _shadow_group,
        retrieve_observations,
    )

    api, client = _api_client(session, monkeypatch)
    try:
        bank = (
            b"source_account_id,transaction_id,booking_date,payer_name,reference,amount,currency\n"
            b"frag-account,frag-payment,2026-09-15,Comercial Norte,PAGO FACT 1432 Y 33,"
            b"55000.00,MXN\n"
        )
        invoices = (
            b"customer_id,customer_name,invoice_id,issued_date,due_date,balance_as_of,"
            b"outstanding_amount,currency\n"
            b"frag-customer,Comercial Norte,F-1432,2026-08-01,2026-09-10,2026-09-15,"
            b"30000.00,MXN\n"
            b"frag-customer,Comercial Norte,F-1433,2026-08-02,2026-09-11,2026-09-15,"
            b"25000.00,MXN\n"
            b"frag-customer,Comercial Norte,F-1436,2026-08-05,2026-09-14,2026-09-15,"
            b"55000.00,MXN\n"
            b"frag-customer,Comercial Norte,F-2001,2026-08-05,2026-09-14,2026-09-15,"
            b"12000.00,MXN\n"
        )
        validated = client.post(
            "/api/v1/imports/validate",
            files={
                "bank": ("bank.csv", bank, "text/csv"),
                "invoices": ("invoices.csv", invoices, "text/csv"),
                "message": ("message.txt", b"Pago de la 1432 y la 33.", "text/plain"),
            },
            data={
                "message_time": "2026-09-15T12:00:00+00:00",
                "payment_source_account_id": "frag-account",
                "payment_transaction_id": "frag-payment",
            },
        )
        assert validated.status_code == 200, validated.text
        batch_id = validated.json()["batch_id"]
        assert client.post(f"/api/v1/imports/{batch_id}/commit").status_code == 200
        _run_all_jobs(client)

        payment = session.scalar(select(Payment).where(Payment.transaction_id == "frag-payment"))
        assert payment is not None
        proposal = session.scalar(select(Proposal).where(Proposal.payment_id == payment.id))
        assert proposal is not None and proposal.status == "NEEDS_REVIEW"

        evidence = {
            str(source.id): source.raw_bytes.decode()
            for source in session.scalars(
                select(Source).where(
                    Source.workspace_id == payment.workspace_id, Source.kind == "message"
                )
            )
        }
        found, credits = retrieve_observations(
            session, payment.workspace_id, payment, evidence, None
        )
        assert [row.invoice_id for row in found] == ["F-1436", "F-1432", "F-1433"]
        assert credits == []

        rules = propose(
            _payment_fact(payment),
            [_invoice_fact(row) for row in found],
            [_credit_fact(row) for row in credits],
            evidence,
        )
        group = _shadow_group(payment, found, credits, evidence, rules)
        allocations = [
            sorted((line["invoice_id"], line["amount"]) for line in candidate["cash"])
            for candidate in group["candidates"]
        ]
        assert [("F-1432", 3_000_000), ("F-1433", 2_500_000)] in allocations
        assert [("F-1436", 5_500_000)] in allocations
    finally:
        api.dependency_overrides.clear()
