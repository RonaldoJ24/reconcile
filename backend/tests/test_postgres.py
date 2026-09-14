"""PostgreSQL-only integration checks for locking/idempotency behavior."""

from __future__ import annotations

import os
import uuid
from datetime import date

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from reconcile.persistence.models import Base, ImportBatch, Invoice, Payment, Source
from reconcile.persistence.service import ReconcileService, ServiceError

pytestmark = pytest.mark.postgres


@pytest.fixture(scope="module")
def db_engine():
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL is not set")
    if url.startswith("sqlite"):
        pytest.fail("TEST_DATABASE_URL must be PostgreSQL")
    # Every test object lives below this explicitly dedicated schema.
    engine = create_engine(
        url, connect_args={"options": "-csearch_path=reconcile_test"}, pool_pre_ping=True
    )
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
    from reconcile.persistence.models import ProposalRevision

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
