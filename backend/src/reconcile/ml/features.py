"""The deliberately small, shared offline/online candidate feature extractor."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from datetime import date
from difflib import SequenceMatcher
from typing import Any

FEATURE_NAMES: tuple[str, ...] = (
    "all_candidate_invoice_ids_explicitly_mentioned",
    "fraction_candidate_ids_explicitly_mentioned",
    "payer_customer_name_similarity",
    "normalized_cash_total_residual",
    "candidate_group_size",
    "max_due_date_distance_days",
    "explicit_eligible_credit_compatibility",
    "all_candidate_ids_mentioned_in_remittance",
    "missing_customer_identity",
    "conflicting_evidence_signal",
)
FEATURE_SCHEMA_VERSION = "ml-v1"
FEATURE_SCHEMA = FEATURE_NAMES
_IDENTIFIER_EDGE = r"A-Za-z0-9_:/-"


def _text(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _integer(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _identifier_mentioned(identifier: str, text: str) -> bool:
    if not identifier:
        return False
    for match in re.finditer(re.escape(identifier), text, flags=re.IGNORECASE):
        before = text[match.start() - 1] if match.start() else ""
        after = text[match.end()] if match.end() < len(text) else ""
        if before and re.match(rf"[{_IDENTIFIER_EDGE}]", before):
            continue
        if after and re.match(rf"[{_IDENTIFIER_EDGE}]", after):
            continue
        # A period can terminate an ID ("INV-1.") but not a dotted token
        # ("INV-1.example").
        if (
            after == "."
            and match.end() + 1 < len(text)
            and re.match(rf"[{_IDENTIFIER_EDGE}]", text[match.end() + 1])
        ):
            continue
        return True
    return False


def _date_value(value: Any) -> date | None:
    try:
        return date.fromisoformat(value) if isinstance(value, str) else None
    except ValueError:
        return None


def _normal_name(value: Any) -> str:
    # Punctuation and spacing are presentation, not identity evidence.
    return re.sub(r"[^\w]+", "", _text(value).casefold(), flags=re.UNICODE)


def _name_similarity(payer_name: Any, customer_names: Sequence[Any]) -> float:
    payer = _normal_name(payer_name)
    if not payer or not customer_names:
        return 0.0
    return max(
        SequenceMatcher(None, payer, _normal_name(customer_name), autojunk=False).ratio()
        for customer_name in customer_names
    )


def _candidate_invoice_ids(candidate: Mapping[str, Any]) -> tuple[str, ...]:
    raw = candidate.get("invoice_ids", ())
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        return ()
    return tuple(sorted({value for value in raw if isinstance(value, str) and value}))


def _rows(group: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    invoices = group.get("invoices", ())
    if not isinstance(invoices, Sequence) or isinstance(invoices, (str, bytes)):
        return {}
    return {
        invoice_id: invoice
        for invoice in invoices
        if isinstance(invoice, Mapping)
        for invoice_id in [invoice.get("invoice_id")]
        if isinstance(invoice_id, str) and invoice_id
    }


def _credit(group: Mapping[str, Any]) -> Mapping[str, Any] | None:
    credit = group.get("credit")
    return credit if isinstance(credit, Mapping) else None


def _lines(candidate: Mapping[str, Any], name: str) -> tuple[Mapping[str, Any], ...]:
    raw = candidate.get(name, ())
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        return ()
    return tuple(line for line in raw if isinstance(line, Mapping))


def candidate_features(group: Mapping[str, Any], candidate: Mapping[str, Any]) -> tuple[float, ...]:
    """Return exactly the ten ordered ML v1 numeric features for one candidate.

    Only decision-time observations in the input group are read.  Candidate IDs
    are used solely as tokens for explicit-reference evidence, never as numeric
    values or a source of ordering.
    """

    payment = group.get("payment")
    payment = payment if isinstance(payment, Mapping) else {}
    invoice_rows = _rows(group)
    invoice_ids = _candidate_invoice_ids(candidate)
    candidate_invoices = tuple(invoice_rows[item] for item in invoice_ids if item in invoice_rows)
    reference = _text(payment.get("reference"))
    message = _text(group.get("message"))
    all_text = f"{reference} {message}"
    explicit = tuple(_identifier_mentioned(item, all_text) for item in invoice_ids)
    mentioned_count = sum(explicit)
    group_size = len(invoice_ids)
    all_mentioned = float(bool(group_size) and mentioned_count == group_size)

    payment_amount = payment.get("amount")
    amount = abs(_integer(payment_amount))
    cash_total = sum(_integer(line.get("amount", 0)) for line in _lines(candidate, "cash"))
    residual = abs(amount - cash_total) / max(amount, 1)

    due_dates = [
        days
        for invoice in candidate_invoices
        for booking_date, due_date in [
            (_date_value(payment.get("booking_date")), _date_value(invoice.get("due_date")))
        ]
        if booking_date is not None and due_date is not None
        for days in [abs((due_date - booking_date).days)]
    ]

    credit = _credit(group)
    credit_lines = _lines(candidate, "credits")
    credit_id = credit.get("credit_note_id") if credit else None
    credit_invoice = credit.get("invoice_id") if credit else None
    credit_available = credit.get("available_amount") if credit else 0
    valid_credit_lines = tuple(
        line
        for line in credit_lines
        if line.get("credit_note_id") == credit_id
        and line.get("invoice_id") in invoice_ids
        and isinstance(line.get("amount"), (int, float))
        and isinstance(credit_available, (int, float))
        and 0 < line["amount"] <= credit_available
        and (not credit_invoice or line.get("invoice_id") == credit_invoice)
    )
    credit_conflict = bool(credit_lines) and (
        credit is None or len(valid_credit_lines) != len(credit_lines)
    )
    credit_compatible = float(bool(valid_credit_lines) and not credit_conflict)

    customer_ids = {invoice.get("customer_id") for invoice in candidate_invoices}
    missing_customer = float(not payment.get("customer_id"))
    conflicting = float(
        len(customer_ids) > 1
        or (
            bool(payment.get("customer_id"))
            and any(
                invoice.get("customer_id") != payment.get("customer_id")
                for invoice in candidate_invoices
            )
        )
        or credit_conflict
        or any(item not in invoice_rows for item in invoice_ids)
    )

    return (
        all_mentioned,
        mentioned_count / group_size if group_size else 0.0,
        _name_similarity(
            payment.get("payer_name"), [i.get("customer_name") for i in candidate_invoices]
        ),
        float(residual),
        float(group_size),
        float(max(due_dates, default=0)),
        credit_compatible,
        float(
            bool(group_size) and all(_identifier_mentioned(item, message) for item in invoice_ids)
        ),
        missing_customer,
        conflicting,
    )


def extract_features(group: Mapping[str, Any]) -> list[list[float]]:
    """Extract a group matrix while preserving candidate rows only."""

    candidates = group.get("candidates", ())
    if not isinstance(candidates, Sequence) or isinstance(candidates, (str, bytes)):
        return []
    return [
        list(candidate_features(group, candidate))
        for candidate in candidates
        if isinstance(candidate, Mapping)
    ]


# Explicit aliases make the shared transform easy to discover in offline and
# runtime callers without introducing a second implementation.
extract_candidate_features = extract_features
extract_group_features = extract_features
features_for_candidate = candidate_features
