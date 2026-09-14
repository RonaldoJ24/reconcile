from __future__ import annotations

import json
from pathlib import Path

import pytest

from reconcile.ml.artifact import sha256_file
from reconcile.ml.release import evaluate_release
from reconcile.release_checks import scan_release


def _group(group_id: str) -> dict[str, object]:
    return {
        "group_id": group_id,
        "payment": {
            "booking_date": "2026-01-15",
            "payer_name": "Customer",
            "reference": "INV-1",
            "amount": 10_000,
            "customer_id": "C-1",
        },
        "invoices": [
            {
                "customer_id": "C-1",
                "customer_name": "Customer",
                "invoice_id": "INV-1",
                "issued_date": "2026-01-01",
                "due_date": "2026-01-15",
                "balance_as_of": "2026-01-15",
                "outstanding_amount": 10_000,
            }
        ],
        "credit": None,
        "message": "Apply to INV-1",
        "candidates": [
            {
                "candidate_id": "candidate-1",
                "invoice_ids": ["INV-1"],
                "cash": [{"invoice_id": "INV-1", "amount": 10_000}],
                "credits": [],
            }
        ],
    }


def _target(group_id: str) -> dict[str, object]:
    return {
        "group_id": group_id,
        "acceptable_candidate_ids": ["candidate-1"],
        "scenario_family": "exact_reference",
    }


def _write_jsonl(path: Path, row: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(row, sort_keys=True) + "\n", encoding="utf-8")


def test_release_evaluation_is_guarded_and_one_shot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "data"
    names = {
        "inputs/final.jsonl": (_group("final-1"), _target("final-1")),
        "challenge/inputs/sealed-test.jsonl": (
            _group("challenge-1"),
            _target("challenge-1"),
        ),
    }
    for input_name, (group, target) in names.items():
        _write_jsonl(root / input_name, group)
        _write_jsonl(root / input_name.replace("inputs/", "targets/"), target)
    expected = {
        name: sha256_file(path)
        for name in (
            "inputs/final.jsonl",
            "targets/final.jsonl",
            "challenge/inputs/sealed-test.jsonl",
            "challenge/targets/sealed-test.jsonl",
        )
        if (path := root / name).is_file()
    }
    report = tmp_path / "evaluation.json"
    access = tmp_path / "access.json"

    monkeypatch.delenv("ALLOW_SEALED_EVAL", raising=False)
    with pytest.raises(RuntimeError, match="ALLOW_SEALED_EVAL"):
        evaluate_release(
            root=root,
            report_path=report,
            access_path=access,
            release_commit="abc123",
            expected_files=expected,
            expected_counts={"final": 1, "sealed-test": 1},
        )
    assert not access.exists()

    monkeypatch.setenv("ALLOW_SEALED_EVAL", "1")
    payload = evaluate_release(
        root=root,
        report_path=report,
        access_path=access,
        release_commit="abc123",
        expected_files=expected,
        expected_counts={"final": 1, "sealed-test": 1},
    )
    assert payload["methods"]["rules_final"]["denominators"]["groups"] == 1
    assert json.loads(access.read_text())["status"] == "completed"
    with pytest.raises(RuntimeError, match="already attempted"):
        evaluate_release(
            root=root,
            report_path=report,
            access_path=access,
            release_commit="abc123",
            expected_files=expected,
            expected_counts={"final": 1, "sealed-test": 1},
        )


def test_release_scan_verifies_tracked_sources_and_artifact() -> None:
    report = scan_release(Path(__file__).resolve().parents[2])
    assert report["passed"] is True
    assert report["secret_findings"] == []
    assert report["artifact"]["verified"] is True
