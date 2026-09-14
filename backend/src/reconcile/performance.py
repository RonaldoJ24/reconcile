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
from reconcile.persistence.db import normalize_database_url
from reconcile.persistence.models import (
    Base,
    ImportBatch,
    Invoice,
    Payment,
    Proposal,
    Source,
    Workspace,
)
from reconcile.persistence.service import ReconcileService

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


def _guarded_url() -> str:
    if os.getenv("ALLOW_PERF_TEST") != "1":
        raise RuntimeError("set ALLOW_PERF_TEST=1 for the isolated Phase 5 workload")
    if os.getenv("ALLOW_DESTRUCTIVE_TEST_DB") != "1":
        raise RuntimeError("set ALLOW_DESTRUCTIVE_TEST_DB=1 for the dedicated test schema")
    value = os.getenv("TEST_DATABASE_URL")
    if not value or value.startswith("sqlite"):
        raise RuntimeError("TEST_DATABASE_URL must identify isolated PostgreSQL")
    return normalize_database_url(value)


def _seed(session: Session, workspace_id: uuid.UUID) -> list[uuid.UUID]:
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
    return payment_ids


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
                payment_ids = _seed(session, workspace_id)

                started = perf_counter()
                service = ReconcileService(session)
                for payment_id in payment_ids:
                    service.process_match(workspace_id, payment_id)
                matching_seconds = perf_counter() - started
                proposal_ids = list(
                    session.scalars(
                        select(Proposal.id)
                        .where(Proposal.workspace_id == workspace_id)
                        .order_by(Proposal.created_at)
                    )
                )

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
