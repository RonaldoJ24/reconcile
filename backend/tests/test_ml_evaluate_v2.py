from __future__ import annotations

import json
from pathlib import Path

import pytest

from reconcile.ml import evaluate_v2
from reconcile.ml.evaluate_v2 import (
    EvaluationValidationError,
    FinalAccessError,
    evaluate_observations,
    evaluate_ranker_thresholds,
    fingerprint_value,
    freeze_manifest,
    freeze_selection_artifact,
    read_split,
    stable_split,
    validate_join,
    validate_lineage,
    validate_observation_parity,
    verify_selection_artifact,
)


def _group_id(split: str, start: int = 0) -> str:
    for index in range(start, start + 100_000):
        value = f"group-{index}"
        if stable_split(value) == split:
            return value
    raise AssertionError(f"could not find a {split} group")


def _case(case_id: str, group_id: str, *, public_exposed: bool = False) -> dict:
    return {
        "case_id": case_id,
        "group_id": group_id,
        "split": stable_split(group_id),
        "public_exposed": public_exposed,
        "lineage": {
            "group_id": group_id,
            "entity_ids": [f"entity-{group_id}"],
            "variant_ids": [f"variant-{case_id}"],
        },
        "payment": {"amount": 10_000, "currency": "MXN"},
        "evidence": [{"source_id": f"source-{case_id}", "start": 0, "end": 12}],
        "candidates": [
            {
                "candidate_id": "candidate-good",
                "invoice_ids": ["invoice-1"],
                "cash": [{"invoice_id": "invoice-1", "amount": 6_000}],
                "credits": [
                    {
                        "credit_note_id": "credit-1",
                        "invoice_id": "invoice-1",
                        "amount": 4_000,
                    }
                ],
            },
            {
                "candidate_id": "candidate-other",
                "invoice_ids": ["invoice-2"],
                "cash": [{"invoice_id": "invoice-2", "amount": 10_000}],
                "credits": [],
            },
        ],
    }


def _label(case: dict, *, answerable: bool = True, semantic_support: str = "supported") -> dict:
    return {
        "case_id": case["case_id"],
        "group_id": case["group_id"],
        "split": case["split"],
        "answerable": answerable,
        "acceptable_candidate_ids": ["candidate-good"] if answerable else [],
        "expected_allocation": case["candidates"][0] if answerable else None,
        "semantic_support": semantic_support,
        "citation_location_valid": True if answerable else None,
    }


def _result(case: dict, *, status: str = "PROPOSED", method: str = "rules") -> dict:
    row = {
        "case_id": case["case_id"],
        "group_id": case["group_id"],
        "split": case["split"],
        "method": method,
        "status": status,
        "input_fingerprint": fingerprint_value(case),
        "candidate_fingerprint": fingerprint_value(case["candidates"]),
        "evidence_fingerprint": fingerprint_value(case["evidence"]),
        "validator_identity": "validator-v2-toy",
        "duration_ms": 1.25,
        "timing_scope": "features+predict",
        "citation_location_valid": True if status in {"PROPOSED", "OBSERVED"} else None,
    }
    if status == "PROPOSED":
        row.update(
            {
                "candidate_id": "candidate-good",
                "allocation": case["candidates"][0],
            }
        )
    return row


def test_split_is_stable_and_lineage_rejects_cross_group_relationships() -> None:
    group = _group_id("development")
    assert stable_split(group) == "development"
    first = _case("case-1", group)
    second = _case("case-2", _group_id("validation"))
    second["lineage"]["entity_ids"] = first["lineage"]["entity_ids"]
    with pytest.raises(EvaluationValidationError, match="crosses groups"):
        validate_lineage([first, second])

    parent = _case("parent-case", group)
    child = _case("child-case", _group_id("validation", 50))
    child["lineage"]["parent_ids"] = [parent["case_id"]]
    with pytest.raises(EvaluationValidationError, match="case reference"):
        validate_lineage([parent, child])


def test_join_rejects_duplicate_and_missing_rows_and_mixed_splits() -> None:
    development = _case("dev-1", _group_id("development"))
    validation = _case("val-1", _group_id("validation"))
    with pytest.raises(EvaluationValidationError, match="duplicate label"):
        validate_join(
            [development],
            [_result(development), _result(development)],
            [_label(development), _label(development)],
            split="development",
            method="rules",
        )
    with pytest.raises(EvaluationValidationError, match="incomplete"):
        validate_join(
            [development],
            [],
            [_label(development)],
            split="development",
            method="rules",
        )
    with pytest.raises(EvaluationValidationError, match="belongs to validation"):
        validate_join(
            [development, validation],
            [_result(development, method="rules"), _result(validation, method="rules")],
            [_label(development), _label(validation)],
            split="development",
            method="rules",
        )


def test_manifest_binds_input_and_target_hashes_and_exposed_data_is_development_only() -> None:
    development = _case("dev-1", _group_id("development"), public_exposed=True)
    target = _label(development)
    manifest = freeze_manifest([development], [target], split="development", public_exposed=True)
    assert manifest["input_sha256"] == evaluate_v2.fingerprint_rows([development])
    assert manifest["target_sha256"] == evaluate_v2.fingerprint_rows([target])
    with pytest.raises(EvaluationValidationError, match="may only be in development"):
        freeze_manifest([development], [target], split="validation", public_exposed=True)


def test_reserved_final_guard_runs_before_opening_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = {
        "protocol": "reconcile-eval-v2",
        "split": "reserved-final",
        "public_exposed": False,
        "input_count": 0,
        "target_count": 0,
        "input_sha256": "b" * 64,
        "target_sha256": "c" * 64,
        "manifest_sha256": "a" * 64,
    }
    manifest["manifest_sha256"] = fingerprint_value(
        {key: value for key, value in manifest.items() if key != "manifest_sha256"}
    )
    input_path = tmp_path / "inputs.jsonl"
    target_path = tmp_path / "targets.jsonl"
    input_path.write_text('{"should_not": "be opened"}\n', encoding="utf-8")
    target_path.write_text('{"should_not": "be opened"}\n', encoding="utf-8")
    opened = False

    def fail_open(*_args: object, **_kwargs: object) -> list[dict]:
        nonlocal opened
        opened = True
        raise AssertionError("reserved labels were opened before authorization")

    monkeypatch.setattr(evaluate_v2, "_read_jsonl", fail_open)
    with pytest.raises(FinalAccessError, match="explicit authorization"):
        read_split(input_path, target_path, split="reserved-final", manifest=manifest)
    assert opened is False

    with pytest.raises(FinalAccessError, match="manifest hash"):
        read_split(
            input_path,
            target_path,
            split="reserved-final",
            manifest=manifest,
            authorize_final=True,
            manifest_hash="b" * 64,
            ledger_path=tmp_path / "ledger",
        )
    assert not (tmp_path / "ledger").exists()

    forged = dict(manifest)
    forged["protocol"] = "reconcile-eval-v2-forged"
    with pytest.raises(EvaluationValidationError, match="manifest protocol"):
        read_split(input_path, target_path, split="reserved-final", manifest=forged)
    assert opened is False


def test_wrong_credit_id_is_not_an_exact_allocation() -> None:
    case = _case("case-credit", _group_id("validation"))
    label = _label(case)
    result = _result(case)
    result["allocation"] = {
        **case["candidates"][0],
        "credits": [{"credit_note_id": "credit-wrong", "invoice_id": "invoice-1", "amount": 4_000}],
    }
    report = evaluate_observations([case], [label], [result], split="validation", method="rules")
    assert report["proposal"] == {
        "proposals": 1,
        "correct": 0,
        "precision": 0.0,
        "all_case_coverage": 1.0,
    }


def test_label_cannot_name_a_missing_input_candidate() -> None:
    case = _case("case-missing-id", _group_id("validation", 5))
    label = _label(case)
    label["acceptable_candidate_ids"] = ["missing-candidate"]
    with pytest.raises(EvaluationValidationError, match="missing candidates"):
        evaluate_observations(
            [case], [_label(case) | label], [_result(case)], split="validation", method="rules"
        )


def test_label_target_shape_rejects_contradictory_semantic_support() -> None:
    case = _case("case-label-shape", _group_id("validation", 7))
    label = _label(case, semantic_support="unsupported")
    with pytest.raises(EvaluationValidationError, match="semantically unsupported"):
        evaluate_observations([case], [label], [_result(case)], split="validation", method="rules")


def test_accepted_candidate_with_altered_payload_is_incorrect_and_missing_label_fails() -> None:
    case = _case("case-payload", _group_id("validation", 10))
    label = _label(case)
    result = _result(case)
    result["allocation"] = {
        **case["candidates"][0],
        "cash": [{"invoice_id": "invoice-1", "amount": 5_000}],
        "credits": [{"credit_note_id": "credit-1", "invoice_id": "invoice-1", "amount": 5_000}],
    }
    report = evaluate_observations([case], [label], [result], split="validation", method="rules")
    assert report["support"]["unsupported"] == 1
    label.pop("expected_allocation")
    with pytest.raises(
        EvaluationValidationError, match="require an acceptable candidate or allocation"
    ):
        evaluate_observations([case], [label], [result], split="validation", method="rules")


def test_failure_and_unavailable_counts_keep_all_case_denominators() -> None:
    cases = [
        _case("case-error", _group_id("validation", 20)),
        _case("case-unavailable", _group_id("validation", 200)),
        _case("case-deferred", _group_id("validation", 400)),
    ]
    labels = [_label(c, answerable=False, semantic_support="unknown") for c in cases]
    results = [
        _result(c, status=status)
        for c, status in zip(cases, ("ERROR", "UNAVAILABLE", "DEFERRED"), strict=True)
    ]
    results[1]["duration_ms"] = None
    report = evaluate_observations(cases, labels, results, split="validation", method="rules")
    assert report["denominators"]["all_cases"] == 3
    assert report["outcomes"]["errors"] == 1
    assert report["outcomes"]["unavailable"] == 1
    assert report["deferral"]["correct"] == 1
    assert report["proposal"]["all_case_coverage"] == 0.0
    assert report["timing"] == {
        "measured": 2,
        "unknown": 1,
        "mean_ms": 1.25,
        "p95_ms": 1.25,
    }


def test_paired_observations_require_identical_fingerprints() -> None:
    case = _case("case-paired", _group_id("validation"))
    first = _result(case, method="rules")
    second = _result(case, method="correction")
    second["candidate_fingerprint"] = "0" * 64
    with pytest.raises(EvaluationValidationError, match="different inputs"):
        validate_observation_parity([first, second])


def test_unmaterialized_ranker_observations_are_rejected() -> None:
    case = _case("case-observed", _group_id("validation", 600))
    label = _label(case)
    result = _result(case, status="OBSERVED", method="ranker")
    result["ranked_candidates"] = [{"candidate_id": "candidate-good", "score": 0.5}]
    with pytest.raises(EvaluationValidationError, match="supported observation outcome"):
        evaluate_observations([case], [label], [result], split="validation", method="ranker")


def test_provider_proposals_are_rejected_until_an_authentic_loader_exists() -> None:
    case = _case("case-provider", _group_id("validation", 700))
    label = _label(case)
    proposed = _result(case, method="direct")
    with pytest.raises(EvaluationValidationError, match="authentic loader"):
        evaluate_observations([case], [label], [proposed], split="validation", method="direct")
    proposed.pop("method")
    with pytest.raises(EvaluationValidationError, match="authentic loader"):
        evaluate_observations([case], [label], [proposed], split="validation", method="direct")
    unavailable = _result(case, status="UNAVAILABLE", method="hybrid")
    unavailable["duration_ms"] = None
    report = evaluate_observations(
        [case], [label], [unavailable], split="validation", method="hybrid"
    )
    assert report["outcomes"]["unavailable"] == 1
    assert report["timing"]["unknown"] == 1


def test_ranker_selection_is_validation_only_and_ties_choose_higher_threshold() -> None:
    cases = [
        _case("case-ranker-1", _group_id("validation", 800)),
        _case("case-ranker-2", _group_id("validation", 1_000)),
    ]
    labels = [_label(case) for case in cases]
    results = []
    for case, score in zip(cases, (0.95, 0.80), strict=True):
        result = _result(case, status="OBSERVED", method="ranker")
        result["ranked_candidates"] = [
            {"candidate_id": "candidate-good", "score": score},
            {"candidate_id": "candidate-other", "score": 0.1},
        ]
        results.append(result)
    metrics = evaluate_ranker_thresholds(cases, labels, results)
    assert metrics["0.5"]["proposal"]["all_case_coverage"] == 1.0
    assert metrics["0.75"]["proposal"]["all_case_coverage"] == 1.0
    manifest = freeze_manifest(cases, labels, split="validation")
    artifact = freeze_selection_artifact(metrics, manifest=manifest)
    assert artifact["selected_threshold"] == 0.75
    verify_selection_artifact(artifact)
    tampered = json.loads(json.dumps(artifact))
    tampered["selected_threshold"] = 0.5
    with pytest.raises(EvaluationValidationError, match="hash mismatch"):
        verify_selection_artifact(tampered)
    with pytest.raises(EvaluationValidationError, match="validation-only"):
        evaluate_ranker_thresholds(cases, labels, results, split="development")
    development_manifest = freeze_manifest(
        [_case("case-dev", _group_id("development"))],
        [_label(_case("case-dev", _group_id("development")))],
        split="development",
    )
    with pytest.raises(EvaluationValidationError, match="validation manifest"):
        freeze_selection_artifact(metrics, manifest=development_manifest)
    forged_metrics = dict(metrics)
    forged_metrics["0.0"] = dict(forged_metrics["0.0"])
    forged_metrics["0.0"]["split"] = "development"
    with pytest.raises(EvaluationValidationError, match="validation v2 metrics"):
        freeze_selection_artifact(threshold_metrics=forged_metrics, manifest=manifest)
    unrelated = _case("case-unrelated", _group_id("validation", 1_600))
    unrelated_manifest = freeze_manifest([unrelated], [_label(unrelated)], split="validation")
    with pytest.raises(EvaluationValidationError, match="do not match validation manifest"):
        freeze_selection_artifact(metrics, manifest=unrelated_manifest)
    mismatched_metrics = dict(metrics)
    mismatched_metrics["0.0"] = dict(mismatched_metrics["0.0"])
    mismatched_metrics["0.0"]["provenance"] = dict(mismatched_metrics["0.0"]["provenance"])
    mismatched_metrics["0.0"]["provenance"]["input_sha256"] = "f" * 64
    with pytest.raises(EvaluationValidationError, match="thresholds disagree"):
        freeze_selection_artifact(threshold_metrics=mismatched_metrics, manifest=manifest)
    missing_counts = dict(metrics)
    missing_counts["0.0"] = dict(missing_counts["0.0"])
    missing_counts["0.0"].pop("support")
    with pytest.raises(EvaluationValidationError, match="support counts"):
        freeze_selection_artifact(threshold_metrics=missing_counts, manifest=manifest)
    incomplete_support = json.loads(json.dumps(metrics))
    incomplete_support["0.0"]["support"] = {
        "supported": 0,
        "unsupported": 0,
        "unknown": 0,
        "denominator": 0,
    }
    with pytest.raises(EvaluationValidationError, match="inconsistent"):
        freeze_selection_artifact(incomplete_support, manifest=manifest)


def test_ranker_selection_falls_back_to_defer_all_when_no_point_proposes() -> None:
    case = _case("case-ranker-defer", _group_id("validation", 1_200))
    label = _label(case)
    result = _result(case, status="OBSERVED", method="ranker")
    result["ranked_candidates"] = [
        {"candidate_id": "candidate-good", "score": -0.1},
    ]
    metrics = evaluate_ranker_thresholds([case], [label], [result])
    manifest = freeze_manifest([case], [label], split="validation")
    artifact = freeze_selection_artifact(metrics, manifest=manifest)
    assert artifact["selected_threshold"] is None
    assert artifact["fallback"] == "defer_all"


def test_wrong_candidate_is_unsupported_for_threshold_selection() -> None:
    case = _case("case-ranker-wrong", _group_id("validation", 1_400))
    label = _label(case)
    result = _result(case, status="OBSERVED", method="ranker")
    result["ranked_candidates"] = [
        {"candidate_id": "candidate-other", "score": 0.95},
    ]
    metrics = evaluate_ranker_thresholds([case], [label], [result])
    assert metrics["0.5"]["support"]["unsupported"] == 1
    manifest = freeze_manifest([case], [label], split="validation")
    artifact = freeze_selection_artifact(metrics, manifest=manifest)
    assert artifact["selected_threshold"] is None
    assert artifact["fallback"] == "defer_all"
