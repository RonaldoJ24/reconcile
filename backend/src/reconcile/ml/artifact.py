"""Versioned, integrity-checked local model artifact handling."""

from __future__ import annotations

import hashlib
import json
import pickle
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .features import FEATURE_NAMES, FEATURE_SCHEMA_VERSION

ARTIFACT_DIR = Path(__file__).resolve().parents[4] / "artifacts" / "ranker-ml-v1"
ARTIFACT_ROOT = ARTIFACT_DIR
MODEL_FILENAME = "model.pkl"
METADATA_FILENAME = "metadata.json"


class ArtifactError(RuntimeError):
    """Raised when a model artifact is absent, outside the allowlist, or altered."""


@dataclass(frozen=True, slots=True)
class LoadedArtifact:
    model: Any
    metadata: dict[str, Any]

    @property
    def model_id(self) -> str:
        return str(self.metadata.get("model_id", "unknown"))

    @property
    def model_version(self) -> str:
        return str(self.metadata.get("model_version", "unknown"))

    def __iter__(self) -> Iterator[Any]:
        # Convenient for callers that naturally unpack (model, metadata).
        yield self.model
        yield self.metadata


def artifact_paths(artifact_dir: Path | None = None) -> tuple[Path, Path]:
    root = artifact_dir or ARTIFACT_DIR
    return root / MODEL_FILENAME, root / METADATA_FILENAME


def _fixed_root() -> Path:
    return ARTIFACT_DIR.resolve()


def _assert_fixed_target(path: Path) -> Path:
    expected = _fixed_root()
    resolved = path.resolve()
    if resolved != expected and resolved.parent != expected:
        raise ArtifactError("model artifacts must remain below the fixed internal artifact path")
    return resolved


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _metadata_valid(metadata: dict[str, Any]) -> None:
    if metadata.get("schema_version") != FEATURE_SCHEMA_VERSION:
        raise ArtifactError("unsupported model feature schema")
    if tuple(metadata.get("feature_names", ())) != FEATURE_NAMES:
        raise ArtifactError("artifact feature schema does not match runtime")
    digest = metadata.get("model_sha256")
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ArtifactError("artifact metadata has no valid model digest")
    if not metadata.get("model_id") or not metadata.get("model_version"):
        raise ArtifactError("artifact metadata must identify the model and version")


def _assert_internal_file(path: Path) -> None:
    if path.resolve().parent != _fixed_root():
        raise ArtifactError("artifact files must not be symlinks outside the fixed path")


def save_artifact(
    model: Any,
    metadata: dict[str, Any],
    *,
    artifact_dir: Path | None = None,
) -> Path:
    """Save a model only below the fixed internal artifact directory."""

    root = (artifact_dir or ARTIFACT_DIR).resolve()
    if root != _fixed_root():
        raise ArtifactError("model artifacts may only be written below the fixed internal path")
    model_path, metadata_path = artifact_paths(root)
    payload = pickle.dumps(model, protocol=pickle.HIGHEST_PROTOCOL)
    complete = dict(metadata)
    complete.setdefault("schema_version", FEATURE_SCHEMA_VERSION)
    complete.setdefault("feature_names", list(FEATURE_NAMES))
    complete.setdefault("seed", 20260914)
    complete.setdefault("dataset_hashes", {})
    complete.setdefault("dependency_versions", {})
    complete.setdefault("data_hashes", complete["dataset_hashes"])
    complete.setdefault("dependencies", complete["dependency_versions"])
    complete.setdefault("metrics", {})
    complete.setdefault("decision", {})
    complete["model_sha256"] = _sha256_bytes(payload)
    _metadata_valid(complete)

    root.mkdir(parents=True, exist_ok=True)
    temp_model = model_path.with_suffix(".tmp")
    temp_metadata = metadata_path.with_suffix(".tmp")
    temp_model.write_bytes(payload)
    temp_metadata.write_text(
        json.dumps(complete, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    temp_model.replace(model_path)
    temp_metadata.replace(metadata_path)
    return model_path


def load_artifact(path: Path | str | None = None) -> LoadedArtifact:
    """Load and verify the built-in artifact; never deserialize an arbitrary path."""

    if path is not None:
        requested = Path(path).resolve()
        expected_model, expected_metadata = artifact_paths(_fixed_root())
        if requested not in {expected_model.resolve(), expected_metadata.resolve(), _fixed_root()}:
            raise ArtifactError("runtime model path is not allowlisted")
    model_path, metadata_path = artifact_paths(_fixed_root())
    if not model_path.is_file() or not metadata_path.is_file():
        raise ArtifactError("verified ranker artifact is not installed")
    _assert_internal_file(model_path)
    _assert_internal_file(metadata_path)
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ArtifactError("artifact metadata cannot be read") from exc
    if not isinstance(metadata, dict):
        raise ArtifactError("artifact metadata must be an object")
    _metadata_valid(metadata)
    actual = sha256_file(model_path)
    if actual != metadata["model_sha256"]:
        raise ArtifactError("model digest verification failed")
    try:
        model = pickle.loads(model_path.read_bytes())
    except (OSError, pickle.PickleError, EOFError, ImportError, AttributeError) as exc:
        raise ArtifactError("verified model could not be deserialized") from exc
    return LoadedArtifact(model, metadata)


save_model_artifact = save_artifact
load_verified_artifact = load_artifact
