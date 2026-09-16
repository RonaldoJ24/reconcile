from __future__ import annotations

import pytest
from pydantic import ValidationError

from reconcile.api.schemas import AllocationLine, CreditAllocationLine


@pytest.mark.parametrize("value", [True, False, "100", 100.0, 100.5])
def test_allocation_amounts_require_json_integers(value: object) -> None:
    with pytest.raises(ValidationError):
        AllocationLine(invoice_id="INV-1", amount=value)

    with pytest.raises(ValidationError):
        CreditAllocationLine(credit_note_id="CR-1", invoice_id="INV-1", amount=value)


def test_allocation_amounts_accept_centavo_integer() -> None:
    assert AllocationLine(invoice_id="INV-1", amount=100).amount == 100
    assert (
        CreditAllocationLine(credit_note_id="CR-1", invoice_id="INV-1", amount=100).amount
        == 100
    )
