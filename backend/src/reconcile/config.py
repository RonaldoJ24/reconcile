from __future__ import annotations

import os
from dataclasses import dataclass
from decimal import ROUND_CEILING, Decimal, InvalidOperation

MAX_EXECUTION_MICRODOLLARS = 50_000


def server_mode() -> str:
    mode = os.getenv("RECONCILE_MODE", "local").strip().lower()
    if mode not in {"local", "preview"}:
        raise RuntimeError("RECONCILE_MODE must be local or preview")
    return mode


@dataclass(frozen=True)
class InterpretationSettings:
    enabled: bool
    api_key: str | None
    model: str
    execution_id: str | None
    execution_microdollars: int


def interpretation_settings() -> InterpretationSettings:
    enabled = os.getenv("RECONCILE_LLM_ENABLED", "0") == "1"
    model = os.getenv("RECONCILE_LLM_MODEL", "deepseek-flash").strip()
    key = os.getenv("DEEPSEEK_API_KEY")
    execution_id = os.getenv("RECONCILE_LLM_EXECUTION_ID")
    raw_budget = os.getenv("RECONCILE_LLM_EXECUTION_BUDGET_USD", "0")
    try:
        microdollars = int(
            (Decimal(raw_budget) * Decimal(1_000_000)).to_integral_value(
                rounding=ROUND_CEILING
            )
        )
    except (InvalidOperation, ValueError) as exc:
        raise RuntimeError("RECONCILE_LLM_EXECUTION_BUDGET_USD must be a decimal") from exc
    if microdollars < 0 or microdollars > MAX_EXECUTION_MICRODOLLARS:
        raise RuntimeError("the Phase 4 execution budget must be between USD 0 and USD 0.05")
    if model != "deepseek-flash":
        raise RuntimeError("only the verified deepseek-flash rate card is enabled")
    if enabled and (not key or not execution_id or microdollars == 0):
        raise RuntimeError(
            "live interpretation requires a credential, execution ID, and nonzero budget"
        )
    return InterpretationSettings(enabled, key, model, execution_id, microdollars)
