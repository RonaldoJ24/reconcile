"""Shared candidate-ranking components for Reconcile ML v1."""

from typing import Any

from .artifact import (
    ARTIFACT_DIR,
    ARTIFACT_ROOT,
    ArtifactError,
    LoadedArtifact,
    load_artifact,
    save_artifact,
)
from .features import FEATURE_NAMES, FEATURE_SCHEMA, candidate_features, extract_features
from .runtime import rank_candidates

__all__ = [
    "ARTIFACT_DIR",
    "ARTIFACT_ROOT",
    "ArtifactError",
    "FEATURE_NAMES",
    "FEATURE_SCHEMA",
    "LoadedArtifact",
    "candidate_features",
    "extract_features",
    "guard_evaluation_split",
    "load_artifact",
    "rank_candidates",
    "save_artifact",
]


def __getattr__(name: str) -> Any:
    if name in {"evaluate_development", "evaluate_rules", "guard_evaluation_split"}:
        from . import evaluate

        return getattr(evaluate, name)
    raise AttributeError(name)
