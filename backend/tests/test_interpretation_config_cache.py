from __future__ import annotations

import pytest

from reconcile.config import interpretation_settings
from reconcile.interpretation.cache import cache_key, source_fingerprint


def test_live_interpretation_is_disabled_without_explicit_enable(monkeypatch) -> None:
    monkeypatch.delenv("RECONCILE_LLM_ENABLED", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("RECONCILE_LLM_EXECUTION_ID", raising=False)
    monkeypatch.delenv("RECONCILE_LLM_EXECUTION_BUDGET_USD", raising=False)

    settings = interpretation_settings()

    assert settings.enabled is False
    assert settings.api_key is None
    assert settings.execution_microdollars == 0


def test_phase4_execution_budget_cannot_exceed_authorization(monkeypatch) -> None:
    monkeypatch.setenv("RECONCILE_LLM_EXECUTION_BUDGET_USD", "0.050001")

    with pytest.raises(RuntimeError, match="USD 0.05"):
        interpretation_settings()


def test_cache_key_changes_with_evidence_and_rank_context() -> None:
    base = {
        "workspace_id": "workspace",
        "payment": {"id": "payment", "version": 1},
        "invoices": [{"id": "i", "version": 1}],
        "credits": [],
        "source_spans": [{"source_id": "s", "sha256": "a", "text": "invoice i"}],
        "candidates": [{"candidate_id": "c"}],
        "ranked_candidates": [],
        "model": "gpt-6-luna",
        "prompt_version": "v1",
        "schema_version": "v1",
        "budget_policy_version": "v1",
        "reasoning_effort": "none",
        "max_output_tokens": 2048,
    }
    changed_evidence = {**base, "source_spans": [{"source_id": "s", "sha256": "b"}]}
    changed_rank = {**base, "ranked_candidates": [{"candidate_id": "c", "score": 1.0}]}

    assert cache_key(base) != cache_key(changed_evidence)
    assert cache_key(base) != cache_key(changed_rank)
    assert source_fingerprint(base) != source_fingerprint(changed_evidence)
