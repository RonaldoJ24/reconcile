"""One-shot evaluation of the frozen synthetic release splits."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .artifact import load_artifact, sha256_file
from .evaluate import evaluate, evaluate_rules, guard_evaluation_split
from .train import read_jsonl

EXPECTED_FILES = {
    "inputs/final.jsonl": "c4a1150bd73ac85a2296cf3782ffbe15bb7604459ad7ffbccb2bd55166715bf9",
    "targets/final.jsonl": "25fc784e9b6ac539649c20a8758f604718b362f35cb7c04a590ffdca3fbcb0fb",
    "challenge/inputs/sealed-test.jsonl": (
        "04090ea2770f11b5effa6e77ddfdc2a483c5c37f0cae535275e43e0ea632d20a"
    ),
    "challenge/targets/sealed-test.jsonl": (
        "6d78c21fee65966717939fc1372aa8494a71fdac4f91bd22f14e70686f6a4c33"
    ),
}
EXPECTED_COUNTS = {"final": 500, "sealed-test": 20}


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(rendered, encoding="utf-8")
    temporary.replace(path)


def _group_ids(rows: list[dict[str, Any]]) -> set[str]:
    return {str(row.get("group_id", "")) for row in rows}


def _read_pair(
    root: Path,
    input_name: str,
    target_name: str,
    *,
    expected_count: int | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    groups = read_jsonl(root / input_name)
    targets = read_jsonl(root / target_name)
    if expected_count is not None and (
        len(groups) != expected_count or len(targets) != expected_count
    ):
        raise RuntimeError(f"sealed split count mismatch for {input_name}")
    if _group_ids(groups) != _group_ids(targets):
        raise RuntimeError(f"sealed input/target IDs differ for {input_name}")
    return groups, targets


def evaluate_release(
    *,
    root: Path,
    report_path: Path,
    access_path: Path,
    release_commit: str,
    expected_files: Mapping[str, str] | None = None,
    expected_counts: Mapping[str, int] | None = None,
) -> dict[str, Any]:
    """Evaluate frozen labels exactly once and leave a durable access record."""

    guard_evaluation_split("final")
    if not release_commit.strip():
        raise RuntimeError("release commit is required")
    if access_path.exists():
        raise RuntimeError(f"sealed evaluation already attempted; see {access_path}")

    expected = dict(expected_files or EXPECTED_FILES)
    counts = dict(expected_counts or EXPECTED_COUNTS)
    access: dict[str, Any] = {
        "schema_version": "release-access-v1",
        "status": "started",
        "started_at": _now(),
        "release_commit": release_commit,
        "scope": "synthetic-agent-generated-not-domain-validated",
        "files": [
            {"path": name, "expected_sha256": digest, "access": "hash-and-evaluate"}
            for name, digest in expected.items()
        ],
    }
    _atomic_json(access_path, access)

    try:
        observed: list[dict[str, Any]] = []
        for name, digest in expected.items():
            path = root / name
            actual = sha256_file(path)
            if actual != digest:
                raise RuntimeError(f"frozen sealed digest mismatch for {name}")
            observed.append({"path": name, "sha256": actual, "bytes": path.stat().st_size})

        final_groups, final_targets = _read_pair(
            root,
            "inputs/final.jsonl",
            "targets/final.jsonl",
            expected_count=counts.get("final"),
        )
        challenge_groups, challenge_targets = _read_pair(
            root,
            "challenge/inputs/sealed-test.jsonl",
            "challenge/targets/sealed-test.jsonl",
            expected_count=counts.get("sealed-test"),
        )
        loaded = load_artifact()
        payload: dict[str, Any] = {
            "schema_version": "release-evaluation-v1",
            "scope": "synthetic-agent-generated-not-domain-validated",
            "release_commit": release_commit,
            "evaluated_at": _now(),
            "dataset": {
                "version": "ml-v1-2026-09-14",
                "generator_version": "ml-v1-data-1",
                "seed": 20260914,
                "sealed_files": observed,
            },
            "artifact": {
                "model_id": loaded.model_id,
                "model_version": loaded.model_version,
                "model_sha256": loaded.metadata["model_sha256"],
                "feature_schema": loaded.metadata["schema_version"],
                "promotion": "shadow",
            },
            "routing": {
                "runtime_authority": "rules-v1",
                "rules_evaluation_threshold": 0.5,
                "learned_ranker_threshold": None,
                "human_approval_required": True,
            },
            "methods": {
                "rules_final": evaluate_rules(final_groups, final_targets, split="final"),
                "ranker_final": evaluate(
                    loaded.model, final_groups, final_targets, split="final"
                ),
                "rules_sealed_challenge": evaluate_rules(
                    challenge_groups, challenge_targets, split="sealed-test"
                ),
                "ranker_sealed_challenge": evaluate(
                    loaded.model, challenge_groups, challenge_targets, split="sealed-test"
                ),
            },
            "provider": {
                "calls": 0,
                "cache_hits": 0,
                "latency_ms": None,
                "cost_usd": 0,
                "reason": "sealed release evaluation is model-free/provider-free",
            },
            "limitations": [
                "Labels and challenge semantics are agent-generated and not "
                "independently reviewed.",
                "Metrics are synthetic and are not real-world accuracy or human validation.",
                "The learned ranker remains shadow-only and cannot apply money.",
            ],
        }
        _atomic_json(report_path, payload)
        access.update(
            {
                "status": "completed",
                "completed_at": _now(),
                "observed_files": observed,
                "report_path": str(report_path),
                "report_sha256": hashlib.sha256(report_path.read_bytes()).hexdigest(),
            }
        )
        _atomic_json(access_path, access)
        return payload
    except Exception as exc:
        access.update(
            {
                "status": "failed",
                "failed_at": _now(),
                "failure_type": type(exc).__name__,
            }
        )
        _atomic_json(access_path, access)
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the one-shot synthetic release evaluation")
    parser.add_argument("--root", type=Path, default=Path("data/generated/ml-v1"))
    parser.add_argument("--report", type=Path, default=Path("reports/release-v1/evaluation.json"))
    parser.add_argument(
        "--access", type=Path, default=Path("reports/release-v1/sealed-access.json")
    )
    parser.add_argument("--release-commit", required=True)
    args = parser.parse_args()
    result = evaluate_release(
        root=args.root,
        report_path=args.report,
        access_path=args.access,
        release_commit=args.release_commit,
    )
    print(
        json.dumps(
            {
                "report": str(args.report),
                "release_commit": result["release_commit"],
                "sealed_groups": {
                    name: metrics["denominators"]["groups"]
                    for name, metrics in result["methods"].items()
                },
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
