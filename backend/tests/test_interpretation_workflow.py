from __future__ import annotations

import hashlib
import uuid
from collections.abc import Callable

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

from reconcile.config import InterpretationSettings
from reconcile.ingest.parsers import parse_batch, parse_message_context
from reconcile.interpretation.provider import (
    AttemptContext,
    AttemptEvent,
    FinalizeAttempt,
    ProviderOutcome,
    ReserveAttempt,
)
from reconcile.interpretation.schemas import (
    AttemptTelemetry,
    Citation,
    Decision,
    InterpretationRequest,
    InterpretationResult,
    ReasonCode,
    Usage,
)
from reconcile.interpretation.workflow import CompiledInterpretationWorkflow
from reconcile.persistence.db import normalize_database_url
from reconcile.persistence.models import (
    ApplicationGroup,
    Base,
    InterpretationCall,
    Payment,
    Proposal,
    Source,
)
from reconcile.persistence.service import ReconcileService

pytestmark = pytest.mark.postgres


@pytest.fixture(scope="module")
def interpretation_engine():
    import os

    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL is not set")
    if os.getenv("ALLOW_DESTRUCTIVE_TEST_DB") != "1":
        pytest.fail("set ALLOW_DESTRUCTIVE_TEST_DB=1 for the isolated test schema")
    engine = create_engine(
        normalize_database_url(url),
        pool_pre_ping=True,
    ).execution_options(schema_translate_map={None: "reconcile_interpretation_test"})
    with engine.begin() as connection:
        connection.execute(text("CREATE SCHEMA IF NOT EXISTS reconcile_interpretation_test"))
    Base.metadata.create_all(engine)
    yield engine
    Base.metadata.drop_all(engine)
    with engine.begin() as connection:
        connection.execute(text("DROP SCHEMA IF EXISTS reconcile_interpretation_test CASCADE"))
    engine.dispose()


@pytest.fixture()
def db(interpretation_engine):
    factory = sessionmaker(bind=interpretation_engine, expire_on_commit=False)
    with factory() as session:
        yield session


class FakeProvider:
    def __init__(self, decide: Callable[[InterpretationRequest], InterpretationResult]):
        self.decide = decide
        self.calls = 0

    def interpret(
        self,
        request: InterpretationRequest,
        *,
        reserve_attempt: ReserveAttempt | None = None,
        finalize_attempt: FinalizeAttempt | None = None,
    ) -> ProviderOutcome:
        self.calls += 1
        context = AttemptContext(1, 0, "deepseek-flash")
        reservation = reserve_attempt(context) if reserve_attempt else None
        telemetry = AttemptTelemetry(
            attempt=1,
            retry_number=0,
            requested_model="deepseek-flash",
            response_model="deepseek-flash-test",
            usage=Usage(input_tokens=500, output_tokens=50, provider_cache_tokens=0),
            latency_ms=10,
            http_status=200,
            reservation_state="reconciled",
        )
        if finalize_attempt:
            finalize_attempt(AttemptEvent(context, telemetry, reservation, "reconciled"))
        return ProviderOutcome(self.decide(request), None, (telemetry,))

    def close(self) -> None:
        pass


def imported_review_case(db) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    service = ReconcileService(db)
    workspace = service.create_workspace()
    bank = (
        b"source_account_id,transaction_id,booking_date,payer_name,reference,amount,currency\n"
        b"acct,tx-1,2026-09-14,Example SA,wire without invoice number,100.00,MXN\n"
    )
    invoices = (
        b"customer_id,customer_name,invoice_id,issued_date,due_date,balance_as_of,"
        b"outstanding_amount,currency\n"
        b"cust,Example SA,INV-A,2026-08-01,2026-09-01,2026-09-13,100.00,MXN\n"
        b"cust,Example SA,INV-B,2026-08-01,2026-09-01,2026-09-13,100.00,MXN\n"
    )
    message = b"Use the invoice ending in A."
    parsed = parse_batch(
        bank,
        invoices,
        message=message,
        message_context=parse_message_context("2026-09-14T12:00:00+00:00", "acct", "tx-1"),
    )
    batch = service.validate_import(workspace.id, parsed, "local")
    db.commit()
    service.commit_import(workspace.id, batch.id)
    payment = db.scalar(select(Payment).where(Payment.workspace_id == workspace.id))
    assert payment is not None
    proposal = service.process_match(workspace.id, payment.id)
    assert proposal.status == "NEEDS_REVIEW"
    return workspace.id, proposal.id, uuid.uuid4()


def settings(execution_id: str) -> InterpretationSettings:
    return InterpretationSettings(True, "not-a-real-key", "deepseek-flash", execution_id, 50_000)


def select_invoice_a(request: InterpretationRequest) -> InterpretationResult:
    candidate = next(item for item in request.candidates if item.invoice_ids == ("INV-A",))
    source = request.source_spans[0]
    return InterpretationResult(
        decision=Decision.SELECT,
        candidate_id=candidate.candidate_id,
        reason_code=ReasonCode.EVIDENCE_SUPPORTED,
        citations=[
            Citation(
                source_id=source.source_id,
                start=source.start,
                end=source.end,
                quote=source.content[source.start : source.end],
            )
        ],
    )


def abstain(request: InterpretationRequest) -> InterpretationResult:
    del request
    return InterpretationResult(
        decision=Decision.NEEDS_REVIEW,
        candidate_id=None,
        reason_code=ReasonCode.AMBIGUOUS,
        citations=[],
    )


def test_actual_imported_observations_select_candidate_without_applying(db) -> None:
    workspace_id, proposal_id, visitor = imported_review_case(db)
    provider = FakeProvider(select_invoice_a)
    workflow = CompiledInterpretationWorkflow(lambda _: provider)  # type: ignore[arg-type]

    outcome = workflow.run(
        db,
        workspace_id=workspace_id,
        session_id=visitor,
        proposal_id=proposal_id,
        mode="direct",
        settings=settings(f"select-{uuid.uuid4()}"),
    )

    assert outcome.status == "selected"
    assert outcome.source == "live"
    assert db.get(Proposal, proposal_id).status == "PROPOSED"
    assert db.scalar(select(ApplicationGroup)) is None
    call = db.scalar(select(InterpretationCall))
    assert call is not None and call.status == "SUCCEEDED"


def test_validated_cache_replays_without_a_second_call(db) -> None:
    workspace_id, proposal_id, visitor = imported_review_case(db)
    provider = FakeProvider(abstain)
    workflow = CompiledInterpretationWorkflow(lambda _: provider)  # type: ignore[arg-type]
    config = settings(f"cache-{uuid.uuid4()}")

    first = workflow.run(
        db,
        workspace_id=workspace_id,
        session_id=visitor,
        proposal_id=proposal_id,
        mode="direct",
        settings=config,
    )
    second = workflow.run(
        db,
        workspace_id=workspace_id,
        session_id=visitor,
        proposal_id=proposal_id,
        mode="direct",
        settings=config,
    )

    assert first.source == "live"
    assert second.source == "cache"
    assert provider.calls == 1


def test_changed_evidence_invalidates_cache(db) -> None:
    workspace_id, proposal_id, visitor = imported_review_case(db)
    provider = FakeProvider(abstain)
    workflow = CompiledInterpretationWorkflow(lambda _: provider)  # type: ignore[arg-type]
    config = settings(f"changed-{uuid.uuid4()}")
    workflow.run(
        db,
        workspace_id=workspace_id,
        session_id=visitor,
        proposal_id=proposal_id,
        mode="direct",
        settings=config,
    )
    message = db.scalar(
        select(Source).where(Source.workspace_id == workspace_id, Source.kind == "message")
    )
    assert message is not None
    message.raw_bytes = b"The invoice is not identified."
    message.sha256 = hashlib.sha256(b"message\0" + message.raw_bytes).hexdigest()
    db.commit()

    changed = workflow.run(
        db,
        workspace_id=workspace_id,
        session_id=visitor,
        proposal_id=proposal_id,
        mode="direct",
        settings=config,
    )

    assert changed.source == "live"
    assert provider.calls == 2


def test_invalid_provider_selection_cannot_create_revision_or_application(db) -> None:
    workspace_id, proposal_id, visitor = imported_review_case(db)
    before = db.get(Proposal, proposal_id).current_revision

    def invalid(request: InterpretationRequest) -> InterpretationResult:
        source = request.source_spans[0]
        return InterpretationResult(
            decision="select",
            candidate_id="unknown-candidate",
            reason_code="evidence_supported",
            citations=[
                Citation(
                    source_id=source.source_id,
                    start=source.start,
                    end=source.end,
                    quote=source.content,
                )
            ],
        )

    provider = FakeProvider(invalid)
    workflow = CompiledInterpretationWorkflow(lambda _: provider)  # type: ignore[arg-type]
    outcome = workflow.run(
        db,
        workspace_id=workspace_id,
        session_id=visitor,
        proposal_id=proposal_id,
        mode="direct",
        settings=settings(f"invalid-{uuid.uuid4()}"),
    )

    assert outcome.status == "unavailable"
    assert outcome.failure_code == "invalid_provider_result"
    assert db.get(Proposal, proposal_id).current_revision == before
    assert db.scalar(select(ApplicationGroup)) is None


def test_cancellation_before_provider_call_has_no_attempt(db) -> None:
    workspace_id, proposal_id, visitor = imported_review_case(db)
    provider = FakeProvider(abstain)
    workflow = CompiledInterpretationWorkflow(lambda _: provider)  # type: ignore[arg-type]

    outcome = workflow.run(
        db,
        workspace_id=workspace_id,
        session_id=visitor,
        proposal_id=proposal_id,
        mode="direct",
        settings=settings(f"cancel-{uuid.uuid4()}"),
        cancelled=lambda: True,
    )

    assert outcome.status == "unavailable"
    assert outcome.failure_code == "cancelled"
    assert provider.calls == 0
    assert (
        db.scalar(select(InterpretationCall).where(InterpretationCall.workspace_id == workspace_id))
        is None
    )
