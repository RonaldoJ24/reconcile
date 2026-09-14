from __future__ import annotations

import argparse
import json
import uuid
from pathlib import Path
from typing import Any, Literal

from sqlalchemy import func, select

from reconcile.config import interpretation_settings
from reconcile.ingest.parsers import parse_batch, parse_message_context
from reconcile.persistence.db import SessionLocal
from reconcile.persistence.models import (
    ApplicationGroup,
    InterpretationCall,
    Payment,
    ProposalRevision,
)
from reconcile.persistence.service import ReconcileService

from .workflow import CompiledInterpretationWorkflow, WorkflowOutcome, compile_workflow

BANK = (
    b"source_account_id,transaction_id,booking_date,payer_name,reference,amount,currency\n"
    b"acct,{transaction},2026-09-14,Example SA,wire without invoice number,100.00,MXN\n"
)
INVOICES = (
    b"customer_id,customer_name,invoice_id,issued_date,due_date,balance_as_of,"
    b"outstanding_amount,currency\n"
    b"cust,Example SA,INV-A,2026-08-01,2026-09-01,2026-09-13,100.00,MXN\n"
    b"cust,Example SA,INV-B,2026-08-01,2026-09-01,2026-09-13,100.00,MXN\n"
)
ORIGINAL = b"Use the alphabetically first invoice identifier."
COUNTERFACTUAL = b"Use the alphabetically last invoice identifier."


def _import_case(transaction: str, message: bytes | None) -> tuple[uuid.UUID, uuid.UUID]:
    with SessionLocal() as db:
        service = ReconcileService(db)
        workspace = service.create_workspace()
        parsed = parse_batch(
            BANK.replace(b"{transaction}", transaction.encode()),
            INVOICES,
            message=message,
            message_context=(
                parse_message_context("2026-09-14T12:00:00+00:00", "acct", transaction)
                if message is not None
                else None
            ),
        )
        batch = service.validate_import(workspace.id, parsed, "local")
        db.commit()
        service.commit_import(workspace.id, batch.id)
        payment = db.scalar(select(Payment).where(Payment.workspace_id == workspace.id))
        if payment is None:
            raise RuntimeError("live smoke import did not persist a payment")
        proposal = service.process_match(workspace.id, payment.id)
        if proposal.status != "NEEDS_REVIEW":
            raise RuntimeError("live smoke case must begin in NEEDS_REVIEW")
        return workspace.id, proposal.id


def _run_case(
    workflow: CompiledInterpretationWorkflow,
    *,
    visitor: uuid.UUID,
    label: str,
    mode: Literal["direct", "hybrid"],
    message: bytes | None,
) -> dict[str, Any]:
    workspace_id, proposal_id = _import_case(f"phase4-{label}-{uuid.uuid4().hex[:8]}", message)
    with SessionLocal() as db:
        outcome: WorkflowOutcome = workflow.run(
            db,
            workspace_id=workspace_id,
            session_id=visitor,
            proposal_id=proposal_id,
            mode=mode,
        )
        revision = db.scalar(
            select(ProposalRevision)
            .where(ProposalRevision.proposal_id == proposal_id)
            .order_by(ProposalRevision.revision.desc())
        )
        return {
            "workspace_id": str(workspace_id),
            "label": label,
            "mode": mode,
            "rules": {"status": "NEEDS_REVIEW", "selected_invoice_ids": []},
            "interpretation": outcome.api_dict(),
            "proposal_status": outcome.proposal_status or "NEEDS_REVIEW",
            "selected_invoice_ids": sorted(
                {item["invoice_id"] for item in revision.cash_lines}
                if revision and outcome.status == "selected"
                else []
            ),
        }


def run_live_smoke(report_path: Path) -> dict[str, Any]:
    settings = interpretation_settings()
    if not settings.enabled:
        raise RuntimeError("set RECONCILE_LLM_ENABLED=1 for the explicit live smoke")
    workflow = compile_workflow()
    visitor = uuid.uuid4()
    cases = [
        _run_case(
            workflow,
            visitor=visitor,
            label="original-direct",
            mode="direct",
            message=ORIGINAL,
        ),
        _run_case(
            workflow,
            visitor=visitor,
            label="original-hybrid",
            mode="hybrid",
            message=ORIGINAL,
        ),
        _run_case(
            workflow,
            visitor=visitor,
            label="counterfactual-direct",
            mode="direct",
            message=COUNTERFACTUAL,
        ),
        _run_case(
            workflow,
            visitor=visitor,
            label="evidence-ablation",
            mode="direct",
            message=None,
        ),
    ]
    with SessionLocal() as db:
        calls = list(
            db.scalars(
                select(InterpretationCall)
                .where(InterpretationCall.execution_id == settings.execution_id)
                .order_by(InterpretationCall.created_at)
            )
        )
        applications = (
            db.scalar(
                select(func.count())
                .select_from(ApplicationGroup)
                .where(
                    ApplicationGroup.workspace_id.in_(
                        [uuid.UUID(item["workspace_id"]) for item in cases]
                    )
                )
            )
            or 0
        )
    observed_cost = sum(item.estimated_microdollars or 0 for item in calls)
    retained = sum(
        item.reservation_microdollars if item.reservation_retained else 0 for item in calls
    )
    original = cases[0]["selected_invoice_ids"]
    counterfactual = cases[2]["selected_invoice_ids"]
    gates = {
        "direct_and_hybrid_same_input_observed": all(
            item["interpretation"]["source"] == "live" for item in cases[:2]
        ),
        "altered_evidence_changed_or_invalidated": (
            counterfactual != original or cases[2]["interpretation"]["status"] != "selected"
        ),
        "missing_evidence_made_no_call": cases[3]["interpretation"]["failure_code"]
        == "missing_evidence",
        "provider_created_no_application": applications == 0,
        "execution_budget_respected": observed_cost + retained <= settings.execution_microdollars,
        "attempt_limit_respected": len(calls) <= 25
        and max(
            (sum(other.session_id == item.session_id for other in calls) for item in calls),
            default=0,
        )
        <= 5,
    }
    report = {
        "version": "llm-v1",
        "created_on": "2026-09-14",
        "dataset": "synthetic development smoke; not independently domain validated",
        "requested_model": settings.model,
        "execution_id": settings.execution_id,
        "authorized_microdollars": settings.execution_microdollars,
        "cases": cases,
        "provider_attempts": [
            {
                "status": item.status,
                "attempt": item.attempt,
                "requested_model": item.requested_model,
                "response_model": item.response_model,
                "input_tokens": item.input_tokens,
                "output_tokens": item.output_tokens,
                "cached_input_tokens": item.cached_input_tokens,
                "reasoning_tokens": item.reasoning_tokens,
                "latency_ms": item.latency_ms,
                "estimated_microdollars": item.estimated_microdollars,
                "reservation_retained_microdollars": (
                    item.reservation_microdollars if item.reservation_retained else 0
                ),
                "error_code": item.error_code,
            }
            for item in calls
        ],
        "estimated_microdollars": observed_cost,
        "retained_microdollars": retained,
        "gates": gates,
        "passed": all(gates.values()),
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the explicit budgeted Phase 4 live smoke")
    parser.add_argument("--report", type=Path, default=Path("reports/llm-v1/live-smoke.json"))
    args = parser.parse_args()
    report = run_live_smoke(args.report)
    print(
        json.dumps(
            {
                "passed": report["passed"],
                "provider_attempts": len(report["provider_attempts"]),
                "estimated_microdollars": report["estimated_microdollars"],
                "retained_microdollars": report["retained_microdollars"],
            },
            sort_keys=True,
        )
    )
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
