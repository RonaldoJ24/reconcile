"""Deterministic, stateful financial safety sequences for the release gate."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from random import Random

import pytest

from reconcile.domain.matching import validate_allocation
from reconcile.domain.types import (
    CashLine,
    CreditFact,
    CreditLine,
    InvoiceFact,
    PaymentFact,
)

SEQUENCE_COUNT = 1_000
DAY = date(2026, 9, 14)


def _invoice(identifier: str, amount: int) -> InvoiceFact:
    return InvoiceFact("customer", identifier, "Customer", DAY, DAY, DAY, amount)


def _payment(identifier: str, amount: int) -> PaymentFact:
    return PaymentFact("account", identifier, DAY, "Customer", identifier, amount)


@dataclass(slots=True)
class _Application:
    payment_id: str
    cash: tuple[CashLine, ...]
    credits: tuple[CreditLine, ...]
    active: bool = True


@dataclass(slots=True)
class _Ledger:
    """Small state machine that routes every accepted operation through the domain validator."""

    payments: dict[str, PaymentFact]
    invoices: dict[str, InvoiceFact]
    credits: dict[str, CreditFact]
    applications: dict[int, _Application] = field(default_factory=dict)
    apply_keys: dict[str, tuple[object, int]] = field(default_factory=dict)
    reverse_keys: dict[str, tuple[object, int]] = field(default_factory=dict)

    def _active_cash(self) -> tuple[dict[str, int], dict[str, int]]:
        by_payment: dict[str, int] = {}
        by_invoice: dict[str, int] = {}
        for application in self.applications.values():
            if not application.active:
                continue
            by_payment[application.payment_id] = by_payment.get(application.payment_id, 0) + sum(
                line.amount for line in application.cash
            )
            for line in application.cash:
                by_invoice[line.invoice_id] = by_invoice.get(line.invoice_id, 0) + line.amount
        return by_payment, by_invoice

    def _active_credit(self) -> tuple[dict[str, int], dict[str, int]]:
        by_credit: dict[str, int] = {}
        by_invoice: dict[str, int] = {}
        for application in self.applications.values():
            if not application.active:
                continue
            for line in application.credits:
                by_credit[line.credit_note_id] = (
                    by_credit.get(line.credit_note_id, 0) + line.amount
                )
                by_invoice[line.invoice_id] = by_invoice.get(line.invoice_id, 0) + line.amount
        return by_credit, by_invoice

    def apply(
        self,
        payment_id: str,
        cash: tuple[CashLine, ...],
        credits: tuple[CreditLine, ...],
        key: str,
    ) -> int:
        payload = (payment_id, cash, credits)
        known = self.apply_keys.get(key)
        if known is not None:
            if known[0] != payload:
                raise ValueError("idempotency key was used with a different payload")
            return known[1]

        payment = self.payments[payment_id]
        already_cash_by_payment, already_cash_by_invoice = self._active_cash()
        already_credit, already_credit_by_invoice = self._active_credit()
        if (
            already_cash_by_payment.get(payment_id, 0) + sum(line.amount for line in cash)
            > payment.amount
        ):
            raise ValueError("cash allocation exceeds payment")
        validate_allocation(
            payment.amount,
            self.invoices,
            self.credits,
            cash,
            credits,
            already_cash=already_cash_by_invoice,
            already_credit=already_credit,
            already_credit_by_invoice=already_credit_by_invoice,
        )
        application_id = len(self.applications) + 1
        self.applications[application_id] = _Application(payment_id, cash, credits)
        self.apply_keys[key] = (payload, application_id)
        return application_id

    def reverse(self, application_id: int, key: str) -> int:
        known = self.reverse_keys.get(key)
        if known is not None:
            if known[0] != application_id:
                raise ValueError("idempotency key was used with a different payload")
            return known[1]
        application = self.applications[application_id]
        if not application.active:
            raise ValueError("application is already reversed")
        application.active = False
        self.reverse_keys[key] = (application_id, application_id)
        return application_id

    def assert_invariants(self) -> None:
        active_cash_by_payment, active_cash_by_invoice = self._active_cash()
        active_credit_by_note, active_credit_by_invoice = self._active_credit()
        total_cash = 0
        total_unapplied = 0
        for payment_id, payment in self.payments.items():
            cash = active_cash_by_payment.get(payment_id, 0)
            assert 0 <= cash <= payment.amount
            total_cash += cash
            total_unapplied += payment.amount - cash
        assert total_cash + total_unapplied == sum(p.amount for p in self.payments.values())

        for invoice_id, invoice in self.invoices.items():
            assert (
                active_cash_by_invoice.get(invoice_id, 0)
                + active_credit_by_invoice.get(invoice_id, 0)
                <= invoice.outstanding_amount
            )
        for credit_id, credit in self.credits.items():
            assert active_credit_by_note.get(credit_id, 0) <= credit.available_amount


def _new_ledger(rng: Random) -> _Ledger:
    payment_zero = 20_000 + rng.randrange(40_000)
    credit_zero = 1_000 + rng.randrange(5_000)
    invoices = {
        "invoice-0": _invoice("invoice-0", payment_zero + credit_zero + rng.randrange(10_000)),
        "invoice-1": _invoice("invoice-1", 5_000 + rng.randrange(25_000)),
        "invoice-2": _invoice("invoice-2", 5_000 + rng.randrange(25_000)),
        "invoice-3": _invoice("invoice-3", 5_000 + rng.randrange(25_000)),
    }
    payments = {
        "payment-0": _payment("payment-0", payment_zero),
        "payment-1": _payment("payment-1", 5_000 + rng.randrange(20_000)),
        "payment-2": _payment("payment-2", 5_000 + rng.randrange(20_000)),
    }
    credits = {
        "credit-0": CreditFact("customer", "credit-0", DAY, credit_zero, "invoice-0"),
        "credit-1": CreditFact(
            "customer", "credit-1", DAY, 1_000 + rng.randrange(3_000), "invoice-1"
        ),
    }
    return _Ledger(payments, invoices, credits)


def _run_sequence(seed: int) -> None:
    rng = Random(seed)
    ledger = _new_ledger(rng)
    payment = ledger.payments["payment-0"]
    credit = ledger.credits["credit-0"]
    opening_cash = payment.amount // 2
    opening_credit = min(credit.available_amount, payment.amount // 5)
    opening = ledger.apply(
        "payment-0",
        (CashLine("invoice-0", opening_cash),),
        (CreditLine("credit-0", "invoice-0", opening_credit),),
        "apply-opening",
    )
    assert ledger.apply(
        "payment-0",
        (CashLine("invoice-0", opening_cash),),
        (CreditLine("credit-0", "invoice-0", opening_credit),),
        "apply-opening",
    ) == opening
    with pytest.raises(ValueError):
        ledger.apply(
            "payment-0",
            (CashLine("invoice-0", payment.amount),),
            (),
            "apply-overconsuming",
        )
    ledger.assert_invariants()

    assert ledger.reverse(opening, "reverse-opening") == opening
    assert ledger.reverse(opening, "reverse-opening") == opening
    with pytest.raises(ValueError, match="already reversed"):
        ledger.reverse(opening, "reverse-opening-again")
    ledger.assert_invariants()

    for step in range(24 + rng.randrange(17)):
        payment_id = rng.choice(tuple(ledger.payments))
        invoice_id = rng.choice(tuple(ledger.invoices))
        operation = rng.randrange(6)
        if operation == 0:
            payment_amount = ledger.payments[payment_id].amount
            cash_amount = rng.randrange(-payment_amount // 4, payment_amount * 2 + 1)
            cash = (CashLine(invoice_id, cash_amount),) if cash_amount else ()
            credit_id = rng.choice(tuple(ledger.credits))
            credit = ledger.credits[credit_id]
            credit_amount = rng.randrange(
                -credit.available_amount // 4, credit.available_amount * 2 + 1
            )
            credits = (
                (CreditLine(credit_id, invoice_id, credit_amount),) if credit_amount else ()
            )
            try:
                application_id = ledger.apply(payment_id, cash, credits, f"apply-{seed}-{step}")
            except ValueError:
                pass
            else:
                assert (
                    ledger.apply(payment_id, cash, credits, f"apply-{seed}-{step}")
                    == application_id
                )
        elif operation == 1 and ledger.applications:
            application_id = rng.choice(tuple(ledger.applications))
            application = ledger.applications[application_id]
            key = f"reverse-{seed}-{step}"
            if application.active:
                assert ledger.reverse(application_id, key) == application_id
                assert ledger.reverse(application_id, key) == application_id
            else:
                with pytest.raises(ValueError, match="already reversed"):
                    ledger.reverse(application_id, key)
        elif operation == 2:
            payment_amount = ledger.payments[payment_id].amount
            with pytest.raises(ValueError):
                ledger.apply(
                    payment_id,
                    (CashLine(invoice_id, payment_amount + 1),),
                    (),
                    f"over-{seed}-{step}",
                )
        elif operation == 3:
            credit_id = rng.choice(tuple(ledger.credits))
            credit = ledger.credits[credit_id]
            wrong_invoice = next(
                candidate for candidate in ledger.invoices if candidate != credit.invoice_id
            )
            with pytest.raises(ValueError, match="credit"):
                ledger.apply(
                    payment_id,
                    (),
                    (CreditLine(credit_id, wrong_invoice, 1),),
                    f"wrong-credit-{seed}-{step}",
                )
        elif operation == 4:
            payment_amount = ledger.payments[payment_id].amount
            invoice_ids = rng.sample(tuple(ledger.invoices), 2)
            first_amount = rng.randrange(1, payment_amount)
            second_amount = rng.randrange(1, payment_amount - first_amount + 1)
            cash = (
                CashLine(invoice_ids[0], first_amount),
                CashLine(invoice_ids[1], second_amount),
            )
            try:
                application_id = ledger.apply(payment_id, cash, (), f"group-{seed}-{step}")
            except ValueError:
                pass
            else:
                assert (
                    ledger.apply(payment_id, cash, (), f"group-{seed}-{step}")
                    == application_id
                )
        else:
            ledger.assert_invariants()
        ledger.assert_invariants()


@pytest.mark.parametrize("seed", range(SEQUENCE_COUNT))
def test_generated_stateful_financial_sequences_preserve_invariants(seed: int) -> None:
    _run_sequence(seed)
