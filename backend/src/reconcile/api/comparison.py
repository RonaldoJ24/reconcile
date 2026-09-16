"""Replayable, non-actionable observations over an immutable decision trace."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from time import perf_counter
from typing import Any

from reconcile.domain.matching import propose, validate_allocation
from reconcile.domain.types import (
    CashLine,
    CreditFact,
    CreditLine,
    InvoiceFact,
    PaymentFact,
    ProposalResult,
    ProposalStatus,
)
from reconcile.ml.artifact import ArtifactError
from reconcile.ml.runtime import ACTIVE_RULES_IDENTITY, rank_candidates
from reconcile.persistence.service import _hash, _stable_trace_identity

from .recordings import find_verified_recording

METHODS = ("rules", "bounded_correction", "shadow_ranker", "direct", "hybrid")


class SnapshotError(ValueError):
    """The persisted trace cannot be safely reconstructed for comparison."""


def _unavailable(method: str, reason: str) -> dict[str, object]:
    return {
        "method": method,
        "status": "unavailable",
        "source": "unavailable",
        "candidate": None,
        "actionable": False,
        "raw_score": None,
        "duration_ms": None,
        "usage": None,
        "cost_usd": None,
        "reason": reason,
    }


def _failed(method: str, reason: str, *, duration_ms: float | None = None) -> dict[str, object]:
    result = _unavailable(method, reason)
    result["status"] = "failed"
    result["duration_ms"] = duration_ms
    return result


def _candidate(cash: tuple[CashLine, ...], credits: tuple[CreditLine, ...]) -> dict[str, object]:
    return {
        "cash": [{"invoice_id": line.invoice_id, "amount": line.amount} for line in cash],
        "credits": [
            {
                "credit_note_id": line.credit_note_id,
                "invoice_id": line.invoice_id,
                "amount": line.amount,
            }
            for line in credits
        ],
    }


def _status(result: ProposalResult) -> str:
    if result.status is ProposalStatus.PROPOSED:
        return "proposed"
    if result.status in {ProposalStatus.NEEDS_REVIEW, ProposalStatus.STALE}:
        return "deferred"
    if result.status is ProposalStatus.FAILED:
        return "failed"
    return "unavailable"


def _duration(started: float) -> float:
    return round((perf_counter() - started) * 1000, 3)


def _facts(
    snapshot: Mapping[str, Any],
) -> tuple[
    PaymentFact,
    dict[str, InvoiceFact],
    dict[str, CreditFact],
    dict[str, str],
    dict[str, Any],
]:
    payment = snapshot.get("payment")
    invoices = snapshot.get("invoices")
    credits = snapshot.get("credits")
    sources = snapshot.get("sources")
    evidence = snapshot.get("evidence")
    group = snapshot.get("candidate_context")
    if (
        not isinstance(payment, Mapping)
        or not isinstance(invoices, list)
        or not isinstance(credits, list)
        or not isinstance(sources, list)
        or not isinstance(evidence, Mapping)
        or not isinstance(group, Mapping)
    ):
        raise SnapshotError("immutable input snapshot is incomplete")
    if not sources or any(
        not isinstance(source, Mapping)
        or not isinstance(source.get("source_id"), str)
        or not isinstance(source.get("kind"), str)
        or not isinstance(source.get("sha256"), str)
        for source in sources
    ):
        raise SnapshotError("immutable source snapshot is invalid")
    try:
        payment_fact = PaymentFact(
            str(payment["source_account_id"]),
            str(payment["transaction_id"]),
            date.fromisoformat(str(payment["booking_date"])),
            str(payment.get("payer_name", "")),
            str(payment.get("reference", "")),
            int(payment["amount"]),
            payment.get("customer_id") if isinstance(payment.get("customer_id"), str) else None,
            str(payment.get("source_id")) if payment.get("source_id") else None,
            int(payment.get("version", 1)),
        )
        invoice_facts: dict[str, InvoiceFact] = {}
        for row in invoices:
            if not isinstance(row, Mapping):
                raise SnapshotError("immutable invoice snapshot is invalid")
            invoice_id = str(row["invoice_id"])
            if invoice_id in invoice_facts:
                raise SnapshotError("immutable invoice snapshot contains duplicate IDs")
            invoice_facts[invoice_id] = InvoiceFact(
                str(row["customer_id"]),
                invoice_id,
                str(row.get("customer_name", "")),
                date.fromisoformat(str(row["issued_date"])),
                date.fromisoformat(str(row["due_date"])),
                date.fromisoformat(str(row["balance_as_of"])),
                int(row["outstanding_amount"]),
                int(row.get("version", 1)),
            )
        credit_facts: dict[str, CreditFact] = {}
        for row in credits:
            if not isinstance(row, Mapping):
                raise SnapshotError("immutable credit snapshot is invalid")
            credit_id = str(row["credit_note_id"])
            if credit_id in credit_facts:
                raise SnapshotError("immutable credit snapshot contains duplicate IDs")
            credit_facts[credit_id] = CreditFact(
                str(row["customer_id"]),
                credit_id,
                date.fromisoformat(str(row["balance_as_of"])),
                int(row["available_amount"]),
                str(row["invoice_id"]) if row.get("invoice_id") is not None else None,
                int(row.get("version", 1)),
            )
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise SnapshotError("immutable input snapshot contains invalid facts") from exc
    if any(
        not isinstance(key, str) or not isinstance(value, str)
        for key, value in evidence.items()
    ):
        raise SnapshotError("immutable evidence snapshot is invalid")
    candidates = group.get("candidates")
    if not isinstance(candidates, list):
        raise SnapshotError("immutable candidate context is invalid")
    return payment_fact, invoice_facts, credit_facts, dict(evidence), dict(group)


def _lines(candidate: Mapping[str, Any]) -> tuple[tuple[CashLine, ...], tuple[CreditLine, ...]]:
    cash_raw, credit_raw = candidate.get("cash", []), candidate.get("credits", [])
    if not isinstance(cash_raw, list) or not isinstance(credit_raw, list):
        raise SnapshotError("candidate allocation is invalid")
    try:
        cash = tuple(CashLine(str(row["invoice_id"]), int(row["amount"])) for row in cash_raw)
        credits = tuple(
            CreditLine(str(row["credit_note_id"]), str(row["invoice_id"]), int(row["amount"]))
            for row in credit_raw
        )
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise SnapshotError("candidate allocation is invalid") from exc
    return cash, credits


def _rules_method(
    payment: PaymentFact,
    invoices: Mapping[str, InvoiceFact],
    credits: Mapping[str, CreditFact],
    evidence: Mapping[str, str],
) -> tuple[dict[str, object], ProposalResult]:
    started = perf_counter()
    result = propose(payment, invoices.values(), credits.values(), evidence)
    method: dict[str, object] = {
        "method": "rules",
        "status": _status(result),
        "source": "rules",
        "candidate": None,
        "actionable": False,
        "raw_score": None,
        "duration_ms": _duration(started),
        "usage": None,
        "cost_usd": None,
        "reason": result.reason,
    }
    if result.status is ProposalStatus.PROPOSED:
        try:
            validate_allocation(payment.amount, invoices, credits, result.cash, result.credits)
        except (KeyError, ValueError) as exc:
            method = _failed("rules", f"shared financial validator rejected the result: {exc}")
        else:
            method["candidate"] = _candidate(result.cash, result.credits)
    return method, result


def _shadow_method(
    payment: PaymentFact,
    invoices: Mapping[str, InvoiceFact],
    credits: Mapping[str, CreditFact],
    group: Mapping[str, Any],
) -> tuple[dict[str, object], list[dict[str, object]] | None]:
    started = perf_counter()
    try:
        trace = rank_candidates(group, force=True)
    except ArtifactError:
        return (
            _unavailable("shadow_ranker", "allowlisted shadow ranker artifact is unavailable"),
            None,
        )
    except (ArithmeticError, TypeError, ValueError) as exc:
        return (
            _failed(
                "shadow_ranker", f"shadow ranker failed: {exc}", duration_ms=_duration(started)
            ),
            None,
        )
    candidate_id = trace.get("ranked_candidate")
    candidates = group.get("candidates")
    if not isinstance(candidate_id, str) or not isinstance(candidates, list):
        return _unavailable("shadow_ranker", "shadow ranker produced no candidate"), None
    selected = next(
        (
            item
            for item in candidates
            if isinstance(item, Mapping) and item.get("candidate_id") == candidate_id
        ),
        None,
    )
    if selected is None:
        return (
            _failed(
                "shadow_ranker",
                "shadow ranker selected an unknown candidate",
                duration_ms=_duration(started),
            ),
            None,
        )
    try:
        cash, credit_lines = _lines(selected)
        validate_allocation(payment.amount, invoices, credits, cash, credit_lines)
    except (KeyError, ValueError, SnapshotError) as exc:
        return (
            _failed(
                "shadow_ranker",
                f"shared financial validator rejected the result: {exc}",
                duration_ms=_duration(started),
            ),
            None,
        )
    score = trace.get("score")
    raw_score = score if isinstance(score, (int, float)) and not isinstance(score, bool) else None
    latency = trace.get("latency_ms")
    duration_ms = (
        latency
        if isinstance(latency, (int, float)) and not isinstance(latency, bool)
        else None
    )
    rank_context = [
        {"candidate_id": item.get("candidate_id"), "raw_score": item.get("score")}
        for item in trace.get("ranked_candidates", [])
        if isinstance(item, Mapping)
        and isinstance(item.get("candidate_id"), str)
        and isinstance(item.get("score"), (int, float))
    ]
    return (
        {
            "method": "shadow_ranker",
            "status": "proposed",
            "source": "local",
            "candidate": _candidate(cash, credit_lines),
            "actionable": False,
            "raw_score": raw_score,
            "duration_ms": duration_ms,
            "usage": None,
            "cost_usd": None,
            "reason": "Existing ranker observation; raw score is not calibrated confidence.",
        },
        rank_context,
    )


def _recorded_method(
    method_name: str,
    recording: Mapping[str, Any] | None,
    payment: PaymentFact,
    invoices: Mapping[str, InvoiceFact],
    credits: Mapping[str, CreditFact],
    group: Mapping[str, Any],
) -> dict[str, object]:
    if recording is None:
        return _unavailable(method_name, "no authenticated exact-input recording is available")
    result = recording.get("result")
    if not isinstance(result, Mapping):
        return _failed(method_name, "authenticated recording has no result")
    status = result.get("decision")
    candidate_id = result.get("candidate_id")
    if status == "select" and isinstance(candidate_id, str):
        candidates = group.get("candidates")
        if not isinstance(candidates, list):
            return _failed(method_name, "authenticated recording has no candidate context")
        selected = next(
            (
                item
                for item in candidates
                if isinstance(item, Mapping) and item.get("candidate_id") == candidate_id
            ),
            None,
        )
        if selected is None:
            return _failed(method_name, "authenticated recording selected an unknown candidate")
        try:
            cash, credit_lines = _lines(selected)
            validate_allocation(payment.amount, invoices, credits, cash, credit_lines)
        except (KeyError, ValueError, SnapshotError) as exc:
            return _failed(method_name, f"shared financial validator rejected the result: {exc}")
        candidate = _candidate(cash, credit_lines)
        comparison_status = "proposed"
    elif status == "needs_review":
        candidate = None
        comparison_status = "deferred"
    else:
        return _failed(method_name, "authenticated recording has an unsupported decision")
    raw_score = recording.get("raw_score")
    return {
        "method": method_name,
        "status": comparison_status,
        "source": "recorded",
        "candidate": candidate,
        "actionable": False,
        "raw_score": (
            raw_score
            if isinstance(raw_score, (int, float)) and not isinstance(raw_score, bool)
            else None
        ),
        "duration_ms": (
            recording.get("duration_ms")
            if isinstance(recording.get("duration_ms"), (int, float))
            else None
        ),
        "usage": recording.get("usage"),
        "cost_usd": recording.get("cost_usd"),
        "reason": "Authenticated exact-input recording; observation only.",
    }


def compare_snapshot(
    model_trace: Mapping[str, Any],
    *,
    case_id: str | None = None,
    case_version: str | None = None,
) -> dict[str, object]:
    """Compare methods using only the persisted trace snapshot."""

    snapshot = model_trace.get("snapshot") if isinstance(model_trace, Mapping) else None
    fingerprint = model_trace.get("input_fingerprint") if isinstance(model_trace, Mapping) else None
    if not isinstance(snapshot, Mapping) or not isinstance(fingerprint, str) or not fingerprint:
        reason = "immutable input snapshot or fingerprint is unavailable"
        return {
            "input_fingerprint": fingerprint,
            "methods": [_unavailable(name, reason) for name in METHODS],
        }
    if snapshot.get("rules_identity") != ACTIVE_RULES_IDENTITY:
        reason = "active rules identity is unavailable for this snapshot"
        return {
            "input_fingerprint": fingerprint,
            "methods": [_unavailable(name, reason) for name in METHODS],
        }
    try:
        if _hash(_stable_trace_identity(dict(snapshot))) != fingerprint:
            reason = "immutable input snapshot fingerprint does not match"
            return {
                "input_fingerprint": fingerprint,
                "methods": [_unavailable(name, reason) for name in METHODS],
            }
    except (TypeError, ValueError):
        reason = "immutable input snapshot fingerprint cannot be verified"
        return {
            "input_fingerprint": fingerprint,
            "methods": [_unavailable(name, reason) for name in METHODS],
        }
    try:
        payment, invoices, credits, evidence, group = _facts(snapshot)
        rules, _ = _rules_method(payment, invoices, credits, evidence)
        bounded = dict(rules)
        bounded["method"] = "bounded_correction"
        bounded["reason"] = (
            "Bounded correction shares the same deterministic rules execution; "
            "reviewer action remains separate."
        )
        shadow, ranked_context = _shadow_method(payment, invoices, credits, group)
        direct_recording = find_verified_recording(
            snapshot=snapshot,
            input_fingerprint=fingerprint,
            mode="direct",
            case_id=case_id,
            case_version=case_version,
        )
        hybrid_recording = find_verified_recording(
            snapshot=snapshot,
            input_fingerprint=fingerprint,
            mode="hybrid",
            case_id=case_id,
            case_version=case_version,
            expected_ranked_candidates=ranked_context,
        )
        return {
            "input_fingerprint": fingerprint,
            "methods": [
                rules,
                bounded,
                shadow,
                _recorded_method(
                    "direct", direct_recording, payment, invoices, credits, group
                ),
                _recorded_method(
                    "hybrid", hybrid_recording, payment, invoices, credits, group
                ),
            ],
        }
    except SnapshotError as exc:
        reason = str(exc)
    except (KeyError, TypeError, ValueError) as exc:
        reason = f"immutable input snapshot is invalid: {exc}"
    return {
        "input_fingerprint": fingerprint,
        "methods": [_unavailable(name, reason) for name in METHODS],
    }


__all__ = ["METHODS", "SnapshotError", "compare_snapshot"]
