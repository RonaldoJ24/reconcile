from __future__ import annotations

import hashlib
from copy import deepcopy

import pytest

import reconcile.api.comparison as comparison_module
import reconcile.api.recordings as recordings_module
from reconcile.api.comparison import METHODS, compare_snapshot
from reconcile.api.recordings import (
    COMPARISON_SCHEMA_ID,
    PARSER_IDENTITY,
    RecordingError,
    _digest,
    verify_recording,
)
from reconcile.interpretation.schemas import PROMPT_VERSION, SCHEMA_VERSION
from reconcile.ml.runtime import ACTIVE_RULES_IDENTITY
from reconcile.persistence.service import _hash, _stable_trace_identity


def _snapshot() -> dict[str, object]:
    return {
        "rules_identity": ACTIVE_RULES_IDENTITY,
        "payment": {
            "source_account_id": "account",
            "transaction_id": "payment",
            "booking_date": "2026-01-15",
            "payer_name": "Customer",
            "reference": "Apply invoice INV-1.",
            "amount": 100,
            "currency": "MXN",
            "customer_id": "customer",
            "source_id": "payment-source",
            "version": 1,
        },
        "invoices": [
            {
                "customer_id": "customer",
                "customer_name": "Customer",
                "invoice_id": "INV-1",
                "issued_date": "2025-12-01",
                "due_date": "2026-01-01",
                "balance_as_of": "2026-01-15",
                "outstanding_amount": 100,
                "currency": "MXN",
                "version": 1,
            }
        ],
        "credits": [],
        "sources": [
            {
                "source_id": "message-source",
                "kind": "message",
                "sha256": "message-hash",
                "version": 1,
            }
        ],
        "evidence": {"message-source": "Apply invoice INV-1."},
        "candidate_context": {
            "group_id": "workspace-payment",
            "message": "Apply invoice INV-1.",
            "candidates": [
                {
                    "candidate_id": "candidate-1",
                    "invoice_ids": ["INV-1"],
                    "cash": [{"invoice_id": "INV-1", "amount": 100}],
                    "credits": [],
                }
            ],
        },
    }


def test_comparison_uses_all_non_actionable_methods_and_snapshot_facts() -> None:
    fingerprint = _hash(_stable_trace_identity(_snapshot()))
    result = compare_snapshot({"snapshot": _snapshot(), "input_fingerprint": fingerprint})

    assert [item["method"] for item in result["methods"]] == list(METHODS)
    assert all(item["actionable"] is False for item in result["methods"])
    assert result["methods"][0]["source"] == "rules"
    assert result["methods"][0]["candidate"] == {
        "cash": [{"invoice_id": "INV-1", "amount": 100}],
        "credits": [],
    }
    assert result["methods"][2]["source"] == "local"


def test_missing_or_invalid_snapshot_is_explicitly_unavailable() -> None:
    result = compare_snapshot({"input_fingerprint": "fingerprint"})
    assert [item["status"] for item in result["methods"]] == ["unavailable"] * 5
    assert all("unavailable" in str(item["reason"]) for item in result["methods"])

    fingerprint = _hash(_stable_trace_identity(_snapshot()))
    tampered = _snapshot()
    tampered["payment"] = {**tampered["payment"], "amount": 101}
    mismatch = compare_snapshot({"snapshot": tampered, "input_fingerprint": fingerprint})
    assert all(item["status"] == "unavailable" for item in mismatch["methods"])
    assert "fingerprint" in str(mismatch["methods"][0]["reason"])

    old_rules = {**_snapshot(), "rules_identity": "rules-old-unavailable"}
    old_fingerprint = _hash(_stable_trace_identity(old_rules))
    old_result = compare_snapshot(
        {"snapshot": old_rules, "input_fingerprint": old_fingerprint}
    )
    assert all(item["status"] == "unavailable" for item in old_result["methods"])
    assert "active rules identity" in str(old_result["methods"][0]["reason"])


def test_recording_verification_rejects_tampered_identity_and_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _snapshot()
    fingerprint = _hash(_stable_trace_identity(snapshot))
    result = {
        "decision": "select",
        "candidate_id": "candidate-1",
        "reason_code": "evidence_supported",
        "citations": [
            {
                "source_id": "message-source",
                "start": 0,
                "end": len("Apply invoice INV-1."),
                "quote": "Apply invoice INV-1.",
            }
        ],
    }
    recording = {
        "mode": "direct",
        "input_fingerprint": fingerprint,
        "rules_identity": ACTIVE_RULES_IDENTITY,
        "case_id": None,
        "case_version": None,
        "parser_identity": PARSER_IDENTITY,
        "prompt_version": PROMPT_VERSION,
        "schema_version": SCHEMA_VERSION,
        "comparison_schema": COMPARISON_SCHEMA_ID,
        "model_id": "deepseek-flash",
        "model_version": "recorded-v1",
        "artifact_sha256": hashlib.sha256(b"allowlisted-artifact").hexdigest(),
        "artifact_provenance": {},
        "sources": [{"kind": "message", "sha256": "message-hash", "version": 1}],
        "result": result,
        "citations": result["citations"],
        "result_sha256": _digest(result),
    }
    artifact_digest = recording["artifact_sha256"]
    recording["artifact_provenance"] = {"allowlisted": True, "sha256": artifact_digest}
    recording["recording_sha256"] = _digest(recording)
    monkeypatch.setattr(recordings_module, "TRUSTED_ARTIFACT_DIGESTS", frozenset({artifact_digest}))
    monkeypatch.setattr(
        recordings_module,
        "TRUSTED_RECORDING_DIGESTS",
        frozenset({recording["recording_sha256"]}),
    )
    assert verify_recording(
        recording,
        snapshot=snapshot,
        input_fingerprint=fingerprint,
        mode="direct",
        case_id=None,
        case_version=None,
    ) == recording

    tampered = {**recording, "input_fingerprint": "other"}
    with pytest.raises(RecordingError):
        verify_recording(
            tampered,
            snapshot=snapshot,
            input_fingerprint=fingerprint,
            mode="direct",
            case_id=None,
            case_version=None,
        )
    tampered_result = {**recording, "result": {**result, "candidate_id": "unknown"}}
    with pytest.raises(RecordingError):
        verify_recording(
            {**tampered_result, "result_sha256": _digest(tampered_result["result"])},
            snapshot=snapshot,
            input_fingerprint=fingerprint,
            mode="direct",
            case_id=None,
            case_version=None,
        )


@pytest.mark.parametrize("decision", ["select", "needs_review"])
def test_verified_registry_recordings_replay_direct_and_hybrid_from_same_snapshot(
    monkeypatch: pytest.MonkeyPatch, decision: str,
) -> None:
    snapshot = _snapshot()
    fingerprint = _hash(_stable_trace_identity(snapshot))
    citations = [
        {
            "source_id": "message-source",
            "start": 0,
            "end": len("Apply invoice INV-1."),
            "quote": "Apply invoice INV-1.",
        }
    ]
    if decision == "needs_review":
        citations = []
    result = {
        "decision": decision,
        "candidate_id": "candidate-1" if decision == "select" else None,
        "reason_code": "evidence_supported" if decision == "select" else "insufficient_evidence",
        "citations": citations,
    }
    artifact_digest = hashlib.sha256(b"allowlisted-artifact").hexdigest()
    rank_context = [{"candidate_id": "candidate-1", "raw_score": 0.25}]

    monkeypatch.setattr(
        comparison_module,
        "rank_candidates",
        lambda _group, force: {
            "ranked_candidate": "candidate-1",
            "score": 0.25,
            "ranked_candidates": [{"candidate_id": "candidate-1", "score": 0.25}],
            "latency_ms": 0.1,
        },
    )

    def recording(mode: str) -> dict[str, object]:
        value: dict[str, object] = {
            "mode": mode,
            "input_fingerprint": fingerprint,
            "rules_identity": ACTIVE_RULES_IDENTITY,
            "case_id": None,
            "case_version": None,
            "parser_identity": PARSER_IDENTITY,
            "prompt_version": PROMPT_VERSION,
            "schema_version": SCHEMA_VERSION,
            "comparison_schema": COMPARISON_SCHEMA_ID,
            "model_id": "deepseek-flash",
            "model_version": "recorded-v1",
            "artifact_sha256": artifact_digest,
            "artifact_provenance": {"allowlisted": True, "sha256": artifact_digest},
            "sources": [{"kind": "message", "sha256": "message-hash", "version": 1}],
            "result": result,
            "citations": citations,
            "result_sha256": _digest(result),
        }
        if mode == "hybrid":
            value["ranked_candidates"] = rank_context
        value["recording_sha256"] = _digest(value)
        return value

    direct = recording("direct")
    hybrid = recording("hybrid")
    monkeypatch.setattr(recordings_module, "TRUSTED_ARTIFACT_DIGESTS", frozenset({artifact_digest}))
    monkeypatch.setattr(
        recordings_module,
        "TRUSTED_RECORDING_DIGESTS",
        frozenset({direct["recording_sha256"], hybrid["recording_sha256"]}),
    )
    monkeypatch.setattr(recordings_module, "RECORDING_REGISTRY", (direct, hybrid))

    compared = compare_snapshot(
        {"snapshot": deepcopy(snapshot), "input_fingerprint": fingerprint}
    )
    direct_method, hybrid_method = compared["methods"][3], compared["methods"][4]
    expected_status = "proposed" if decision == "select" else "deferred"
    assert direct_method["status"] == expected_status
    assert hybrid_method["status"] == expected_status
    assert direct_method["source"] == "recorded"
    assert hybrid_method["source"] == "recorded"
    assert direct_method["actionable"] is False
    assert hybrid_method["actionable"] is False
