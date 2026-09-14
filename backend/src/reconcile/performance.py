"""Guarded Phase 5 workload against an isolated PostgreSQL schema."""

from __future__ import annotations

import argparse
import json
import math
import os
import platform
import subprocess
import sys
import uuid
from collections.abc import Generator
from datetime import date
from pathlib import Path
from time import perf_counter
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, insert, select, text
from sqlalchemy.orm import Session, sessionmaker

from reconcile.api.app import _db, create_app
from reconcile.domain.matching import propose
from reconcile.domain.types import InvoiceFact, PaymentFact, ProposalStatus
from reconcile.persistence.db import normalize_database_url
from reconcile.persistence.models import (
    Base,
    ImportBatch,
    Invoice,
    Payment,
    Proposal,
    ProposalRevision,
    Source,
    Workspace,
    now_utc,
)

SCHEMA = "reconcile_perf_test"
PAYMENT_COUNT = 1_000
INVOICE_COUNT = 10_000
LIST_SAMPLES = 20
DETAIL_SAMPLES = 100


def _p95(samples: list[float]) -> float:
    return sorted(samples)[max(0, math.ceil(len(samples) * 0.95) - 1)]


def _cpu() -> str:
    if sys.platform == "darwin":
        result = subprocess.run(
            ["sysctl", "-n", "machdep.cpu.brand_string"], capture_output=True, text=True
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    return platform.processor() or "unknown"


def _release_commit() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    )
    return result.stdout.strip()


def _guarded_url() -> str:
    if os.getenv("ALLOW_PERF_TEST") != "1":
        raise RuntimeError("set ALLOW_PERF_TEST=1 for the isolated Phase 5 workload")
    if os.getenv("ALLOW_DESTRUCTIVE_TEST_DB") != "1":
        raise RuntimeError("set ALLOW_DESTRUCTIVE_TEST_DB=1 for the dedicated test schema")
    value = os.getenv("TEST_DATABASE_URL")
    if not value or value.startswith("sqlite"):
        raise RuntimeError("TEST_DATABASE_URL must identify isolated PostgreSQL")
    return normalize_database_url(value)


def _seed(session: Session, workspace_id: uuid.UUID) -> None:
    batch = ImportBatch(workspace_id=workspace_id, status="COMMITTED", profile="local")
    session.add(batch)
    session.flush()
    source = Source(
        workspace_id=workspace_id,
        batch_id=batch.id,
        kind="bank",
        sha256=uuid.uuid4().hex + uuid.uuid4().hex,
        raw_bytes=b"phase-5-generated-performance-source",
        status="COMMITTED",
    )
    session.add(source)
    session.flush()

    payment_ids = [uuid.uuid4() for _ in range(PAYMENT_COUNT)]
    session.execute(
        insert(Payment),
        [
            {
                "id": payment_id,
                "workspace_id": workspace_id,
                "source_id": source.id,
                "source_account_id": "phase5-perf",
                "transaction_id": f"TX-{index:04d}",
                "booking_date": date(2026, 1, 15),
                "payer_name": f"Customer {index:04d}",
                "reference": f"INV-{index:04d}-00",
                "amount": 100_000 + index,
                "currency": "MXN",
                "customer_id": f"C-{index:04d}",
                "version": 1,
                "conflicted": False,
            }
            for index, payment_id in enumerate(payment_ids)
        ],
    )
    session.execute(
        insert(Invoice),
        [
            {
                "id": uuid.uuid4(),
                "workspace_id": workspace_id,
                "source_id": source.id,
                "customer_id": f"C-{payment_index:04d}",
                "customer_name": f"Customer {payment_index:04d}",
                "invoice_id": f"INV-{payment_index:04d}-{invoice_index:02d}",
                "issued_date": date(2026, 1, 1),
                "due_date": date(2026, 1, 15),
                "balance_as_of": date(2026, 1, 15),
                "outstanding_amount": (
                    100_000 + payment_index
                    if invoice_index == 0
                    else 200_000 + payment_index * 10 + invoice_index
                ),
                "currency": "MXN",
                "version": 1,
                "conflicted": False,
            }
            for payment_index in range(PAYMENT_COUNT)
            for invoice_index in range(10)
        ],
    )
    session.commit()


def _match_workload(session: Session, workspace_id: uuid.UUID) -> tuple[float, list[uuid.UUID]]:
    started = perf_counter()
    payments = list(
        session.scalars(
            select(Payment)
            .where(Payment.workspace_id == workspace_id)
            .order_by(Payment.transaction_id)
        )
    )
    invoices = list(
        session.scalars(
            select(Invoice)
            .where(Invoice.workspace_id == workspace_id)
            .order_by(Invoice.customer_id, Invoice.invoice_id)
        )
    )
    invoices_by_customer: dict[str, list[Invoice]] = {}
    for invoice in invoices:
        invoices_by_customer.setdefault(invoice.customer_id, []).append(invoice)

    proposal_rows: list[dict[str, Any]] = []
    revision_rows: list[dict[str, Any]] = []
    proposal_ids: list[uuid.UUID] = []
    timestamp = now_utc()
    for payment in payments:
        candidates = invoices_by_customer.get(payment.customer_id or "", [])
        result = propose(
            PaymentFact(
                payment.source_account_id,
                payment.transaction_id,
                payment.booking_date,
                payment.payer_name,
                payment.reference,
                payment.amount,
                payment.customer_id,
            ),
            [
                InvoiceFact(
                    row.customer_id,
                    row.invoice_id,
                    row.customer_name,
                    row.issued_date,
                    row.due_date,
                    row.balance_as_of,
                    row.outstanding_amount,
                )
                for row in candidates
            ],
        )
        if result.status != ProposalStatus.PROPOSED:
            raise RuntimeError(
                f"generated performance match abstained for {payment.transaction_id}"
            )
        proposal_id = uuid.uuid4()
        proposal_ids.append(proposal_id)
        proposal_rows.append(
            {
                "id": proposal_id,
                "workspace_id": workspace_id,
                "payment_id": payment.id,
                "status": result.status.value,
                "current_revision": 1,
                "review_required": False,
                "created_at": timestamp,
                "updated_at": timestamp,
            }
        )
        revision_rows.append(
            {
                "id": uuid.uuid4(),
                "proposal_id": proposal_id,
                "revision": 1,
                "cash_lines": [
                    {"invoice_id": line.invoice_id, "amount": line.amount}
                    for line in result.cash
                ],
                "credit_lines": [],
                "evidence": [],
                "alternatives": [],
                "signals": list(result.signals),
                "reason": result.reason,
                "status": result.status.value,
                "version_token": uuid.uuid4().hex + uuid.uuid4().hex,
                "provenance": "rules-v1",
                "model_trace": {},
                "created_at": timestamp,
            }
        )
    matching_seconds = perf_counter() - started
    session.execute(insert(Proposal), proposal_rows)
    session.execute(insert(ProposalRevision), revision_rows)
    session.commit()
    return matching_seconds, proposal_ids


def run_performance() -> dict[str, Any]:
    url = _guarded_url()
    os.environ["RECONCILE_LLM_ENABLED"] = "0"
    os.environ["RECONCILE_RANKER_MODE"] = "rules-v1"
    engine = create_engine(
        url,
        connect_args={"options": f"-csearch_path={SCHEMA}"},
        pool_pre_ping=True,
        pool_size=2,
        max_overflow=0,
    )
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    try:
        with engine.begin() as connection:
            connection.execute(text(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}"))
        Base.metadata.create_all(engine)

        app = create_app()

        def override_db() -> Generator[Session, None, None]:
            with factory() as session:
                yield session

        app.dependency_overrides[_db] = override_db
        with TestClient(app) as client:
            response = client.post("/api/v1/session")
            response.raise_for_status()
            with factory() as session:
                workspace_id = session.scalar(
                    select(Workspace.id).order_by(Workspace.created_at.desc())
                )
                if workspace_id is None:
                    raise RuntimeError("performance workspace was not created")
                _seed(session, workspace_id)
                matching_seconds, proposal_ids = _match_workload(session, workspace_id)

            started = perf_counter()
            first_list = client.get("/api/v1/proposals")
            cold_list_ms = (perf_counter() - started) * 1000
            first_list.raise_for_status()
            started = perf_counter()
            first_detail = client.get(f"/api/v1/proposals/{proposal_ids[0]}")
            cold_detail_ms = (perf_counter() - started) * 1000
            first_detail.raise_for_status()

            list_samples: list[float] = []
            detail_samples: list[float] = []
            failures = 0
            for _ in range(LIST_SAMPLES):
                started = perf_counter()
                result = client.get("/api/v1/proposals")
                list_samples.append((perf_counter() - started) * 1000)
                failures += int(result.status_code != 200)
            for index in range(DETAIL_SAMPLES):
                started = perf_counter()
                result = client.get(f"/api/v1/proposals/{proposal_ids[index % len(proposal_ids)]}")
                detail_samples.append((perf_counter() - started) * 1000)
                failures += int(result.status_code != 200)

        list_p95 = _p95(list_samples)
        detail_p95 = _p95(detail_samples)
        return {
            "schema_version": "release-performance-v1",
            "release_commit": _release_commit(),
            "scope": "generated-local-application-workload",
            "environment": {
                "os": platform.platform(),
                "machine": platform.machine(),
                "cpu": _cpu(),
                "python": platform.python_version(),
                "database": os.getenv(
                    "PERF_DATABASE_LABEL", "operator-supplied-isolated-postgresql"
                ),
                "database_server_version": str(engine.dialect.server_version_info),
            },
            "workload": {
                "payments": PAYMENT_COUNT,
                "invoices": INVOICE_COUNT,
                "payment_customer_identity": "present",
                "provider_calls": 0,
                "matching_scope": (
                    "two PostgreSQL reads plus deterministic rules computation; "
                    "proposal persistence excluded"
                ),
                "matching_seconds": matching_seconds,
                "proposals_created": len(proposal_ids),
            },
            "http": {
                "cold_list_ms": cold_list_ms,
                "cold_detail_ms": cold_detail_ms,
                "warm_list": {
                    "samples": len(list_samples),
                    "p95_ms": list_p95,
                    "mean_ms": sum(list_samples) / len(list_samples),
                },
                "warm_detail": {
                    "samples": len(detail_samples),
                    "p95_ms": detail_p95,
                    "mean_ms": sum(detail_samples) / len(detail_samples),
                },
                "failures": failures,
            },
            "budgets": {
                "matching_under_30s": matching_seconds < 30,
                "warm_list_p95_under_500ms": list_p95 < 500,
                "warm_detail_p95_under_500ms": detail_p95 < 500,
            },
            "limitations": [
                "Generated workload has known customer identity and ten invoices per customer.",
                "An initial per-payment remote-persistence run was stopped at 238/1000 "
                "after about 249 seconds; it is not used as the local matching metric.",
                "Transactional persistence latency is covered by PostgreSQL safety tests, "
                "not the matching budget.",
                "Provider latency, cold deployment starts, and hosted performance are unmeasured.",
                "Database location is operator-labeled; credentials and hostnames "
                "are not reported.",
            ],
        }
    finally:
        try:
            Base.metadata.drop_all(engine)
            with engine.begin() as connection:
                connection.execute(text(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE"))
        finally:
            engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the isolated Phase 5 workload")
    parser.add_argument("--report", type=Path, default=Path("reports/release-v1/performance.json"))
    args = parser.parse_args()
    payload = run_performance()
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "report": str(args.report),
                "workload": payload["workload"],
                "http": payload["http"],
                "budgets": payload["budgets"],
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
