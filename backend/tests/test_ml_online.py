from __future__ import annotations

import uuid
from datetime import date

import pytest

from reconcile.domain.matching import propose
from reconcile.persistence.models import Invoice, Payment
from reconcile.persistence.service import (
    _invoice_fact,
    _payment_fact,
    _shadow_group,
    _shadow_model_trace,
)


def _records() -> tuple[Payment, list[Invoice]]:
    workspace_id = uuid.uuid4()
    source_id = uuid.uuid4()
    payment = Payment(
        id=uuid.uuid4(),
        workspace_id=workspace_id,
        source_id=source_id,
        source_account_id="bank",
        transaction_id="tx",
        booking_date=date(2026, 1, 15),
        payer_name="Acme",
        reference="Apply INV-00",
        amount=10_000,
        currency="MXN",
        customer_id="customer",
    )
    invoices = [
        Invoice(
            id=uuid.uuid4(),
            workspace_id=workspace_id,
            source_id=source_id,
            customer_id="customer",
            customer_name="Acme",
            invoice_id=f"INV-{index:02d}",
            issued_date=date(2025, 12, 1),
            due_date=date(2026, 1, 10),
            balance_as_of=date(2026, 1, 14),
            outstanding_amount=10_000 if index == 0 else 20_000 + index,
            currency="MXN",
        )
        for index in range(12)
    ]
    return payment, invoices


def test_online_group_is_bounded_and_default_mode_does_not_load_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payment, invoices = _records()
    result = propose(_payment_fact(payment), [_invoice_fact(row) for row in invoices])
    group = _shadow_group(payment, invoices, [], {}, result)
    assert len(group["invoices"]) == 10
    assert group["retrieval_truncated"] is True
    assert len(group["candidates"]) == 10
    assert all(1 <= len(candidate["invoice_ids"]) <= 3 for candidate in group["candidates"])

    monkeypatch.delenv("RECONCILE_RANKER_MODE", raising=False)
    assert _shadow_model_trace(payment, invoices, [], {}, result) == {}


def test_installed_artifact_runs_through_service_shadow_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payment, invoices = _records()
    result = propose(_payment_fact(payment), [_invoice_fact(row) for row in invoices])
    monkeypatch.setenv("RECONCILE_RANKER_MODE", "shadow")
    trace = _shadow_model_trace(payment, invoices, [], {}, result)["ranker"]
    assert isinstance(trace, dict)
    assert trace["status"] == "observed"
    assert trace["model_id"] == "ranker-ml-v1-logistic"
    assert trace["model_version"] == "1"
    assert isinstance(trace["score"], float)
    assert isinstance(trace["latency_ms"], float)
    assert trace["candidate_count"] == 10
