"""Local shadow-mode ranking; deterministic rules remain the financial authority."""

from __future__ import annotations

import math
import os
from collections.abc import Mapping
from time import perf_counter_ns
from typing import Any

from .artifact import ArtifactError, LoadedArtifact, load_cached_artifact
from .features import candidate_features, score_classifier

ACTIVE_RULES_IDENTITY = "rules-v2-conservative"


def runtime_mode() -> str:
    mode = os.getenv("RECONCILE_RANKER_MODE", "rules-v1").strip().lower()
    if mode not in {"rules-v1", "shadow"}:
        return "rules-v1"
    return mode


def _candidate_rows(group: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    candidates = group.get("candidates", ())
    if not isinstance(candidates, list | tuple):
        return []
    return [candidate for candidate in candidates if isinstance(candidate, Mapping)]


def _raw_scores(model: Any, rows: list[tuple[float, ...]]) -> list[float]:
    return score_classifier(model, rows)


def rank_candidates(
    group: Mapping[str, Any],
    *,
    artifact: LoadedArtifact | None = None,
    max_candidates: int = 10,
    force: bool = False,
) -> dict[str, Any]:
    """Return an observational shadow trace and never mutate the proposal.

    The returned score is a raw classifier ranking score.  It is intentionally
    not named or reported as a probability.
    """

    candidates = _candidate_rows(group)
    truncated = len(candidates) > max_candidates
    candidates = candidates[:max_candidates]
    trace: dict[str, Any] = {
        "mode": runtime_mode(),
        "rules_identity": ACTIVE_RULES_IDENTITY,
        "model_id": None,
        "model_version": None,
        "ranked_candidate": None,
        "score": None,
        "ranked_candidates": [],
        "truncated": truncated,
        "truncation": truncated,
    }
    if (trace["mode"] != "shadow" and not force) or not candidates:
        return trace

    loaded = artifact or load_cached_artifact()
    started = perf_counter_ns()
    values = _raw_scores(
        loaded.model, [candidate_features(group, candidate) for candidate in candidates]
    )
    ranked = sorted(
        zip(candidates, values),
        key=lambda item: (
            -item[1] if math.isfinite(item[1]) else math.inf,
            str(item[0].get("candidate_id", "")),
        ),
    )
    trace.update(
        {
            "mode": "hybrid" if force and trace["mode"] != "shadow" else trace["mode"],
            "model_id": loaded.model_id,
            "model_version": loaded.model_version,
            "ranked_candidate": ranked[0][0].get("candidate_id"),
            "score": ranked[0][1],
            "ranked_candidates": [
                {"candidate_id": candidate.get("candidate_id"), "score": score}
                for candidate, score in ranked
            ],
            "latency_ms": (perf_counter_ns() - started) / 1_000_000,
        }
    )
    return trace


def shadow_rank(group: Mapping[str, Any], **kwargs: Any) -> dict[str, Any]:
    """Compatibility name for callers wiring a proposal trace."""

    return rank_candidates(group, **kwargs)


predict_shadow = rank_candidates


__all__ = [
    "ACTIVE_RULES_IDENTITY",
    "ArtifactError",
    "rank_candidates",
    "runtime_mode",
    "shadow_rank",
]
