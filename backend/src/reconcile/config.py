from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_CEILING, Decimal, InvalidOperation

MAX_EXECUTION_MICRODOLLARS = 50_000
DEFAULT_DAY_MICRODOLLARS = 100_000
DEFAULT_MONTH_MICRODOLLARS = 1_000_000
MAX_PUBLIC_DAY_MICRODOLLARS = 500_000
MAX_PUBLIC_MONTH_MICRODOLLARS = 5_000_000


def server_mode() -> str:
    mode = os.getenv("RECONCILE_MODE", "local").strip().lower()
    if mode not in {"local", "preview"}:
        raise RuntimeError("RECONCILE_MODE must be local or preview")
    return mode


def provider_invite_hash() -> str | None:
    value = os.getenv("RECONCILE_PROVIDER_INVITE_SHA256", "").strip().lower()
    if not value:
        return None
    if re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise RuntimeError("RECONCILE_PROVIDER_INVITE_SHA256 must be a SHA-256 hex digest")
    return value


def public_provider_access_enabled() -> bool:
    """Return whether preview visitors may use the configured provider."""

    return os.getenv("RECONCILE_PUBLIC_PROVIDER_ACCESS", "0").strip() == "1"


@dataclass(frozen=True)
class InterpretationSettings:
    enabled: bool
    api_key: str | None
    model: str
    execution_id: str | None
    execution_microdollars: int
    day_microdollars: int = DEFAULT_DAY_MICRODOLLARS
    month_microdollars: int = DEFAULT_MONTH_MICRODOLLARS


def _budget_microdollars(
    raw: str,
    *,
    variable: str,
    maximum: int,
    maximum_usd: str,
) -> int:
    try:
        amount = Decimal(raw)
    except (InvalidOperation, ValueError) as exc:
        raise RuntimeError(f"{variable} must be a finite decimal") from exc
    if not amount.is_finite() or amount < 0:
        raise RuntimeError(f"{variable} must be a finite, nonnegative decimal")
    microdollars = int(
        (amount * Decimal(1_000_000)).to_integral_value(rounding=ROUND_CEILING)
    )
    if microdollars > maximum:
        raise RuntimeError(f"{variable} must be between USD 0 and USD {maximum_usd}")
    return microdollars


def _utc_day() -> str:
    return datetime.now(UTC).date().isoformat()


def interpretation_settings() -> InterpretationSettings:
    enabled = os.getenv("RECONCILE_LLM_ENABLED", "0") == "1"
    model = os.getenv("RECONCILE_LLM_MODEL", "gpt-6-luna").strip()
    key = os.getenv("OPENAI_API_KEY")
    public_preview = server_mode() == "preview" and public_provider_access_enabled()
    if public_preview:
        day_microdollars = _budget_microdollars(
            os.getenv("RECONCILE_LLM_DAILY_BUDGET_USD", "0.10"),
            variable="RECONCILE_LLM_DAILY_BUDGET_USD",
            maximum=MAX_PUBLIC_DAY_MICRODOLLARS,
            maximum_usd="0.50",
        )
        month_microdollars = _budget_microdollars(
            os.getenv("RECONCILE_LLM_MONTHLY_BUDGET_USD", "1.00"),
            variable="RECONCILE_LLM_MONTHLY_BUDGET_USD",
            maximum=MAX_PUBLIC_MONTH_MICRODOLLARS,
            maximum_usd="5.00",
        )
        execution_prefix = os.getenv("RECONCILE_LLM_EXECUTION_ID")
        execution_id = (
            f"{execution_prefix}:{_utc_day()}" if execution_prefix else None
        )
        if execution_id is not None and len(execution_id) > 100:
            raise RuntimeError("RECONCILE_LLM_EXECUTION_ID is too long for public preview")
        microdollars = day_microdollars
    else:
        day_microdollars = DEFAULT_DAY_MICRODOLLARS
        month_microdollars = DEFAULT_MONTH_MICRODOLLARS
        execution_id = os.getenv("RECONCILE_LLM_EXECUTION_ID")
        microdollars = _budget_microdollars(
            os.getenv("RECONCILE_LLM_EXECUTION_BUDGET_USD", "0"),
            variable="RECONCILE_LLM_EXECUTION_BUDGET_USD",
            maximum=MAX_EXECUTION_MICRODOLLARS,
            maximum_usd="0.05",
        )
    if model != "gpt-6-luna":
        raise RuntimeError("only the verified gpt-6-luna rate card is enabled")
    if enabled and (not key or not execution_id or microdollars == 0):
        raise RuntimeError(
            "live interpretation requires a credential, execution ID, and nonzero budget"
        )
    return InterpretationSettings(
        enabled,
        key,
        model,
        execution_id,
        microdollars,
        day_microdollars,
        month_microdollars,
    )
