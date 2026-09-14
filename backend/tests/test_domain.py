from datetime import date

import pytest

from reconcile.domain.matching import propose, validate_allocation
from reconcile.domain.money import MoneyError, format_money, parse_money
from reconcile.domain.types import (
    CashLine,
    CreditFact,
    CreditLine,
    InvoiceFact,
    PaymentFact,
    ProposalStatus,
)
from reconcile.ingest.parsers import parse_batch, parse_csv_source, parse_message_context

DAY = date(2026, 1, 15)


def invoice(identifier: str, amount: int, customer: str = "c1") -> InvoiceFact:
    return InvoiceFact(customer, identifier, "Customer", date(2025, 12, 1), DAY, DAY, amount)


def payment(amount: int, reference: str = "") -> PaymentFact:
    return PaymentFact("acct", "tx", DAY, "Customer", reference, amount)


def test_money_is_exact_centavos_and_bounded() -> None:
    assert parse_money("54000") == 5_400_000
    assert parse_money("0.5") == 50
    assert format_money(parse_money("1.20")) == "1.20"
    with pytest.raises(MoneyError):
        parse_money("1.001")
    with pytest.raises(MoneyError):
        parse_money("01")
    with pytest.raises(MoneyError):
        parse_money("-1")


def test_required_example_has_unique_cash_and_credit_allocation() -> None:
    result = propose(
        payment(5_400_000),
        [invoice("100", 5_400_000), invoice("101", 3_000_000), invoice("102", 2_500_000)],
        [CreditFact("c1", "103", DAY, 100_000, "102")],
        {"message": "Apply invoices 101 and 102 and credit note 103 to 102"},
    )
    assert result.status == ProposalStatus.PROPOSED
    assert result.cash == (CashLine("101", 3_000_000), CashLine("102", 2_400_000))
    assert result.credits == (CreditLine("103", "102", 100_000),)


def test_exact_amount_without_identity_abstains() -> None:
    result = propose(payment(1_000_000), [invoice("a", 1_000_000), invoice("b", 1_000_000)])
    assert result.status == ProposalStatus.NEEDS_REVIEW
    assert result.cash == ()
    assert set(result.alternatives) == {("a",), ("b",)}


def test_group_with_unbalanced_amount_abstains() -> None:
    result = propose(
        payment(3_000_000),
        [invoice("a", 2_000_000), invoice("b", 2_000_000)],
        evidence={"m": "a and b"},
    )
    assert result.status == ProposalStatus.NEEDS_REVIEW


def test_credit_must_be_explicitly_linked() -> None:
    result = propose(
        payment(1_000_000),
        [invoice("a", 2_000_000)],
        [CreditFact("c1", "cr", DAY, 1_000_000, None)],
        {"m": "a cr"},
    )
    assert result.status == ProposalStatus.PROPOSED
    assert result.credits == ()


def test_allocation_validator_rejects_overconsumption() -> None:
    with pytest.raises(ValueError, match="invoice allocation"):
        validate_allocation(
            2_000_000, {"a": invoice("a", 1_000_000)}, {}, [CashLine("a", 1_000_001)], []
        )


def test_csv_validation_returns_row_errors_without_rounding() -> None:
    raw = (
        b"source_account_id,transaction_id,booking_date,payer_name,reference,amount,currency\n"
        b"acct,tx,2026-01-15,C,,1.001,MXN\n"
    )
    parsed = parse_csv_source("bank", raw)
    assert parsed.accepted_count == 0
    assert any(issue.code == "invalid_money" for issue in parsed.issues)


def test_message_context_requires_offset_and_association() -> None:
    context = parse_message_context("2026-01-15T12:00:00+00:00", "acct", "tx")
    assert context.payment_transaction_id == "tx"
    with pytest.raises(ValueError):
        parse_message_context("2026-01-15T12:00:00Z", "acct", "tx")


def test_parser_batch_requires_context_for_message() -> None:
    bank = (
        b"source_account_id,transaction_id,booking_date,payer_name,reference,amount,currency\n"
        b"a,t,2026-01-15,p,r,1,MXN\n"
    )
    invoices = (
        b"customer_id,customer_name,invoice_id,issued_date,due_date,balance_as_of,"
        b"outstanding_amount,currency\n"
        b"c,C,i,2026-01-01,2026-01-15,2026-01-15,1,MXN\n"
    )
    with pytest.raises(ValueError, match="context"):
        parse_batch(bank, invoices, message=b"invoice i")
