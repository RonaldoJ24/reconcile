"""Verified recordings of live interpretation calls for the v2 evaluation.

A recording binds one provider observation to the exact request the workflow
sent, the frozen system identity and the provider's result. The evaluator only
accepts a Direct observation after re-deriving it from a recording that passes
every check here; a flag on the observation itself is never trusted.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import ValidationError

from reconcile.interpretation.cache import source_fingerprint
from reconcile.interpretation.schemas import (
    InterpretationRequest,
    InterpretationResult,
    validate_result,
)

RECORDING_SCHEMA = "reconcile-eval-v2-recording-v1"
IDENTITY_FIELDS = (
    "frozen_commit",
    "prompt_version",
    "schema_version",
    "rules_identity",
    "model",
    "temperature",
)
OUTCOMES = frozenset({"selected", "needs_review", "unavailable"})


class RecordingVerificationError(ValueError):
    """A recording failed an integrity, identity or validation check."""


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str
    ).encode("utf-8")


def recording_digest(recording: Mapping[str, Any]) -> str:
    body = {key: value for key, value in recording.items() if key != "recording_sha256"}
    return hashlib.sha256(_canonical(body)).hexdigest()


def request_digest(request_payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical(request_payload)).hexdigest()


def seal_recording(recording: Mapping[str, Any]) -> dict[str, Any]:
    sealed = dict(recording)
    sealed.pop("recording_sha256", None)
    sealed["recording_sha256"] = recording_digest(sealed)
    return sealed


def _allocation_key(candidate: Mapping[str, Any]) -> tuple[Any, ...]:
    def amount(line: Mapping[str, Any]) -> int:
        value = line.get("amount", line.get("amount_centavos"))
        if isinstance(value, bool) or not isinstance(value, int):
            raise RecordingVerificationError("candidate amounts must be integer centavos")
        return int(value)

    cash = sorted((str(line["invoice_id"]), amount(line)) for line in candidate.get("cash", []))
    credits = sorted(
        (str(line["credit_note_id"]), str(line["invoice_id"]), amount(line))
        for line in candidate.get("credits", [])
    )
    return (
        str(candidate.get("candidate_id")),
        tuple(sorted(str(item) for item in candidate.get("invoice_ids", []))),
        tuple(cash),
        tuple(credits),
    )


def _input_candidates(input_row: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    rows = input_row.get("candidates")
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
        raise RecordingVerificationError("input row has no candidate list")
    candidates: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        if not isinstance(row, Mapping) or not isinstance(row.get("candidate_id"), str):
            raise RecordingVerificationError("input row candidate is invalid")
        candidates[str(row["candidate_id"])] = row
    return candidates


def observation_from_recording(
    recording: Any,
    *,
    input_row: Mapping[str, Any],
    identity: Mapping[str, Any],
) -> dict[str, Any]:
    """Return the only observation a recording can support, or raise."""

    if not isinstance(recording, Mapping):
        raise RecordingVerificationError("provider observation has no recording")
    if recording.get("schema") != RECORDING_SCHEMA:
        raise RecordingVerificationError("recording schema is not recognized")
    if recording.get("recording_sha256") != recording_digest(recording):
        raise RecordingVerificationError("recording digest does not match its content")
    if recording.get("case_id") != input_row.get("case_id"):
        raise RecordingVerificationError("recording belongs to a different case")
    for field in IDENTITY_FIELDS:
        if field not in identity or recording.get(field) != identity[field]:
            raise RecordingVerificationError(f"recording {field} differs from the frozen run")

    outcome = recording.get("outcome")
    if not isinstance(outcome, Mapping) or outcome.get("status") not in OUTCOMES:
        raise RecordingVerificationError("recording outcome is invalid")
    status = str(outcome["status"])
    if status == "unavailable":
        # A failed or budget-stopped call still counts in every denominator.
        return {"status": "UNAVAILABLE", "candidate_id": None, "allocation": None}
    expected = _input_candidates(input_row)
    payload = recording.get("request")
    if outcome.get("provider_called") is False:
        # Production defers without a provider call when there is nothing to choose
        # from or no note to quote; that is a deferral, not an outage.
        if status != "needs_review":
            raise RecordingVerificationError("an uncalled provider cannot select")
        if not expected and payload is None:
            return {"status": "NEEDS_REVIEW", "candidate_id": None, "allocation": None}
        if not isinstance(payload, Mapping):
            raise RecordingVerificationError("recording has no request")
        try:
            uncalled = InterpretationRequest.model_validate(payload)
        except ValidationError as exc:
            raise RecordingVerificationError("recorded request is invalid") from exc
        if uncalled.source_spans:
            raise RecordingVerificationError("a request with evidence must reach the provider")
        return {"status": "NEEDS_REVIEW", "candidate_id": None, "allocation": None}

    if not isinstance(payload, Mapping):
        raise RecordingVerificationError("recording has no request")
    try:
        request = InterpretationRequest.model_validate(payload)
    except ValidationError as exc:
        raise RecordingVerificationError("recorded request is invalid") from exc
    dumped = request.model_dump(mode="json")
    if recording.get("request_sha256") != request_digest(dumped):
        raise RecordingVerificationError("recorded request digest does not match")
    # The persisted interpretation revision stores this fingerprint; it proves the
    # recorded request is the one the production workflow actually sent.
    if recording.get("workflow_input_fingerprint") != source_fingerprint(dumped):
        raise RecordingVerificationError("recorded request differs from the workflow input")
    if request.mode != "direct":
        raise RecordingVerificationError("only Direct recordings are part of this run")

    requested = {candidate.candidate_id: candidate for candidate in request.candidates}
    if set(requested) != set(expected):
        raise RecordingVerificationError("recorded candidates differ from the evaluated input")
    for candidate_id, candidate in requested.items():
        if _allocation_key(candidate.model_dump(mode="json")) != _allocation_key(
            expected[candidate_id]
        ):
            raise RecordingVerificationError("recorded candidate allocation differs")

    try:
        result = InterpretationResult.model_validate(
            {
                "decision": "select" if status == "selected" else "needs_review",
                "candidate_id": outcome.get("candidate_id"),
                "reason_code": outcome.get("reason_code"),
                "citations": list(outcome.get("citations") or []),
            }
        )
        validate_result(request, result)
    except (ValidationError, ValueError) as exc:
        raise RecordingVerificationError("recorded result fails production validation") from exc

    if status == "needs_review":
        return {"status": "NEEDS_REVIEW", "candidate_id": None, "allocation": None}
    candidate_id = str(result.candidate_id)
    return {
        "status": "PROPOSED",
        "candidate_id": candidate_id,
        "allocation": dict(expected[candidate_id]),
        "citation_location_valid": True,
    }


__all__ = [
    "IDENTITY_FIELDS",
    "RECORDING_SCHEMA",
    "RecordingVerificationError",
    "observation_from_recording",
    "recording_digest",
    "request_digest",
    "seal_recording",
]
