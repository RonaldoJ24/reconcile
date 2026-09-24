"""Verification for authenticated, exact-input comparison recordings.

The runtime registry is intentionally empty in this release.  Keeping the
verification boundary here means a future allowlisted recording cannot be
mistaken for a test fixture or a result for a merely similar payment.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any

from reconcile.interpretation.schemas import (
    PROMPT_VERSION,
    SCHEMA_VERSION,
    InterpretationRequest,
    InterpretationResult,
    SourceSpan,
    validate_result,
)

COMPARISON_SCHEMA_ID = "reconcile-comparison-v1"
PARSER_IDENTITY = "reconcile-parser-v1"
RECORDING_REGISTRY: tuple[Mapping[str, Any], ...] = ()
ALLOWED_MODEL_IDS = frozenset({"gpt-6-luna"})
# No recording is trusted in the current release.  A future release must pin
# both the complete recording digest and the complete artifact digest here at
# packaging time; a registry entry cannot supply its own trust anchor.
TRUSTED_RECORDING_DIGESTS: frozenset[str] = frozenset()
TRUSTED_ARTIFACT_DIGESTS: frozenset[str] = frozenset()


class RecordingError(ValueError):
    """A recording failed an exact-input or authenticity check."""


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def _source_identity(snapshot: Mapping[str, Any]) -> list[dict[str, object]]:
    sources = snapshot.get("sources")
    if not isinstance(sources, list):
        raise RecordingError("recording input has no source snapshot")
    identity: list[dict[str, object]] = []
    for source in sources:
        if not isinstance(source, Mapping):
            raise RecordingError("recording input has an invalid source snapshot")
        kind, digest = source.get("kind"), source.get("sha256")
        if not isinstance(kind, str) or not kind or not isinstance(digest, str):
            raise RecordingError("recording input has incomplete source identity")
        version = source.get("version", 1)
        if not isinstance(version, (str, int)) or isinstance(version, bool):
            raise RecordingError("recording input has an invalid source version")
        identity.append({"kind": kind, "sha256": digest, "version": version})
    return sorted(identity, key=lambda item: (str(item["kind"]), str(item["sha256"])))


def _recording_sources(recording: Mapping[str, Any]) -> list[dict[str, object]]:
    """Read portable source identity, accepting the early mapping shape too."""

    sources = recording.get("sources")
    if isinstance(sources, list):
        value: list[dict[str, object]] = []
        for item in sources:
            if not isinstance(item, Mapping):
                raise RecordingError("recording source identity is invalid")
            value.append(
                {
                    "source_id": item.get("source_id"),
                    "kind": item.get("kind"),
                    "sha256": item.get("sha256"),
                    "version": item.get("version", 1),
                }
            )
        return sorted(value, key=lambda item: (str(item["kind"]), str(item["sha256"])))

    hashes = recording.get("source_hashes")
    versions = recording.get("source_versions")
    if not isinstance(hashes, Mapping) or not isinstance(versions, Mapping):
        raise RecordingError("recording source identity is missing")
    # Mapping keys are only a compatibility format.  Authentic registrations
    # should use ``sources`` so UUIDs cannot become part of portable identity.
    return sorted(
        [
            {"kind": "unknown", "sha256": digest, "version": versions.get(key, 1)}
            for key, digest in hashes.items()
            if isinstance(digest, str)
        ],
        key=lambda item: str(item["sha256"]),
    )


def _snapshot_request(
    snapshot: Mapping[str, Any],
    mode: str,
    ranked_candidates: list[dict[str, object]] | None = None,
) -> InterpretationRequest:
    payment = snapshot.get("payment")
    invoices = snapshot.get("invoices")
    credits = snapshot.get("credits")
    context = snapshot.get("candidate_context")
    evidence = snapshot.get("evidence")
    if not isinstance(payment, Mapping) or not isinstance(invoices, list):
        raise RecordingError("recording input facts are incomplete")
    if not isinstance(credits, list) or not isinstance(context, Mapping):
        raise RecordingError("recording candidate context is incomplete")
    if not isinstance(evidence, Mapping):
        raise RecordingError("recording evidence is incomplete")
    source_by_id = {
        str(source.get("source_id")): source
        for source in snapshot.get("sources", [])
        if isinstance(source, Mapping) and source.get("source_id") is not None
    }
    source_spans = tuple(
        SourceSpan(
            source_id=str(source_id),
            start=0,
            end=len(text),
            content=text,
            source_hash=(
                str(source_by_id[source_id].get("sha256", ""))
                if source_id in source_by_id
                else ""
            ),
            source_version=1,
        )
        for source_id, text in sorted(evidence.items())
        if isinstance(text, str) and text
    )
    candidates = context.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        raise RecordingError("recording has no candidate context")
    try:
        candidate_rows = tuple(candidates)
        payment_row = {
            "payment_id": "recorded-payment",
            "amount_centavos": payment["amount"],
            "currency": payment.get("currency", "MXN"),
            "booking_date": payment["booking_date"],
            "payer_name": payment.get("payer_name", ""),
            "reference": payment.get("reference", ""),
            "version": payment.get("version", 1),
        }
        invoice_rows = tuple(
            {
                "invoice_id": row["invoice_id"],
                "outstanding_amount_centavos": row["outstanding_amount"],
                "currency": row.get("currency", "MXN"),
                "customer_id": row.get("customer_id"),
                "customer_name": row.get("customer_name", ""),
                "issued_date": row.get("issued_date"),
                "due_date": row.get("due_date"),
                "balance_as_of": row.get("balance_as_of"),
                "version": row.get("version", 1),
            }
            for row in invoices
            if isinstance(row, Mapping)
        )
        credit_rows = tuple(
            {
                "credit_note_id": row["credit_note_id"],
                "invoice_id": row.get("invoice_id"),
                "available_amount_centavos": row["available_amount"],
                "currency": row.get("currency", "MXN"),
                "customer_id": row.get("customer_id"),
                "balance_as_of": row.get("balance_as_of"),
                "version": row.get("version", 1),
            }
            for row in credits
            if isinstance(row, Mapping)
        )
        request_data = {
            "workspace_id": "recorded-workspace",
            "payment": payment_row,
            "invoices": invoice_rows,
            "credits": credit_rows,
            "candidates": candidate_rows,
            "source_spans": source_spans,
            "mode": mode,
            "prompt_version": PROMPT_VERSION,
            "schema_version": SCHEMA_VERSION,
            "decision_timestamp": datetime.now(UTC),
        }
        if mode == "hybrid":
            if not ranked_candidates:
                raise RecordingError("hybrid comparison has no executed rank context")
            request_data["ranked_candidates"] = ranked_candidates
        return InterpretationRequest.model_validate(request_data)
    except (KeyError, TypeError, ValueError) as exc:
        raise RecordingError("recording input cannot be validated") from exc


def verify_recording(
    recording: Mapping[str, Any],
    *,
    snapshot: Mapping[str, Any],
    input_fingerprint: str,
    mode: str,
    case_id: str | None,
    case_version: str | None,
    expected_ranked_candidates: list[dict[str, object]] | None = None,
) -> Mapping[str, Any]:
    """Verify one immutable exact-input recording before runtime use."""

    if recording.get("test_only") is True:
        raise RecordingError("test-only recordings are not authentic runtime inputs")
    if recording.get("mode") != mode:
        raise RecordingError("recording mode does not match the requested method")
    if recording.get("input_fingerprint") != input_fingerprint:
        raise RecordingError("recording input fingerprint does not match")
    if recording.get("rules_identity") != snapshot.get("rules_identity"):
        raise RecordingError("recording rules identity does not match")
    if recording.get("case_id") != case_id or recording.get("case_version") != case_version:
        raise RecordingError("recording case identity does not match")
    if recording.get("parser_identity") != PARSER_IDENTITY:
        raise RecordingError("recording parser identity is not verified")
    if recording.get("prompt_version") != PROMPT_VERSION:
        raise RecordingError("recording prompt identity is not verified")
    if recording.get("schema_version") != SCHEMA_VERSION:
        raise RecordingError("recording result schema is not verified")
    if recording.get("comparison_schema") != COMPARISON_SCHEMA_ID:
        raise RecordingError("recording comparison schema is not verified")
    if mode == "hybrid" and recording.get("ranked_candidates") != expected_ranked_candidates:
        raise RecordingError("recording rank context does not match the executed ranker")
    if recording.get("model_id") not in ALLOWED_MODEL_IDS:
        raise RecordingError("recording model is not allowlisted")
    if not isinstance(recording.get("model_version"), str) or not recording["model_version"]:
        raise RecordingError("recording model version is missing")
    artifact_sha256 = recording.get("artifact_sha256")
    if (
        not isinstance(artifact_sha256, str)
        or not re.fullmatch(r"[0-9a-f]{64}", artifact_sha256)
        or artifact_sha256 not in TRUSTED_ARTIFACT_DIGESTS
    ):
        raise RecordingError("recording artifact digest is not verified")
    provenance = recording.get("artifact_provenance")
    if (
        not isinstance(provenance, Mapping)
        or provenance.get("allowlisted") is not True
        or provenance.get("sha256") != artifact_sha256
    ):
        raise RecordingError("recording artifact provenance is not allowlisted")
    recording_sources = _recording_sources(recording)
    expected_sources = _source_identity(snapshot)
    if [
        {
            "kind": item.get("kind"),
            "sha256": item.get("sha256"),
            "version": item.get("version", 1),
        }
        for item in recording_sources
    ] != expected_sources:
        raise RecordingError("recording source identity does not match")
    citations = recording.get("citations")
    result = recording.get("result")
    if not isinstance(citations, list):
        raise RecordingError("recording citations must be a list")
    if not isinstance(result, Mapping):
        raise RecordingError("recording has no result")
    if recording.get("result_sha256") != _digest(result):
        raise RecordingError("recording result digest verification failed")
    result_citations = result.get("citations")
    if not isinstance(result_citations, list) or result_citations != citations:
        raise RecordingError("recording citations do not match its result")
    recording_digest = recording.get("recording_sha256")
    digest_payload = {
        key: value
        for key, value in recording.items()
        if key != "recording_sha256"
    }
    if (
        not isinstance(recording_digest, str)
        or recording_digest != _digest(digest_payload)
        or recording_digest not in TRUSTED_RECORDING_DIGESTS
    ):
        raise RecordingError("recording digest is not trusted")
    try:
        request = _snapshot_request(snapshot, mode, expected_ranked_candidates)
        result_for_validation = deepcopy(dict(result))
        result_citations = result_for_validation.get("citations")
        if not isinstance(result_citations, list):
            raise RecordingError("recording result citations are invalid")
        source_by_identity = {
            (str(source.get("kind")), str(source.get("sha256")), source.get("version", 1)): str(
                source.get("source_id")
            )
            for source in snapshot.get("sources", [])
            if isinstance(source, Mapping) and source.get("source_id") is not None
        }
        source_by_recorded_id = {
            str(item.get("source_id")): source_by_identity[
                (str(item.get("kind")), str(item.get("sha256")), item.get("version", 1))
            ]
            for item in recording_sources
            if item.get("source_id") is not None
            and (
                str(item.get("kind")), str(item.get("sha256")), item.get("version", 1)
            )
            in source_by_identity
        }
        normalized_citations: list[dict[str, Any]] = []
        for citation in result_citations:
            if not isinstance(citation, Mapping):
                raise RecordingError("recording citation is invalid")
            original_id = str(citation.get("source_id"))
            current_id = source_by_recorded_id.get(original_id, original_id)
            if current_id not in {str(item) for item in snapshot.get("evidence", {})}:
                raise RecordingError("recording citation names a foreign source")
            normalized_citations.append({**citation, "source_id": current_id})
        result_for_validation["citations"] = normalized_citations
        parsed_result = InterpretationResult.model_validate(result_for_validation)
        validate_result(request, parsed_result)
    except RecordingError:
        raise
    except (TypeError, ValueError) as exc:
        raise RecordingError(
            "recording result failed schema, citation, and candidate validation"
        ) from exc
    return recording


def find_verified_recording(
    *,
    snapshot: Mapping[str, Any],
    input_fingerprint: str,
    mode: str,
    case_id: str | None,
    case_version: str | None,
    expected_ranked_candidates: list[dict[str, object]] | None = None,
) -> Mapping[str, Any] | None:
    """Return an authenticated registry match; fail closed when empty."""

    for recording in RECORDING_REGISTRY:
        try:
            return verify_recording(
                recording,
                snapshot=snapshot,
                input_fingerprint=input_fingerprint,
                mode=mode,
                case_id=case_id,
                case_version=case_version,
                expected_ranked_candidates=expected_ranked_candidates,
            )
        except RecordingError:
            continue
    return None


__all__ = [
    "ALLOWED_MODEL_IDS",
    "COMPARISON_SCHEMA_ID",
    "PARSER_IDENTITY",
    "RECORDING_REGISTRY",
    "TRUSTED_ARTIFACT_DIGESTS",
    "TRUSTED_RECORDING_DIGESTS",
    "RecordingError",
    "find_verified_recording",
    "verify_recording",
]
