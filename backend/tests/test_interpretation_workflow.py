from __future__ import annotations

import hashlib
import threading
import uuid
from collections.abc import Callable

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

from reconcile.api.cases import get_case
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
from reconcile.interpretation.workflow import CompiledInterpretationWorkflow, WorkflowProgress
from reconcile.persistence.db import normalize_database_url
from reconcile.persistence.models import (
    ApplicationGroup,
    Base,
    InterpretationCall,
    Payment,
    Proposal,
    ProposalRevision,
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
    assert outcome.proposal_revision is not None
    revision = db.scalar(
        select(ProposalRevision).where(
            ProposalRevision.proposal_id == proposal_id,
            ProposalRevision.revision == outcome.proposal_revision,
        )
    )
    assert revision is not None
    assert revision.model_trace["schema_version"] == "decision-trace-v1"
    assert revision.model_trace["source"] == "live"
    assert revision.model_trace["stages"]
    assert all(stage["duration_ms"] is None for stage in revision.model_trace["stages"])
    assert revision.model_trace["stages"][-1]["id"] == "record_proposal_revision"
    assert revision.model_trace["stages"][-1]["status"] == "completed"
    assert db.scalar(select(ApplicationGroup)) is None
    call = db.scalar(select(InterpretationCall))
    assert call is not None and call.status == "SUCCEEDED"


def test_progress_is_emitted_while_provider_is_still_blocked(db) -> None:
    workspace_id, proposal_id, visitor = imported_review_case(db)
    provider_started = threading.Event()
    provider_finished = threading.Event()
    release_provider = threading.Event()
    progress_seen = threading.Event()
    progress: list[WorkflowProgress] = []

    class BlockingProvider(FakeProvider):
        def interpret(self, request, *, reserve_attempt=None, finalize_attempt=None):
            provider_started.set()
            assert release_provider.wait(5)
            provider_finished.set()
            return super().interpret(
                request,
                reserve_attempt=reserve_attempt,
                finalize_attempt=finalize_attempt,
            )

    provider = BlockingProvider(abstain)
    workflow = CompiledInterpretationWorkflow(lambda _: provider)  # type: ignore[arg-type]
    result: list[object] = []
    factory = sessionmaker(bind=db.get_bind(), expire_on_commit=False)

    def on_progress(item: WorkflowProgress) -> None:
        progress.append(item)
        if item.stage == "reserve_and_call" and item.status == "running":
            progress_seen.set()

    def run_workflow() -> None:
        with factory() as worker_db:
            result.append(
                workflow.run(
                    worker_db,
                    workspace_id=workspace_id,
                    session_id=visitor,
                    proposal_id=proposal_id,
                    mode="direct",
                    settings=settings(f"progress-{uuid.uuid4()}"),
                    progress=on_progress,
                )
            )

    thread = threading.Thread(target=run_workflow)
    thread.start()
    assert progress_seen.wait(5)
    assert provider_started.wait(5)
    assert not provider_finished.is_set()
    assert any(
        item.stage == "reserve_and_call" and item.status == "running" for item in progress
    )
    release_provider.set()
    thread.join(timeout=10)
    assert not thread.is_alive()
    assert result
    assert db.scalar(select(ApplicationGroup)) is None


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
    cached_revision = db.scalar(
        select(ProposalRevision)
        .where(ProposalRevision.proposal_id == proposal_id)
        .order_by(ProposalRevision.revision.desc())
    )
    assert cached_revision is not None
    assert cached_revision.model_trace["source"] == "cache"
    assert cached_revision.model_trace["interpretation"]["cache_hit"] is True
    assert cached_revision.model_trace["stages"][-1]["status"] == "completed"


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


def _open_registered_case(db, service: ReconcileService, workspace_id: uuid.UUID, case_id: str):
    packet = get_case(case_id)
    assert packet is not None
    batch = service.validate_import(workspace_id, packet.parse(), "local")
    for source in service.sources_for_batch(workspace_id, batch.id):
        if source.batch_id == batch.id:
            source.source_metadata = {**source.source_metadata, "case_id": case_id}
    db.commit()
    service.commit_import(workspace_id, batch.id)
    payment = db.scalar(
        select(Payment).where(
            Payment.workspace_id == workspace_id,
            Payment.transaction_id == packet.payment_transaction_id,
        )
    )
    assert payment is not None
    return service.process_match(workspace_id, payment.id)


def test_abbreviated_reference_reaches_the_message_supported_split(db) -> None:
    service = ReconcileService(db)
    workspace = service.create_workspace()
    # Another case in the same workspace must not leak into this request.
    _open_registered_case(db, service, workspace.id, "partial-installment")
    proposal = _open_registered_case(db, service, workspace.id, "spei-shorthand")
    assert proposal.status == "NEEDS_REVIEW"
    requests: list[InterpretationRequest] = []

    def select_message_split(request: InterpretationRequest) -> InterpretationResult:
        requests.append(request)
        candidate = next(
            item for item in request.candidates if item.invoice_ids == ("F-1432", "F-1433")
        )
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

    provider = FakeProvider(select_message_split)
    workflow = CompiledInterpretationWorkflow(lambda _: provider)  # type: ignore[arg-type]
    outcome = workflow.run(
        db,
        workspace_id=workspace.id,
        session_id=uuid.uuid4(),
        proposal_id=proposal.id,
        mode="direct",
        settings=settings(f"shorthand-{uuid.uuid4()}"),
    )

    assert outcome.status == "selected"
    assert {item.invoice_id for item in requests[0].invoices} == {"F-1432", "F-1433", "F-1436"}
    assert db.get(Proposal, proposal.id).status == "PROPOSED"
    revision = db.scalar(
        select(ProposalRevision).where(
            ProposalRevision.proposal_id == proposal.id,
            ProposalRevision.revision == outcome.proposal_revision,
        )
    )
    assert revision is not None
    assert sorted((line["invoice_id"], line["amount"]) for line in revision.cash_lines) == [
        ("F-1432", 3_000_000),
        ("F-1433", 2_400_000),
    ]
    assert revision.credit_lines == [
        {"credit_note_id": "NC-88", "invoice_id": "F-1433", "amount": 100_000}
    ]
    assert db.scalar(select(ApplicationGroup)) is None
