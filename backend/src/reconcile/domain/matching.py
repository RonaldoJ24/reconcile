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


def _mentions(identifier: str, text: str) -> bool:
    return bool(
        re.search(rf"(?<![A-Za-z0-9._:/-]){re.escape(identifier)}(?![A-Za-z0-9._:/-])", text)
    )


def _evidence_for(identifier: str, evidence: Mapping[str, str]) -> tuple[Evidence, ...]:
    found: list[Evidence] = []
    for source_id, text in evidence.items():
        match = re.search(
            rf"(?<![A-Za-z0-9._:/-]){re.escape(identifier)}(?![A-Za-z0-9._:/-])", text
        )
        if match:
            found.append(Evidence(source_id, match.start(), match.end(), match.group(0)))
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
    all_text = " ".join((payment.reference, *(evidence or {}).values()))
    mentioned_invoices = tuple(i for i in invoice_list if _mentions(i.invoice_id, all_text))
    exact = tuple(i for i in invoice_list if i.outstanding_amount == payment.amount)
    signals: list[str] = []
    if exact:
        signals.append("exact_amount_candidate")

    # Explicit identity is required before any allocation. Payer names and totals are signals only.
    if not mentioned_invoices:
        alternatives = tuple((i.invoice_id,) for i in exact)
        return ProposalResult(
            ProposalStatus.NEEDS_REVIEW,
            alternatives=alternatives,
            signals=tuple(signals),
            reason="no explicit invoice reference",
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
        and _mentions(c.credit_note_id, all_text)
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
            evidence=_evidence_for(invoice.invoice_id, evidence or {})
            + tuple(
                e
                for credit_item in mentioned_credits
                for e in _evidence_for(credit_item.credit_note_id, evidence or {})
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
            for e in _evidence_for(identifier, evidence or {})
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
    if used_cash + sum((already_cash or {}).values()) > payment_amount:
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
