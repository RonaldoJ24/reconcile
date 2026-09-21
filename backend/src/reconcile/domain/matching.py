from __future__ import annotations

import re
from collections.abc import Iterable, Mapping

from .types import (
    CashLine,
    Correction,
    CreditFact,
    CreditLine,
    Evidence,
    InvoiceFact,
    PaymentFact,
    ProposalResult,
    ProposalStatus,
)

_STRUCTURED_DELIMITERS = frozenset("._:/-")
_ACTION_RE = re.compile(
    r"\b(?:apply|aplicar)\b",
    re.IGNORECASE,
)
_INSTRUCTION_WORDS = re.compile(
    r"^(?:please|apply|the|payment|to|for|invoice|invoices|factura|facturas|a|al|la|el|"
    r"las|los|para|por|favor|and|y|credit|note|nota|number|of|aplicar|pago|,|;|&|\s)+$",
    re.IGNORECASE,
)
_NEGATION_RE = re.compile(
    r"\b(?:do\s+not|don't|no\s+aplicar|not|without)\b",
    re.IGNORECASE,
)
_UNCERTAINTY_RE = re.compile(
    r"\b(?:maybe|perhaps|possibly|uncertain|tal\s+vez|quizá|quizas|posiblemente)\b|\?",
    re.IGNORECASE,
)
_CONTRADICTION_RE = re.compile(
    r"\b(?:instead|but|however|sino|en\s+vez\s+de)\b",
    re.IGNORECASE,
)
_PROMPT_RE = re.compile(
    r"\b(?:ignore|disregard)\b[^.!?\n]{0,80}\b(?:previous|prior|system|instructions?)\b|"
    r"<\s*(?:system|assistant|user)\s*>",
    re.IGNORECASE,
)
_REFERENCE_WORDS_RE = re.compile(
    r"^(?:the|a|an|la|el|los|las|invoice|invoices|factura|facturas|number|"
    r"credit|note|credit\s+note|nota|and|y|for|to|para|a|payment|,|;|&|\s)+$",
    re.IGNORECASE,
)


def _standalone(identifier: str, text: str, start: int, end: int) -> bool:
    """Accept terminal sentence punctuation but reject structured-ID substrings."""

    if start and text[start - 1].isalnum():
        return False
    if end < len(text) and text[end].isalnum():
        return False
    if start and text[start - 1] in _STRUCTURED_DELIMITERS:
        if start > 1 and text[start - 2].isalnum():
            return False
    if end < len(text) and text[end] in _STRUCTURED_DELIMITERS:
        if end + 1 < len(text) and text[end + 1].isalnum():
            return False
    return bool(identifier)


def _spans(identifier: str, text: str) -> tuple[re.Match[str], ...]:
    return tuple(
        match
        for match in re.finditer(re.escape(identifier), text, flags=re.IGNORECASE)
        if _standalone(identifier, text, match.start(), match.end())
    )


def _mentions(identifier: str, text: str) -> bool:
    return bool(_spans(identifier, text))


def _clauses(text: str) -> tuple[tuple[int, int, str], ...]:
    """Split source text while retaining offsets for review evidence."""

    result: list[tuple[int, int, str]] = []
    start = 0
    for match in re.finditer(r"[.!?;\n]+", text):
        if (
            match.group(0).startswith(".")
            and match.start()
            and match.end() < len(text)
            and text[match.start() - 1].isalnum()
            and text[match.end()].isalnum()
        ):
            continue
        end = match.end()
        if text[start:end].strip():
            result.append((start, end, text[start:end]))
        start = match.end()
    if text[start:].strip():
        result.append((start, len(text), text[start:]))
    return tuple(result)


def _canonical_references(
    text: str, invoice_ids: set[str], credit_ids: set[str]
) -> tuple[set[str], set[str]] | None:
    """Parse the intentionally tiny reference-only syntax.

    A line made solely of known identifiers and short reference connectors is
    unambiguous. Free-form prose mentioning an identifier is not accepted.
    """

    invoice_matches: list[re.Match[str]] = []
    credit_matches: list[re.Match[str]] = []
    for identifier in sorted(invoice_ids | credit_ids, key=len, reverse=True):
        target = invoice_matches if identifier in invoice_ids else credit_matches
        target.extend(_spans(identifier, text))
    matches = sorted([*invoice_matches, *credit_matches], key=lambda item: item.start())
    if not matches:
        return None
    cursor = 0
    remainder: list[str] = []
    selected_invoices: set[str] = set()
    selected_credits: set[str] = set()
    for match in matches:
        if match.start() < cursor:
            continue
        remainder.append(text[cursor : match.start()])
        value = match.group(0)
        if value.casefold() in {item.casefold() for item in invoice_ids}:
            selected_invoices.add(
                next(item for item in invoice_ids if item.casefold() == value.casefold())
            )
        else:
            selected_credits.add(
                next(item for item in credit_ids if item.casefold() == value.casefold())
            )
        cursor = match.end()
    remainder.append(text[cursor:])
    leftover = "".join(remainder).strip()
    leftover = re.sub(r"\s*[.!?]+\s*$", "", leftover).strip()
    if leftover and _REFERENCE_WORDS_RE.fullmatch(leftover) is None:
        return None
    return selected_invoices, selected_credits


def _unsafe_reason(clause: str) -> str | None:
    if _PROMPT_RE.search(clause):
        return "prompt-like instruction"
    if _NEGATION_RE.search(clause):
        return "negated invoice reference"
    if _CONTRADICTION_RE.search(clause):
        return "contradictory invoice references"
    if _UNCERTAINTY_RE.search(clause):
        return "uncertain invoice reference"
    return None


def _meaningful(clause: str) -> bool:
    return bool(clause.strip(" \t\r\n.!?;,"))


def _bounded_instruction(clause: str, spans: tuple[re.Match[str], ...]) -> bool:
    if not spans or _ACTION_RE.search(clause) is None:
        return False
    pieces: list[str] = []
    cursor = 0
    for match in spans:
        pieces.append(clause[cursor : match.start()])
        cursor = match.end()
    pieces.append(clause[cursor:])
    remainder = "".join(pieces).strip()
    remainder = re.sub(r"\s*[.!?]+\s*$", "", remainder).strip()
    return not remainder or _INSTRUCTION_WORDS.fullmatch(remainder) is not None


def _evidence_for(identifier: str, evidence: Mapping[str, str]) -> tuple[Evidence, ...]:
    found: list[Evidence] = []
    for source_id, text in evidence.items():
        for clause_start, clause_end, clause in _clauses(text):
            for match in _spans(identifier, clause):
                start = clause_start + len(clause) - len(clause.lstrip())
                end = clause_start + len(clause.rstrip())
                found.append(Evidence(source_id, start, end, text[start:end]))
    return tuple(found)


def propose(
    payment: PaymentFact,
    invoices: Iterable[InvoiceFact],
    credits: Iterable[CreditFact] = (),
    evidence: Mapping[str, str] | None = None,
) -> ProposalResult:
    """Produce a safe, rules-only proposal; evidence-free amounts never identify an invoice."""
    invoice_list = tuple(
        i
        for i in invoices
        if i.outstanding_amount > 0
        and i.balance_as_of <= payment.booking_date
        and (payment.customer_id is None or i.customer_id == payment.customer_id)
    )
    credit_list = tuple(
        c
        for c in credits
        if c.available_amount > 0
        and c.balance_as_of <= payment.booking_date
        and (payment.customer_id is None or c.customer_id == payment.customer_id)
    )
    invoice_ids = {item.invoice_id for item in invoice_list}
    credit_ids = {item.credit_note_id for item in credit_list}
    evidence_map = evidence or {}
    positive_invoice_ids: set[str] = set()
    positive_credit_ids: set[str] = set()
    mentioned_invoice_ids: set[str] = set()
    deferred_reason: str | None = None

    # Payment references are intentionally accepted only when they are an exact
    # known-ID list. This keeps the legacy ``invoice INV-1`` form while refusing
    # free-form prose as allocation intent.
    for reference in (payment.reference, *evidence_map.values()):
        for start, end, clause in _clauses(reference):
            if not _meaningful(clause):
                continue
            clause_invoice_ids = {
                item
                for item in invoice_ids
                if _spans(item, clause)
            }
            clause_credit_ids = {
                item
                for item in credit_ids
                if _spans(item, clause)
            }
            if not clause_invoice_ids and not clause_credit_ids:
                deferred_reason = deferred_reason or "unrecognized source text"
                continue
            mentioned_invoice_ids.update(clause_invoice_ids)
            reason = _unsafe_reason(clause)
            if reason is not None:
                deferred_reason = deferred_reason or reason
                continue
            canonical = _canonical_references(clause, invoice_ids, credit_ids)
            if canonical is not None:
                positive_invoice_ids.update(canonical[0])
                positive_credit_ids.update(canonical[1])
                continue
            clause_matches = tuple(
                sorted(
                    (
                        match
                        for item in (*invoice_ids, *credit_ids)
                        for match in _spans(item, clause)
                    ),
                    key=lambda item: item.start(),
                )
            )
            if _bounded_instruction(clause, clause_matches):
                positive_invoice_ids.update(clause_invoice_ids)
                positive_credit_ids.update(clause_credit_ids)
            else:
                deferred_reason = deferred_reason or "unrecognized source text"

    mentioned_invoices = tuple(i for i in invoice_list if i.invoice_id in mentioned_invoice_ids)
    exact = tuple(i for i in invoice_list if i.outstanding_amount == payment.amount)
    signals: list[str] = []
    if exact:
        signals.append("exact_amount_candidate")

    # Explicit identity is required before any allocation. Payer names and totals are signals only.
    if deferred_reason is not None:
        return ProposalResult(
            ProposalStatus.NEEDS_REVIEW,
            evidence=tuple(
                e
                for identifier in [*invoice_ids, *credit_ids]
                for e in _evidence_for(identifier, evidence_map)
            ),
            signals=tuple(signals) + ("unsafe_reference",),
            reason=deferred_reason,
        )
    if not mentioned_invoices or not positive_invoice_ids:
        alternatives = tuple((i.invoice_id,) for i in exact)
        return ProposalResult(
            ProposalStatus.NEEDS_REVIEW,
            alternatives=alternatives,
            signals=tuple(signals),
            reason=(
                "invoice mention is not a bounded allocation instruction"
                if mentioned_invoices
                else "no explicit invoice reference"
            ),
        )
    if positive_invoice_ids != {invoice.invoice_id for invoice in mentioned_invoices}:
        return ProposalResult(
            ProposalStatus.NEEDS_REVIEW,
            alternatives=(tuple(invoice.invoice_id for invoice in mentioned_invoices),),
            signals=tuple(signals) + ("uncertain_reference_scope",),
            reason="invoice reference scope is uncertain",
        )
    if len({invoice.customer_id for invoice in mentioned_invoices}) > 1:
        return ProposalResult(
            ProposalStatus.NEEDS_REVIEW,
            alternatives=(tuple(invoice.invoice_id for invoice in mentioned_invoices),),
            reason="selected invoices belong to different customers",
        )

    mentioned_credits = tuple(
        c
        for c in credit_list
        if c.invoice_id
        and c.credit_note_id in positive_credit_ids
        and any(
            i.invoice_id == c.invoice_id and i.customer_id == c.customer_id
            for i in mentioned_invoices
        )
    )
    if len(mentioned_invoices) == 1:
        invoice = mentioned_invoices[0]
        credit = next((c for c in mentioned_credits if c.invoice_id == invoice.invoice_id), None)
        if credit and payment.amount + credit.available_amount > invoice.outstanding_amount:
            return ProposalResult(
                ProposalStatus.NEEDS_REVIEW,
                signals=tuple(signals) + ("credit_exceeds_invoice",),
                reason="cash and credit exceed invoice balance",
            )
        cash = payment.amount
        if cash + (credit.available_amount if credit else 0) > invoice.outstanding_amount:
            return ProposalResult(
                ProposalStatus.NEEDS_REVIEW,
                signals=tuple(signals),
                reason="cash exceeds invoice balance",
            )
        return ProposalResult(
            ProposalStatus.PROPOSED,
            cash=(CashLine(invoice.invoice_id, cash),),
            credits=(
                CreditLine(credit.credit_note_id, invoice.invoice_id, credit.available_amount),
            )
            if credit
            else (),
            evidence=_evidence_for(invoice.invoice_id, evidence_map)
            + tuple(
                e
                for credit_item in mentioned_credits
                for e in _evidence_for(credit_item.credit_note_id, evidence_map)
            ),
            signals=tuple(signals) + ("explicit_invoice_reference",),
        )

    # A group is safe only when every selected invoice was explicit and totals reconcile.
    if len(mentioned_invoices) > 3:
        return ProposalResult(
            ProposalStatus.NEEDS_REVIEW, reason="more than three invoices referenced"
        )
    credit_total = sum(c.available_amount for c in mentioned_credits)
    target_total = sum(i.outstanding_amount for i in mentioned_invoices)
    if any(
        c.available_amount
        > next(i.outstanding_amount for i in mentioned_invoices if i.invoice_id == c.invoice_id)
        for c in mentioned_credits
    ):
        return ProposalResult(
            ProposalStatus.NEEDS_REVIEW,
            signals=tuple(signals) + ("credit_exceeds_invoice",),
            reason="credit exceeds its linked invoice balance",
        )
    if payment.amount + credit_total != target_total:
        return ProposalResult(
            ProposalStatus.NEEDS_REVIEW,
            alternatives=(tuple(i.invoice_id for i in mentioned_invoices),),
            signals=tuple(signals) + ("group_total_mismatch",),
            reason="explicit references do not produce a feasible full allocation",
        )
    credit_by_invoice = {c.invoice_id: c for c in mentioned_credits}
    cash_result = tuple(
        CashLine(
            i.invoice_id,
            i.outstanding_amount
            - credit_by_invoice.get(
                i.invoice_id, CreditFact("", "", i.balance_as_of, 0)
            ).available_amount,
        )
        for i in mentioned_invoices
    )
    return ProposalResult(
        ProposalStatus.PROPOSED,
        cash=tuple(line for line in cash_result if line.amount),
        credits=tuple(
            CreditLine(c.credit_note_id, c.invoice_id or "", c.available_amount)
            for c in mentioned_credits
        ),
        evidence=tuple(
            e
            for identifier in [
                *(i.invoice_id for i in mentioned_invoices),
                *(c.credit_note_id for c in mentioned_credits),
            ]
            for e in _evidence_for(identifier, evidence_map)
        ),
        signals=tuple(signals) + ("explicit_group_references",),
    )


def validate_allocation(
    payment_amount: int,
    invoices: Mapping[str, InvoiceFact],
    credits: Mapping[str, CreditFact],
    cash: Iterable[CashLine],
    credit_lines: Iterable[CreditLine],
    *,
    already_cash: Mapping[str, int] | None = None,
    already_credit: Mapping[str, int] | None = None,
    already_credit_by_invoice: Mapping[str, int] | None = None,
) -> None:
    """Validate proposal/application lines against opening balances and active consumption."""
    cash_lines = tuple(cash)
    credit_lines_tuple = tuple(credit_lines)
    if len({line.credit_note_id for line in credit_lines_tuple}) > 1:
        raise ValueError("proposal can contain at most one credit note")
    if (
        not 1
        <= len(
            {line.invoice_id for line in cash_lines}
            | {line.invoice_id for line in credit_lines_tuple}
        )
        <= 3
    ):
        raise ValueError("proposal must contain one to three invoices")
    if any(line.amount <= 0 for line in cash_lines) or any(
        line.amount <= 0 for line in credit_lines_tuple
    ):
        raise ValueError("application amounts must be positive")
    used_cash = sum(line.amount for line in cash_lines)
    if used_cash > payment_amount:
        raise ValueError("cash allocation exceeds payment")
    cash_by_invoice: dict[str, int] = {}
    for cash_line in cash_lines:
        if cash_line.invoice_id not in invoices:
            raise ValueError("unknown invoice")
        cash_by_invoice[cash_line.invoice_id] = (
            cash_by_invoice.get(cash_line.invoice_id, 0) + cash_line.amount
        )
    credit_by_invoice: dict[str, int] = {}
    used_credit: dict[str, int] = {}
    for credit_line in credit_lines_tuple:
        credit = credits.get(credit_line.credit_note_id)
        invoice = invoices.get(credit_line.invoice_id)
        if credit is None or invoice is None or credit.customer_id != invoice.customer_id:
            raise ValueError("credit and invoice relationship is invalid")
        if credit.invoice_id != credit_line.invoice_id:
            raise ValueError("credit must target its explicit invoice link")
        used_credit[credit_line.credit_note_id] = (
            used_credit.get(credit_line.credit_note_id, 0) + credit_line.amount
        )
        credit_by_invoice[credit_line.invoice_id] = (
            credit_by_invoice.get(credit_line.invoice_id, 0) + credit_line.amount
        )
    for credit_id, amount in used_credit.items():
        if amount + (already_credit or {}).get(credit_id, 0) > credits[credit_id].available_amount:
            raise ValueError("credit allocation exceeds available credit")
    for invoice_id in set(cash_by_invoice) | set(credit_by_invoice):
        consumed = cash_by_invoice.get(invoice_id, 0) + credit_by_invoice.get(invoice_id, 0)
        consumed += (already_cash or {}).get(invoice_id, 0) + (already_credit_by_invoice or {}).get(
            invoice_id, 0
        )
        if consumed > invoices[invoice_id].outstanding_amount:
            raise ValueError("invoice allocation exceeds available balance")


def correction(
    correction_request: Correction,
    invoices: Mapping[str, InvoiceFact],
    credits: Mapping[str, CreditFact],
    payment_amount: int,
) -> ProposalResult:
    validate_allocation(
        payment_amount, invoices, credits, correction_request.cash, correction_request.credits
    )
    return ProposalResult(
        ProposalStatus.NEEDS_REVIEW,
        correction_request.cash,
        correction_request.credits,
        reason="human correction",
    )
