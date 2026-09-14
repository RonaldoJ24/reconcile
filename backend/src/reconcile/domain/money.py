from __future__ import annotations

import re

CENTAVOS_MAX = 999_999_999_999
MONEY_RE = re.compile(r"(?:0|[1-9][0-9]{0,12})(?:\.[0-9]{1,2})?\Z")


class MoneyError(ValueError):
    pass


def parse_money(value: str) -> int:
    """Parse an MXN decimal string without going through a float."""
    if not isinstance(value, str) or not MONEY_RE.fullmatch(value):
        raise MoneyError("money must be a non-negative base-10 amount with at most 2 decimals")
    whole, _, fraction = value.partition(".")
    cents = int(whole) * 100 + int((fraction + "00")[:2])
    if cents > CENTAVOS_MAX:
        raise MoneyError("money is out of range")
    return cents


def format_money(centavos: int) -> str:
    if not isinstance(centavos, int) or centavos < 0 or centavos > CENTAVOS_MAX:
        raise MoneyError("money is out of range")
    return f"{centavos // 100}.{centavos % 100:02d}"
