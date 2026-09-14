from __future__ import annotations

import math
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from reconcile.persistence.models import InterpretationBudgetCounter, InterpretationCall


class BudgetExceeded(RuntimeError):
    def __init__(self, scope: str, dimension: str):
        super().__init__(f"{scope} {dimension} budget exhausted")
        self.scope = scope
        self.dimension = dimension


@dataclass(frozen=True)
class RateCard:
    verified_on: str = "2026-09-14"
    model: str = "deepseek-flash"
    uncached_input_usd_per_million: Decimal = Decimal("0.30")
    cached_input_usd_per_million: Decimal = Decimal("0.006")
    output_usd_per_million: Decimal = Decimal("1.20")

    def estimated_microdollars(
        self, *, input_tokens: int, output_tokens: int, cached_input_tokens: int = 0
    ) -> int:
        if min(input_tokens, output_tokens, cached_input_tokens) < 0:
            raise ValueError("token counts cannot be negative")
        if cached_input_tokens > input_tokens:
            raise ValueError("cached input tokens cannot exceed input tokens")
        uncached = input_tokens - cached_input_tokens
        microdollars = (
            Decimal(uncached) * self.uncached_input_usd_per_million
            + Decimal(cached_input_tokens) * self.cached_input_usd_per_million
            + Decimal(output_tokens) * self.output_usd_per_million
        )
        return math.ceil(microdollars)


@dataclass(frozen=True)
class BudgetPolicy:
    policy_version: str = "llm-budget-v1"
    day_microdollars: int = 100_000
    month_microdollars: int = 1_000_000
    execution_microdollars: int = 50_000
    session_attempts: int = 5
    global_day_attempts: int = 25
    execution_attempts: int = 5
    max_input_tokens: int = 6_000
    max_output_tokens: int = 2_048

    @property
    def attempt_tokens(self) -> int:
        return self.max_input_tokens + self.max_output_tokens

    @property
    def session_tokens(self) -> int:
        return self.session_attempts * self.attempt_tokens

    @property
    def day_tokens(self) -> int:
        return self.global_day_attempts * self.attempt_tokens

    @property
    def execution_tokens(self) -> int:
        return self.execution_attempts * self.attempt_tokens


@dataclass(frozen=True)
class Usage:
    input_tokens: int
    output_tokens: int
    cached_input_tokens: int = 0
    reasoning_tokens: int | None = None


def _scope_limits(
    policy: BudgetPolicy,
    *,
    session_id: uuid.UUID,
    execution_id: str,
    at: datetime,
) -> dict[str, tuple[int, int, int | None]]:
    day = at.astimezone(UTC).date().isoformat()
    month = day[:7]
    return {
        f"day:{day}": (
            policy.day_microdollars,
            policy.day_tokens,
            policy.global_day_attempts,
        ),
        f"month:{month}": (policy.month_microdollars, 31 * policy.day_tokens, None),
        f"session:{session_id}:{day}": (
            policy.day_microdollars,
            policy.session_tokens,
            policy.session_attempts,
        ),
        f"execution:{execution_id}": (
            policy.execution_microdollars,
            policy.execution_tokens,
            policy.execution_attempts,
        ),
    }


def reserve_attempt(
    session: Session,
    *,
    workspace_id: uuid.UUID,
    session_id: uuid.UUID,
    execution_id: str,
    mode: str,
    requested_model: str,
    attempt: int,
    policy: BudgetPolicy,
    rate_card: RateCard,
    at: datetime | None = None,
) -> uuid.UUID:
    """Reserve and commit quota before a call, leaving no transaction open."""

    if mode not in {"direct", "hybrid"}:
        raise ValueError("mode must be direct or hybrid")
    if not execution_id or len(execution_id) > 100:
        raise ValueError("execution_id must contain 1 to 100 characters")
    when = at or datetime.now(UTC)
    reserved_cost = rate_card.estimated_microdollars(
        input_tokens=policy.max_input_tokens,
        output_tokens=policy.max_output_tokens,
    )
    limits = _scope_limits(
        policy, session_id=session_id, execution_id=execution_id, at=when
    )
    keys = sorted(limits)
    try:
        session.execute(
            insert(InterpretationBudgetCounter)
            .values(
                [
                    {
                        "key": key,
                        "reserved_microdollars": 0,
                        "committed_microdollars": 0,
                        "reserved_tokens": 0,
                        "committed_tokens": 0,
                        "attempts": 0,
                        "updated_at": when,
                    }
                    for key in keys
                ]
            )
            .on_conflict_do_nothing(index_elements=["key"])
        )
        counters = list(
            session.scalars(
                select(InterpretationBudgetCounter)
                .where(InterpretationBudgetCounter.key.in_(keys))
                .order_by(InterpretationBudgetCounter.key)
                .with_for_update()
            )
        )
        if len(counters) != len(keys):
            raise RuntimeError("budget counters could not be locked")
        for counter in counters:
            money_limit, token_limit, attempt_limit = limits[counter.key]
            if attempt_limit is not None and counter.attempts + 1 > attempt_limit:
                raise BudgetExceeded(counter.key, "attempts")
            if (
                counter.reserved_microdollars
                + counter.committed_microdollars
                + reserved_cost
                > money_limit
            ):
                raise BudgetExceeded(counter.key, "money")
            if (
                counter.reserved_tokens
                + counter.committed_tokens
                + policy.attempt_tokens
                > token_limit
            ):
                raise BudgetExceeded(counter.key, "tokens")
        for counter in counters:
            counter.reserved_microdollars += reserved_cost
            counter.reserved_tokens += policy.attempt_tokens
            counter.attempts += 1
            counter.updated_at = when
        call = InterpretationCall(
            workspace_id=workspace_id,
            session_id=session_id,
            execution_id=execution_id,
            mode=mode,
            requested_model=requested_model,
            attempt=attempt,
            reservation_microdollars=reserved_cost,
            created_at=when,
        )
        session.add(call)
        session.commit()
        return call.id
    except Exception:
        session.rollback()
        raise


def finalize_attempt(
    session: Session,
    *,
    call_id: uuid.UUID,
    policy: BudgetPolicy,
    rate_card: RateCard,
    status: str,
    usage: Usage | None = None,
    response_model: str | None = None,
    latency_ms: int | None = None,
    error_code: str | None = None,
    billing_unknown: bool = False,
    at: datetime | None = None,
) -> InterpretationCall:
    """Reconcile a reservation, retaining it when billing is indeterminate."""

    if status not in {"SUCCEEDED", "FAILED"}:
        raise ValueError("status must be SUCCEEDED or FAILED")
    if status == "SUCCEEDED" and usage is None:
        raise ValueError("successful calls require usage")
    when = at or datetime.now(UTC)
    try:
        call = session.scalar(
            select(InterpretationCall)
            .where(InterpretationCall.id == call_id)
            .with_for_update()
        )
        if call is None:
            raise LookupError("interpretation call not found")
        if call.status != "RESERVED":
            raise RuntimeError("interpretation call is already finalized")
        limits = _scope_limits(
            policy,
            session_id=call.session_id,
            execution_id=call.execution_id,
            at=call.created_at,
        )
        counters = list(
            session.scalars(
                select(InterpretationBudgetCounter)
                .where(InterpretationBudgetCounter.key.in_(sorted(limits)))
                .order_by(InterpretationBudgetCounter.key)
                .with_for_update()
            )
        )
        if len(counters) != len(limits):
            raise RuntimeError("reserved budget counters are missing")
        call.response_model = response_model
        call.latency_ms = latency_ms
        call.error_code = error_code
        call.finished_at = when
        if billing_unknown:
            call.status = "UNKNOWN_BILLING"
            session.commit()
            return call
        for counter in counters:
            counter.reserved_microdollars -= call.reservation_microdollars
            counter.reserved_tokens -= policy.attempt_tokens
            counter.updated_at = when
        call.reservation_retained = False
        if status == "SUCCEEDED" and usage is not None:
            estimated = rate_card.estimated_microdollars(
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cached_input_tokens=usage.cached_input_tokens,
            )
            used_tokens = usage.input_tokens + usage.output_tokens
            for counter in counters:
                counter.committed_microdollars += estimated
                counter.committed_tokens += used_tokens
            call.estimated_microdollars = estimated
            call.input_tokens = usage.input_tokens
            call.output_tokens = usage.output_tokens
            call.cached_input_tokens = usage.cached_input_tokens
            call.reasoning_tokens = usage.reasoning_tokens
            call.provider_cache_hit = usage.cached_input_tokens > 0
            call.status = "SUCCEEDED"
        else:
            call.status = "FAILED"
        session.commit()
        return call
    except Exception:
        session.rollback()
        raise
