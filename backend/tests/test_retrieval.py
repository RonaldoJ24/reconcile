from reconcile.persistence.service import (
    MAX_RETRIEVED_INVOICES,
    _folio_fragments,
    _folio_match_length,
)


def test_folio_fragments_keep_abbreviations_and_skip_amounts() -> None:
    text = "pago fact 1432 y 33 menos nc-88 por $54,000.00"

    assert _folio_fragments(text) == ("1432", "33", "88")


def test_folio_fragments_are_bounded_and_unique() -> None:
    text = " ".join(str(10 + index) for index in range(20)) + " 10 11"

    fragments = _folio_fragments(text)

    assert len(fragments) == 8
    assert len(set(fragments)) == len(fragments)


def test_folio_match_uses_the_final_digit_run() -> None:
    fragments = ("1432", "33")

    assert _folio_match_length("F-1432", fragments) == 4
    assert _folio_match_length("F-1433", fragments) == 2
    assert _folio_match_length("F-1436", fragments) == 0
    assert _folio_match_length("SERIE-A", fragments) == 0


def test_retrieval_keeps_the_existing_invoice_bound() -> None:
    assert MAX_RETRIEVED_INVOICES == 10
