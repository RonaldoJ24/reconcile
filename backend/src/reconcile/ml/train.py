"""Bounded, reproducible two-model training for ML v1 candidate ranking."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from .artifact import save_artifact
from .evaluate import evaluate
from .features import FEATURE_NAMES, FEATURE_SCHEMA_VERSION, candidate_features

TRAINING_SEED = 20260914


@dataclass(frozen=True, slots=True)
class TrainingResult:
    models: dict[str, Any]
    validation_metrics: dict[str, dict[str, Any]]
    calibration_metrics: dict[str, Any] | None
    selected_model: str
    artifact_path: Path | None


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSONL at {path}:{line_number}") from exc
            if not isinstance(value, dict):
                raise ValueError(f"JSONL row at {path}:{line_number} must be an object")
            rows.append(value)
    return rows


load_jsonl = read_jsonl


def _target_map(targets: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(row["group_id"]): row for row in targets if row.get("group_id") is not None}


def _training_rows(
    groups: list[dict[str, Any]],
    targets: list[dict[str, Any]],
) -> tuple[list[tuple[float, ...]], list[int], list[float]]:
    target_by_group = _target_map(targets)
    features: list[tuple[float, ...]] = []
    labels: list[int] = []
    weights: list[float] = []
    for group in groups:
        candidates = group.get("candidates", ())
        if not isinstance(candidates, list | tuple) or not candidates:
            continue
        target = target_by_group.get(str(group.get("group_id", "")), {})
        accepted = target.get("acceptable_candidate_ids", ())
        accepted_ids = (
            {str(value) for value in accepted} if isinstance(accepted, list | tuple) else set()
        )
        weight = 1.0 / len(candidates)
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            features.append(candidate_features(group, candidate))
            labels.append(int(str(candidate.get("candidate_id", "")) in accepted_ids))
            weights.append(weight)
    if not features:
        raise ValueError("no candidate rows available for training")
    if len(set(labels)) < 2:
        raise ValueError("training data must contain positive and negative candidates")
    return features, labels, weights


def _models() -> dict[str, Pipeline[Any]]:
    return {
        "logistic": Pipeline(
            [
                ("scale", StandardScaler()),
                (
                    "classifier",
                    LogisticRegression(
                        C=1.0,
                        max_iter=500,
                        random_state=TRAINING_SEED,
                        solver="lbfgs",
                        n_jobs=1,
                    ),
                ),
            ]
        ),
        "hist_gradient_boosting": Pipeline(
            [
                ("scale", StandardScaler()),
                (
                    "classifier",
                    HistGradientBoostingClassifier(
                        learning_rate=0.08,
                        max_iter=100,
                        max_leaf_nodes=15,
                        l2_regularization=1.0,
                        random_state=TRAINING_SEED,
                    ),
                ),
            ]
        ),
    }


def _selection_key(metrics: dict[str, Any]) -> tuple[float, float, float]:
    ranking = metrics.get("ranking", {})
    proposal = metrics.get("proposal", {})

    def value(container: dict[str, Any], key: str) -> float:
        value = container.get(key)
        return float(value) if isinstance(value, (int, float)) else -1.0

    return (
        value(ranking, "top1_accuracy"),
        value(proposal, "precision"),
        value(proposal, "coverage"),
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _dependency_versions() -> dict[str, str]:
    versions: dict[str, str] = {}
    for name in ("scikit-learn", "numpy", "scipy"):
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = "unavailable"
    return versions


def train_models(
    train_groups: list[dict[str, Any]],
    train_targets: list[dict[str, Any]],
    validation_groups: list[dict[str, Any]],
    validation_targets: list[dict[str, Any]],
    *,
    calibration_groups: list[dict[str, Any]] | None = None,
    calibration_targets: list[dict[str, Any]] | None = None,
    dataset_hashes: dict[str, str] | None = None,
    write_artifact: bool = False,
) -> TrainingResult:
    """Fit exactly the logistic baseline and bounded tree challenger."""

    train_x, train_y, weights = _training_rows(train_groups, train_targets)
    fitted: dict[str, Any] = {}
    metrics: dict[str, dict[str, Any]] = {}
    for name, model in _models().items():
        model.fit(train_x, train_y, classifier__sample_weight=weights)
        fitted[name] = model
        metrics[name] = evaluate(model, validation_groups, validation_targets)
    selected_name = max(
        metrics, key=lambda name: (_selection_key(metrics[name]), name == "logistic")
    )

    calibration_metrics = None
    if calibration_groups is not None and calibration_targets is not None:
        calibration_metrics = evaluate(
            fitted[selected_name], calibration_groups, calibration_targets
        )

    artifact_path: Path | None = None
    if write_artifact:
        metadata = {
            "schema_version": FEATURE_SCHEMA_VERSION,
            "feature_names": list(FEATURE_NAMES),
            "seed": TRAINING_SEED,
            "model_id": f"ranker-ml-v1-{selected_name}",
            "model_version": "1",
            "dataset_hashes": dataset_hashes or {},
            "dependency_versions": _dependency_versions(),
            "metrics": {
                "validation": metrics,
                "calibration": calibration_metrics,
            },
            "decision": {
                "selected_model": selected_name,
                "promotion_decision": "shadow",
                "reason": (
                    "ML promotion requires a documented baseline improvement and remains "
                    "shadow by default"
                ),
            },
        }
        metadata["data_hashes"] = metadata["dataset_hashes"]
        metadata["dependencies"] = metadata["dependency_versions"]
        artifact_path = save_artifact(fitted[selected_name], metadata)
    return TrainingResult(fitted, metrics, calibration_metrics, selected_name, artifact_path)


def train_from_directory(
    root: Path = Path("data/generated/ml-v1"),
    *,
    write_artifact: bool = True,
) -> TrainingResult:
    """Train from the generator's split files once the data lane is present."""

    input_root = root / "inputs"
    target_root = root / "targets"
    loaded: dict[str, tuple[list[dict[str, Any]], list[dict[str, Any]]]] = {}
    hashes: dict[str, str] = {}
    for split in ("train", "validation", "calibration"):
        input_path = input_root / f"{split}.jsonl"
        target_path = target_root / f"{split}.jsonl"
        if not input_path.is_file() or not target_path.is_file():
            if split == "calibration":
                continue
            raise FileNotFoundError(f"missing ML v1 {split} split")
        loaded[split] = (read_jsonl(input_path), read_jsonl(target_path))
        hashes[f"inputs/{split}"] = _sha256(input_path)
        hashes[f"targets/{split}"] = _sha256(target_path)
    calibration = loaded.get("calibration", (None, None))
    return train_models(
        *loaded["train"],
        *loaded["validation"],
        calibration_groups=calibration[0],
        calibration_targets=calibration[1],
        dataset_hashes=hashes,
        write_artifact=write_artifact,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Train the two ML v1 ranker candidates")
    parser.add_argument("--root", type=Path, default=Path("data/generated/ml-v1"))
    parser.add_argument("--no-artifact", action="store_true")
    args = parser.parse_args()
    result = train_from_directory(args.root, write_artifact=not args.no_artifact)
    print(
        json.dumps(
            {
                "selected_model": result.selected_model,
                "validation": result.validation_metrics,
                "calibration": result.calibration_metrics,
                "artifact": str(result.artifact_path) if result.artifact_path else None,
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
