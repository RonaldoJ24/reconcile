"""PostgreSQL-only integration checks for locking/idempotency behavior."""

from __future__ import annotations

import hashlib
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from threading import Barrier

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
from reconcile.jobs.queue import claim_one
from reconcile.persistence.db import normalize_database_url
from reconcile.persistence.maintenance import (
    cleanup_expired_preview_workspaces,
    enforce_database_admission,
)
from reconcile.persistence.models import (
    Base,
    CashApplication,
    ImportBatch,
    InterpretationBudgetCounter,
    InterpretationCall,
    Invoice,
    Job,
    Payment,
    ProposalRevision,
    Source,
    Workspace,
    now_utc,
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
    assert revision.provenance == "rules-v1"
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
