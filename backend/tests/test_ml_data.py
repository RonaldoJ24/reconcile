from __future__ import annotations

import hashlib
import json
from itertools import combinations
from pathlib import Path
from typing import Any

import pytest

from reconcile.ml.data import (
    CHALLENGE_COUNTS,
    DATASET_VERSION,
    INPUT_KEYS,
    LABEL_PROVENANCE,
    SCENARIO_FAMILIES,
    SEED,
    SPLIT_COUNTS,
    TARGET_KEYS,
    TEMPLATE_FAMILIES,
    generate_dataset,
    sha256_file,
    validate_dataset,
)


def _rows(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


@pytest.fixture(scope="module")
def generated(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, dict[str, Any]]:
    root = tmp_path_factory.mktemp("ml-v1")
    return root, generate_dataset(root)


def _path(root: Path, split: str, kind: str) -> Path:
    if split in CHALLENGE_COUNTS:
        return root / "challenge" / kind / f"{split}.jsonl"
    return root / kind / f"{split}.jsonl"


def test_exact_counts_and_versions(generated: tuple[Path, dict[str, Any]]) -> None:
    root, manifest = generated
    assert manifest["dataset_version"] == DATASET_VERSION
    assert manifest["seed"] == SEED
    for split, expected in {**SPLIT_COUNTS, **CHALLENGE_COUNTS}.items():
        assert len(_rows(_path(root, split, "inputs"))) == expected
        assert len(_rows(_path(root, split, "targets"))) == expected
        assert manifest["splits"][split]["groups"] == expected


def test_regeneration_is_byte_for_byte_deterministic(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    generate_dataset(first)
    generate_dataset(second)
    for path in sorted(first.rglob("*")):
        if path.is_file():
            assert path.read_bytes() == (second / path.relative_to(first)).read_bytes()


def test_input_target_separation_and_candidate_bounds(
    generated: tuple[Path, dict[str, Any]],
) -> None:
    root, _manifest = generated
    forbidden = TARGET_KEYS - {"group_id"}
    for split in {**SPLIT_COUNTS, **CHALLENGE_COUNTS}:
        inputs = _rows(_path(root, split, "inputs"))
        targets = _rows(_path(root, split, "targets"))
        target_by_group = {target["group_id"]: target for target in targets}
        assert set(target_by_group) == {row["group_id"] for row in inputs}
        for row in inputs:
            assert set(row) == INPUT_KEYS
            assert not forbidden.intersection(row)
            invoice_ids = {invoice["invoice_id"] for invoice in row["invoices"]}
            assert 1 <= len(row["invoices"]) <= 10
            if row["credit"] is not None:
                assert set(row["credit"]).issubset(
                    {
                        "customer_id",
                        "credit_note_id",
                        "balance_as_of",
                        "available_amount",
                        "currency",
                        "invoice_id",
                    }
                )
                assert {
                    "customer_id",
                    "credit_note_id",
                    "balance_as_of",
                    "available_amount",
                    "currency",
                }.issubset(row["credit"])
            candidate_ids = set()
            for candidate in row["candidates"]:
                assert set(candidate) == {"candidate_id", "invoice_ids", "cash", "credits"}
                candidate_ids.add(candidate["candidate_id"])
                assert 1 <= len(candidate["invoice_ids"]) <= 3
                assert set(candidate["invoice_ids"]).issubset(invoice_ids)
                for line in candidate["cash"]:
                    assert set(line) == {"invoice_id", "amount"}
                    assert line["invoice_id"] in candidate["invoice_ids"]
                    assert isinstance(line["amount"], int) and line["amount"] > 0
                for line in candidate["credits"]:
                    assert set(line) == {"credit_note_id", "invoice_id", "amount"}
                    assert line["invoice_id"] in candidate["invoice_ids"]
                    assert isinstance(line["amount"], int) and line["amount"] > 0
            target = target_by_group[row["group_id"]]
            assert set(target) == TARGET_KEYS
            assert set(target["acceptable_candidate_ids"]).issubset(candidate_ids)
            assert target["seed"] == SEED
            assert target["label_provenance"] == LABEL_PROVENANCE
            assert target["expected_status"] == (
                "PROPOSED" if target["acceptable_candidate_ids"] else "NEEDS_REVIEW"
            )


def test_disjoint_entities_and_held_out_templates(
    generated: tuple[Path, dict[str, Any]],
) -> None:
    root, manifest = generated
    entity_sets: dict[str, set[str]] = {}
    for split in SPLIT_COUNTS:
        rows = _rows(_path(root, split, "inputs"))
        entity_sets[split] = {
            invoice["customer_id"] for row in rows for invoice in row["invoices"]
        }
        targets = _rows(_path(root, split, "targets"))
        assert {target["scenario_family"] for target in targets} == set(SCENARIO_FAMILIES)
        assert set(manifest["splits"][split]["template_families"]).issubset(set(TEMPLATE_FAMILIES))
    for left, right in combinations(entity_sets.values(), 2):
        assert left.isdisjoint(right)
    for left, right in combinations(SPLIT_COUNTS, 2):
        assert set(manifest["splits"][left]["template_families"]).isdisjoint(
            manifest["splits"][right]["template_families"]
        )


def test_challenge_covers_all_families_and_has_underdetermined_cases(
    generated: tuple[Path, dict[str, Any]],
) -> None:
    root, _manifest = generated
    for split in CHALLENGE_COUNTS:
        targets = _rows(_path(root, split, "targets"))
        assert {target["scenario_family"] for target in targets} == set(SCENARIO_FAMILIES)
        assert any(target["expected_status"] == "NEEDS_REVIEW" for target in targets)


def test_manifest_hashes_match_files(generated: tuple[Path, dict[str, Any]]) -> None:
    root, manifest = generated
    assert validate_dataset(root)["sealed"] == manifest["sealed"]
    for spec in manifest["splits"].values():
        for kind in ("input", "target"):
            record = spec[kind]
            path = root / record["path"]
            assert record["sha256"] == sha256_file(path)
            assert hashlib.sha256(path.read_bytes()).hexdigest() == record["sha256"]
