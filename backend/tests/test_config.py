from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast
from uuid import uuid4

import pytest

import reconcile.config as config_module
from reconcile.api.app import _effective_provider_access, _interpretation_enabled
from reconcile.config import interpretation_settings, public_provider_access_enabled
from reconcile.interpretation.budget import BudgetPolicy, _scope_limits
from reconcile.persistence.models import Session, Workspace


def test_public_provider_access_is_opt_in(monkeypatch) -> None:
    monkeypatch.delenv("RECONCILE_PUBLIC_PROVIDER_ACCESS", raising=False)
    assert public_provider_access_enabled() is False

    monkeypatch.setenv("RECONCILE_PUBLIC_PROVIDER_ACCESS", "true")
    assert public_provider_access_enabled() is False

    monkeypatch.setenv("RECONCILE_PUBLIC_PROVIDER_ACCESS", "1")
    assert public_provider_access_enabled() is True


def test_public_provider_access_requires_valid_live_configuration(monkeypatch) -> None:
    record = cast(Session, SimpleNamespace(provider_access=False))
    workspace = cast(Workspace, SimpleNamespace(mode="preview"))
    monkeypatch.setenv("RECONCILE_MODE", "preview")
    monkeypatch.setenv("RECONCILE_PUBLIC_PROVIDER_ACCESS", "1")
    monkeypatch.setenv("RECONCILE_LLM_ENABLED", "1")
    monkeypatch.setenv("OPENAI_API_KEY", "server-only-test-key")
    monkeypatch.setenv("RECONCILE_LLM_EXECUTION_ID", "public-access-test")
    monkeypatch.setenv("RECONCILE_LLM_DAILY_BUDGET_USD", "0.10")
    monkeypatch.setenv("RECONCILE_LLM_EXECUTION_BUDGET_USD", "0.01")

    assert _effective_provider_access(record, workspace) is True
    assert _interpretation_enabled(record, workspace) is True

    monkeypatch.delenv("OPENAI_API_KEY")
    assert _effective_provider_access(record, workspace) is False
    monkeypatch.setenv("OPENAI_API_KEY", "server-only-test-key")
    monkeypatch.setenv("RECONCILE_LLM_DAILY_BUDGET_USD", "0")
    assert _effective_provider_access(record, workspace) is False

    monkeypatch.setenv("RECONCILE_PUBLIC_PROVIDER_ACCESS", "0")
    assert _effective_provider_access(record, workspace) is False
    assert _interpretation_enabled(record, workspace) is False


def test_invitation_access_survives_public_flag_and_runtime_changes(monkeypatch) -> None:
    record = cast(Session, SimpleNamespace(provider_access=True))
    workspace = cast(Workspace, SimpleNamespace(mode="preview"))
    monkeypatch.setenv("RECONCILE_PUBLIC_PROVIDER_ACCESS", "0")
    monkeypatch.setenv("RECONCILE_LLM_ENABLED", "0")

    assert _effective_provider_access(record, workspace) is True
    assert _interpretation_enabled(record, workspace) is False


def test_public_budget_caps_and_execution_id_roll_over_by_utc_day(monkeypatch) -> None:
    monkeypatch.setenv("RECONCILE_MODE", "preview")
    monkeypatch.setenv("RECONCILE_PUBLIC_PROVIDER_ACCESS", "1")
    monkeypatch.setenv("RECONCILE_LLM_ENABLED", "1")
    monkeypatch.setenv("OPENAI_API_KEY", "server-only-test-key")
    monkeypatch.setenv("RECONCILE_LLM_EXECUTION_ID", "public-preview")
    monkeypatch.setenv("RECONCILE_LLM_EXECUTION_BUDGET_USD", "not-used")
    monkeypatch.setenv("RECONCILE_LLM_DAILY_BUDGET_USD", "0.50")
    monkeypatch.setenv("RECONCILE_LLM_MONTHLY_BUDGET_USD", "5.00")
    monkeypatch.setattr(config_module, "_utc_day", lambda: "2026-09-21")

    first = interpretation_settings()

    assert first.day_microdollars == 500_000
    assert first.month_microdollars == 5_000_000
    assert first.execution_microdollars == 500_000
    assert first.execution_id == "public-preview:2026-09-21"

    monkeypatch.setattr(config_module, "_utc_day", lambda: "2026-09-22")
    second = interpretation_settings()
    assert second.execution_id == "public-preview:2026-09-22"
    assert second.execution_microdollars == first.execution_microdollars


@pytest.mark.parametrize(
    ("variable", "value"),
    [
        ("RECONCILE_LLM_DAILY_BUDGET_USD", "-0.01"),
        ("RECONCILE_LLM_DAILY_BUDGET_USD", "not-a-number"),
        ("RECONCILE_LLM_DAILY_BUDGET_USD", "NaN"),
        ("RECONCILE_LLM_DAILY_BUDGET_USD", "0.500001"),
        ("RECONCILE_LLM_MONTHLY_BUDGET_USD", "-0.01"),
        ("RECONCILE_LLM_MONTHLY_BUDGET_USD", "Infinity"),
        ("RECONCILE_LLM_MONTHLY_BUDGET_USD", "5.000001"),
    ],
)
def test_public_budget_values_fail_closed(monkeypatch, variable: str, value: str) -> None:
    monkeypatch.setenv("RECONCILE_MODE", "preview")
    monkeypatch.setenv("RECONCILE_PUBLIC_PROVIDER_ACCESS", "1")
    monkeypatch.setenv("RECONCILE_LLM_ENABLED", "0")
    monkeypatch.setenv(variable, value)

    with pytest.raises(RuntimeError, match=variable):
        interpretation_settings()


def test_nonpublic_runtime_keeps_legacy_execution_cap(monkeypatch) -> None:
    monkeypatch.setenv("RECONCILE_MODE", "local")
    monkeypatch.setenv("RECONCILE_PUBLIC_PROVIDER_ACCESS", "0")
    monkeypatch.setenv("RECONCILE_LLM_ENABLED", "1")
    monkeypatch.setenv("OPENAI_API_KEY", "server-only-test-key")
    monkeypatch.setenv("RECONCILE_LLM_EXECUTION_ID", "legacy-execution")
    monkeypatch.setenv("RECONCILE_LLM_EXECUTION_BUDGET_USD", "0.05")
    monkeypatch.setenv("RECONCILE_LLM_DAILY_BUDGET_USD", "not-used")
    monkeypatch.setenv("RECONCILE_LLM_MONTHLY_BUDGET_USD", "not-used")

    settings = interpretation_settings()

    assert settings.execution_id == "legacy-execution"
    assert settings.execution_microdollars == 50_000
    assert settings.day_microdollars == 100_000
    assert settings.month_microdollars == 1_000_000


def test_public_monthly_counter_scope_stays_global_across_daily_execution_ids() -> None:
    policy = BudgetPolicy(
        day_microdollars=500_000,
        month_microdollars=5_000_000,
        execution_microdollars=500_000,
    )
    session_id = uuid4()
    first = _scope_limits(
        policy,
        session_id=session_id,
        execution_id="public-preview:2026-09-21",
        at=datetime(2026, 9, 21, tzinfo=UTC),
    )
    second = _scope_limits(
        policy,
        session_id=session_id,
        execution_id="public-preview:2026-09-22",
        at=datetime(2026, 9, 22, tzinfo=UTC),
    )

    assert first["month:2026-09"] == second["month:2026-09"]
    assert first["month:2026-09"][0] == 5_000_000
    assert first["execution:public-preview:2026-09-21"][0] == 500_000
    assert second["execution:public-preview:2026-09-22"][0] == 500_000
