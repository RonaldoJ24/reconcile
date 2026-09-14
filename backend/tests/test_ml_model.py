from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from reconcile.ml import artifact as artifact_module
from reconcile.ml.artifact import ArtifactError, load_artifact, save_artifact
from reconcile.ml.evaluate import evaluate_rules, evaluate_scores, model_scores, rules_scores
from reconcile.ml.features import (
    FEATURE_NAMES,
    candidate_features,
    extract_features,
    score_classifier,
)
from reconcile.ml.runtime import rank_candidates
from reconcile.ml.train import train_models


def _group(group_id: str = "g-1") -> dict:
    return {
        "group_id": group_id,
        "payment": {
            "booking_date": "2026-01-15",
            "payer_name": "Acme, S.A.",
            "reference": "Payment INV-A and INV-B",
            "amount": 100,
            "currency": "MXN",
            "customer_id": "cust-1",
        },
        "invoices": [
            {
                "customer_id": "cust-1",
                "customer_name": "Acme SA",
                "invoice_id": "INV-A",
                "issued_date": "2025-12-01",
                "due_date": "2026-01-01",
                "balance_as_of": "2026-01-15",
                "outstanding_amount": 60,
                "currency": "MXN",
            },
            {
                "customer_id": "cust-1",
                "customer_name": "Acme SA",
                "invoice_id": "INV-B",
                "issued_date": "2025-12-01",
                "due_date": "2026-01-10",
                "balance_as_of": "2026-01-15",
                "outstanding_amount": 40,
                "currency": "MXN",
            },
        ],
        "credit": None,
        "message": "Please apply INV-A and INV-B.",
        "candidates": [
            {
                "candidate_id": "c-a",
                "invoice_ids": ["INV-A"],
                "cash": [{"invoice_id": "INV-A", "amount": 100}],
                "credits": [],
            },
            {
                "candidate_id": "c-b",
                "invoice_ids": ["INV-A", "INV-B"],
                "cash": [
                    {"invoice_id": "INV-A", "amount": 60},
                    {"invoice_id": "INV-B", "amount": 40},
                ],
                "credits": [],
            },
        ],
    }


def _targets(groups: list[dict]) -> list[dict]:
    return [
        {
            "group_id": group["group_id"],
            "acceptable_candidate_ids": ["c-b"],
            "status": "PROPOSED",
            "family": "explicit_group",
        }
        for group in groups
    ]


def test_features_are_order_id_and_irrelevant_text_invariant() -> None:
    original = _group()
    changed = deepcopy(original)
    changed["payment"]["reference"] = "Completely different wording: X-1 and Y-2"
    changed["message"] = "X-1 and Y-2 are the payment destination; thanks."
    changed["invoices"][0]["invoice_id"] = "X-1"
    changed["invoices"][1]["invoice_id"] = "Y-2"
    changed["candidates"][0]["invoice_ids"] = ["X-1"]
    changed["candidates"][1]["invoice_ids"] = ["X-1", "Y-2"]
    changed["candidates"] = list(reversed(changed["candidates"]))
    original["message"] += " Irrelevant words should not affect numeric evidence."

    left = sorted(
        (sum(line["amount"] for line in candidate["cash"]), candidate_features(original, candidate))
        for candidate in original["candidates"]
    )
    right = sorted(
        (sum(line["amount"] for line in candidate["cash"]), candidate_features(changed, candidate))
        for candidate in changed["candidates"]
    )
    assert [row[1] for row in left] == [row[1] for row in right]
    assert len(FEATURE_NAMES) == 10


def test_offline_online_feature_parity() -> None:
    group = _group()
    assert extract_features(group) == [
        list(candidate_features(group, candidate)) for candidate in group["candidates"]
    ]


def test_train_fits_exactly_two_bounded_candidates() -> None:
    train_groups = [_group(f"train-{index}") for index in range(4)]
    validation_groups = [_group("validation-1")]
    result = train_models(
        train_groups, _targets(train_groups), validation_groups, _targets(validation_groups)
    )
    assert set(result.models) == {"logistic", "hist_gradient_boosting"}
    assert result.selected_model in result.models
    assert result.calibration_metrics is None


def test_tree_uses_continuous_decision_function_and_probability_fallback() -> None:
    train_groups = [_group(f"train-{index}") for index in range(4)]
    result = train_models(
        train_groups, _targets(train_groups), train_groups, _targets(train_groups)
    )
    tree = result.models["hist_gradient_boosting"]
    rows = [
        candidate_features(train_groups[0], candidate)
        for candidate in train_groups[0]["candidates"]
    ]
    assert model_scores(tree, [train_groups[0]])["train-0"] == [
        float(value) for value in tree.decision_function(rows)
    ]

    class ProbabilityOnly:
        def predict_proba(self, values: list[tuple[float, ...]]) -> list[list[float]]:
            return [[0.25, 0.75] for _ in values]

    assert score_classifier(ProbabilityOnly(), rows) == [0.75, 0.75]


def test_save_load_equivalence_tamper_rejection_and_target_guard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    train_groups = [_group(f"train-{index}") for index in range(4)]
    result = train_models(
        train_groups, _targets(train_groups), train_groups, _targets(train_groups)
    )
    root = tmp_path / "ranker-ml-v1"
    monkeypatch.setattr(artifact_module, "ARTIFACT_DIR", root)
    save_artifact(
        result.models[result.selected_model],
        {"model_id": "toy-ranker", "model_version": "test", "seed": 20260914},
    )
    loaded = load_artifact()
    from reconcile.ml.evaluate import model_scores

    assert model_scores(loaded.model, train_groups) == model_scores(
        result.models[result.selected_model], train_groups
    )
    with pytest.raises(ArtifactError):
        load_artifact(tmp_path / "other.pkl")
    model_path = root / "model.pkl"
    model_path.write_bytes(model_path.read_bytes() + b"tampered")
    with pytest.raises(ArtifactError, match="digest"):
        load_artifact()


def test_sealed_guard_and_observed_runtime_prediction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    group = _group()
    with pytest.raises(RuntimeError, match="ALLOW_SEALED_EVAL"):
        evaluate_scores(
            [group], _targets([group]), {group["group_id"]: [0.0, 1.0]}, split="final-test"
        )

    train_groups = [_group(f"train-{index}") for index in range(4)]
    result = train_models(
        train_groups, _targets(train_groups), train_groups, _targets(train_groups)
    )
    monkeypatch.setattr(artifact_module, "ARTIFACT_DIR", tmp_path / "ranker-ml-v1")
    save_artifact(
        result.models[result.selected_model],
        {"model_id": "toy-ranker", "model_version": "test", "seed": 20260914},
    )
    monkeypatch.setenv("RECONCILE_RANKER_MODE", "shadow")
    trace = rank_candidates(group)
    assert trace["model_id"] == "toy-ranker"
    assert trace["model_version"] == "test"
    assert trace["ranked_candidate"] in {"c-a", "c-b"}
    assert isinstance(trace["score"], float)


def test_runtime_allowlist_excludes_training_and_generated_data() -> None:
    allowlist = Path(__file__).parents[2] / "deploy/runtime-allowlist.txt"
    entries = {
        line.strip()
        for line in allowlist.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    assert "frontend/dist/" in entries
    assert "backend/alembic/" in entries
    assert "artifacts/ranker-ml-v1/model.pkl" in entries
    assert "artifacts/ranker-ml-v1/metadata.json" in entries
    assert not any(
        path in entries
        for path in {
            "backend/src/reconcile/ml/data.py",
            "backend/src/reconcile/ml/train.py",
            "backend/src/reconcile/ml/evaluate.py",
        }
    )
    assert not any(path.startswith("data/") for path in entries)


def test_rules_unmapped_and_needs_review_cases_abstain() -> None:
    group = _group()
    group["payment"]["reference"] = "No explicit match"
    group["message"] = "Please review this payment."
    targets = _targets([group])
    scores = rules_scores([group])
    assert scores[group["group_id"]] == []
    report = evaluate_rules([group], targets)
    assert report["proposal"]["proposals"] == 0

    group = _group()
    for candidate in group["candidates"]:
        for line in candidate["cash"]:
            line["invoice_id"] = "not-the-enumerated-invoice"
    scores = rules_scores([group])
    assert 0.0 in scores[group["group_id"]]
    report = evaluate_rules([group], _targets([group]))
    assert report["proposal"]["proposals"] == 0
