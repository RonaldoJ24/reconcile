from __future__ import annotations

from reconcile.api.cases import CASES, LIBRARY_CASES
from reconcile.domain.matching import propose
from reconcile.domain.types import CreditFact, InvoiceFact, PaymentFact, ProposalStatus
from reconcile.persistence.service import _hash, _stable_trace_identity


def _facts(packet):
    parsed = packet.parse()
    bank = parsed.bank.rows[0]
    payment = PaymentFact(
        bank["source_account_id"],
        bank["transaction_id"],
        bank["booking_date"],
        bank["payer_name"],
        bank["reference"],
        bank["amount"],
        None,
        "payment-source",
        1,
    )
    invoices = [
        InvoiceFact(
            row["customer_id"],
            row["invoice_id"],
            row["customer_name"],
            row["issued_date"],
            row["due_date"],
            row["balance_as_of"],
            row["outstanding_amount"],
            1,
        )
        for row in parsed.invoices.rows
    ]
    credits = [
        CreditFact(
            row["customer_id"],
            row["credit_note_id"],
            row["balance_as_of"],
            row["available_amount"],
            row["invoice_id"],
            1,
        )
        for row in (parsed.credits.rows if parsed.credits else ())
    ]
    evidence = {"message": parsed.message.text} if parsed.message else {}
    return payment, invoices, credits, evidence


def test_registered_packets_round_trip_through_the_parser() -> None:
    assert [packet.case_id for packet in CASES] == [
        "spei-shorthand",
        "partial-installment",
        "unclear-reference",
        "clean-reference",
        "hidden-instruction",
        "straightforward",
        "bundle",
        "correction",
        "insufficient",
        "adversarial",
    ]
    for packet in CASES:
        for profile in ("local", "preview"):
            parsed = packet.parse(profile=profile)
            assert parsed.bank.accepted_count == 1
            assert parsed.invoices.accepted_count >= 1
            assert not parsed.bank.issues and not parsed.invoices.issues
            assert parsed.message is not None
        if packet.group == "regression":
            assert parsed.bank.rows[0]["reference"] == "—"


def test_library_cases_look_like_mexican_receivables() -> None:
    library = [packet for packet in CASES if packet.group == "library"]
    assert [packet.case_id for packet in library] == [
        packet.case_id for packet in LIBRARY_CASES
    ]
    customers = set()
    for packet in library:
        parsed = packet.parse()
        # SPEI concept fields are short; realistic references must fit them.
        assert len(parsed.bank.rows[0]["reference"]) <= 40
        assert parsed.bank.rows[0]["currency"] == "MXN"
        customer_ids = {row["customer_id"] for row in parsed.invoices.rows}
        assert len(customer_ids) == 1
        assert not customer_ids & customers
        customers |= customer_ids
        assert all(row["invoice_id"].startswith("F-") for row in parsed.invoices.rows)


def test_registered_variants_are_server_owned_and_parseable() -> None:
    packet = CASES[0]
    assert packet.variant("original").message == packet.message
    for name in ("ambiguous", "prompt_like"):
        variant = packet.variant(name)
        assert variant.message is not None
        assert variant.message != packet.message
        parsed = variant.parse()
        assert parsed.message is not None
        assert parsed.message.raw == variant.message


def test_registered_packets_use_the_ordinary_conservative_engine() -> None:
    expected = {
        "spei-shorthand": ProposalStatus.NEEDS_REVIEW,
        "partial-installment": ProposalStatus.NEEDS_REVIEW,
        "unclear-reference": ProposalStatus.NEEDS_REVIEW,
        "clean-reference": ProposalStatus.PROPOSED,
        "hidden-instruction": ProposalStatus.NEEDS_REVIEW,
        "straightforward": ProposalStatus.PROPOSED,
        "bundle": ProposalStatus.PROPOSED,
        "correction": ProposalStatus.NEEDS_REVIEW,
        "insufficient": ProposalStatus.NEEDS_REVIEW,
        "adversarial": ProposalStatus.NEEDS_REVIEW,
    }
    for packet in CASES:
        payment, invoices, credits, evidence = _facts(packet)
        result = propose(payment, invoices, credits, evidence)
        assert result.status == expected[packet.case_id]
    hidden = next(packet for packet in CASES if packet.case_id == "hidden-instruction")
    assert propose(*_facts(hidden)).reason == "prompt-like instruction"
    clean = next(packet for packet in CASES if packet.case_id == "clean-reference")
    assert [(line.invoice_id, line.amount) for line in propose(*_facts(clean)).cash] == [
        ("F-4410", 845_000)
    ]


def test_trace_fingerprint_omits_workspace_owned_identifiers() -> None:
    def snapshot(suffix: str) -> dict[str, object]:
        return {
            "rules_identity": "rules-v2-conservative",
            "payment": {
                "id": f"payment-{suffix}",
                "source_id": f"bank-{suffix}",
                "source_account_id": "acct",
                "transaction_id": "case-payment",
                "booking_date": "2026-01-15",
                "payer_name": "Case Customer",
                "reference": "—",
                "amount": 100,
                "currency": "MXN",
                "customer_id": None,
                "version": 1,
            },
            "invoices": [
                {
                    "id": f"invoice-{suffix}",
                    "source_id": f"invoice-source-{suffix}",
                    "customer_id": "case-customer",
                    "invoice_id": "case-invoice",
                    "issued_date": "2025-12-01",
                    "due_date": "2026-01-01",
                    "balance_as_of": "2026-01-15",
                    "outstanding_amount": 100,
                    "currency": "MXN",
                    "version": 1,
                }
            ],
            "credits": [],
            "sources": [
                {
                    "source_id": f"bank-{suffix}",
                    "kind": "bank",
                    "sha256": "bank-hash",
                    "version": "v1",
                    "batch_id": f"batch-{suffix}",
                },
                {
                    "source_id": f"invoice-source-{suffix}",
                    "kind": "invoice",
                    "sha256": "invoice-hash",
                    "version": "v1",
                    "batch_id": f"batch-{suffix}",
                },
                {
                    "source_id": f"message-{suffix}",
                    "kind": "message",
                    "sha256": "message-hash",
                    "version": "v1",
                    "batch_id": f"batch-{suffix}",
                },
            ],
            "evidence": {f"message-{suffix}": "Apply invoice case-invoice."},
            "candidate_context": {
                "group_id": f"payment-{suffix}",
                "message": "Apply invoice case-invoice.",
                "candidates": [{"candidate_id": "candidate-stable"}],
            },
        }

    first = _stable_trace_identity(snapshot("one"))
    second = _stable_trace_identity(snapshot("two"))
    assert _hash(first) == _hash(second)
