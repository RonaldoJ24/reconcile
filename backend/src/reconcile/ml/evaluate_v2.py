"""Offline, observation-only evaluation protocol for portfolio v2.

This module deliberately does not call the rules engine, a ranker, or a
provider.  It validates frozen inputs, labels, and recorded method
observations, then computes denominator-first metrics.  An observation is
usable only when it identifies the exact input/candidate/evidence/validator
fingerprints that were used to produce it.

The final split is guarded before any input or label file is opened.  The
guard is intentionally inconvenient: creating this harness never authorizes
access to reserved labels.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, cast

PROTOCOL_VERSION: Final = "reconcile-eval-v2"
RANKER_THRESHOLDS: Final[tuple[float, ...]] = (0.0, 0.25, 0.5, 0.75, 0.9, 1.0)
SPLIT_NAMES: Final[frozenset[str]] = frozenset({"development", "validation", "reserved-final"})
FINAL_SPLIT: Final[str] = "reserved-final"

JsonObject = dict[str, Any]
JsonRow = Mapping[str, Any]


class EvaluationValidationError(ValueError):
    """Raised when an evaluation input violates the v2 protocol."""


class FinalAccessError(PermissionError):
    """Raised when reserved-final access is missing or has already been used."""


@dataclass(frozen=True)
class ValidatedJoin:
    """The exact case/result/label join used by an evaluation."""

    inputs: Mapping[str, JsonRow]
    labels: Mapping[str, JsonRow]
    results: Mapping[tuple[str, str], JsonRow]
    methods: tuple[str, ...]
    split: str | None


def _canonical_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
        + "\n"
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def fingerprint_rows(rows: Iterable[JsonRow]) -> str:
    """Hash ordered canonical JSONL rows; order is part of the frozen input."""

    return sha256_bytes(b"".join(_canonical_bytes(dict(row)) for row in rows))


def fingerprint_value(value: Any) -> str:
    """Hash a canonical JSON value for input/candidate/evidence provenance."""

    return sha256_bytes(_canonical_bytes(value))


def stable_split(group_id: str) -> str:
    """Assign a group with SHA-256(``reconcile-eval-v2:<group_id>``) modulo 10."""

    if not isinstance(group_id, str) or not group_id.strip():
        raise EvaluationValidationError("group_id must be a non-empty string")
    bucket = (
        int.from_bytes(hashlib.sha256(f"{PROTOCOL_VERSION}:{group_id}".encode()).digest(), "big")
        % 10
    )
    if bucket <= 5:
        return "development"
    if bucket <= 7:
        return "validation"
    return FINAL_SPLIT


def _required_string(row: JsonRow, field: str, context: str) -> str:
    value = row.get(field)
    if not isinstance(value, str) or not value.strip():
        raise EvaluationValidationError(f"{context} requires non-empty {field}")
    return value


def _require_integer_centavos(value: Any, context: str, *, positive: bool = False) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise EvaluationValidationError(f"{context} must be an integer number of centavos")
    if value < 0 or (positive and value == 0):
        qualifier = "positive " if positive else "non-negative "
        raise EvaluationValidationError(f"{context} must be {qualifier}integer centavos")
    return cast(int, value)


def _sequence(value: Any, context: str) -> Sequence[Any]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise EvaluationValidationError(f"{context} must be a list")
    return value


def validate_allocation(allocation: JsonRow | None, *, context: str = "allocation") -> None:
    """Validate a complete allocation, including linked credit-note identity."""

    if not isinstance(allocation, Mapping):
        raise EvaluationValidationError(f"{context} must be an object")
    for key in ("cash", "credits"):
        rows = _sequence(allocation.get(key, []), f"{context}.{key}")
        for index, row in enumerate(rows):
            if not isinstance(row, Mapping):
                raise EvaluationValidationError(f"{context}.{key}[{index}] must be an object")
            row_context = f"{context}.{key}[{index}]"
            _required_string(row, "invoice_id", row_context)
            _require_integer_centavos(row.get("amount"), f"{row_context}.amount", positive=True)
            if key == "credits":
                _required_string(row, "credit_note_id", row_context)
                if "credit_id" in row or "amount_cents" in row:
                    raise EvaluationValidationError(
                        f"{row_context} must use canonical credit fields"
                    )


def allocation_key(
    allocation: JsonRow,
) -> tuple[tuple[tuple[str, int], ...], tuple[tuple[str, str, int], ...]]:
    """Return an exact, order-independent cash/credit allocation key."""

    validate_allocation(allocation)
    cash = tuple(
        sorted(
            (str(row["invoice_id"]), int(row["amount"]))
            for row in _sequence(allocation.get("cash", []), "allocation.cash")
        )
    )
    credits = tuple(
        sorted(
            (str(row["credit_note_id"]), str(row["invoice_id"]), int(row["amount"]))
            for row in _sequence(allocation.get("credits", []), "allocation.credits")
        )
    )
    return cash, credits


def allocation_matches(expected: JsonRow, actual: JsonRow) -> bool:
    """Compare all cash amounts and linked credit IDs, not only invoice totals."""

    try:
        return allocation_key(expected) == allocation_key(actual)
    except EvaluationValidationError:
        return False


def _lineage_tokens(row: JsonRow, context: str, *, require_explicit: bool) -> set[str]:
    lineage = row.get("lineage")
    if lineage is None:
        if require_explicit:
            raise EvaluationValidationError(f"{context} requires explicit lineage metadata")
        return set()
    if not isinstance(lineage, Mapping):
        raise EvaluationValidationError(f"{context}.lineage must be an object")
    group_id = _required_string(row, "group_id", context)
    lineage_group = lineage.get("group_id")
    if lineage_group is not None and lineage_group != group_id:
        raise EvaluationValidationError(f"{context}.lineage.group_id disagrees with group_id")

    tokens: set[str] = set()
    entity_fields = {"entity_ids", "shared_entity_ids", "history_ids"}
    variant_fields = {"variant_ids"}
    relationship_fields = {"related_case_ids", "parent_ids"}
    for field in (*entity_fields, *variant_fields, *relationship_fields):
        raw = lineage.get(field, [])
        if raw is None:
            continue
        for value in _sequence(raw, f"{context}.lineage.{field}"):
            token = _required_string({"value": value}, "value", f"{context}.lineage.{field}")
            namespace = (
                "entity"
                if field in entity_fields
                else "variant"
                if field in variant_fields
                else "relationship"
            )
            tokens.add(f"{namespace}:{token}")
    for field in ("variant_of", "shared_entity_id", "history_id"):
        value = lineage.get(field)
        if value is not None:
            token = _required_string({"value": value}, "value", f"{context}.lineage.{field}")
            namespace = "variant" if field == "variant_of" else "entity"
            tokens.add(f"{namespace}:{token}")
    return tokens


def validate_lineage(rows: Iterable[JsonRow], *, require_explicit: bool = True) -> None:
    """Reject duplicate cases and related entities split across group IDs."""

    input_rows = list(rows)
    case_groups: dict[str, str] = {}
    for index, row in enumerate(input_rows):
        context = f"input[{index}]"
        case_id = _required_string(row, "case_id", context)
        group_id = _required_string(row, "group_id", context)
        if case_id in case_groups:
            raise EvaluationValidationError(f"duplicate input case_id {case_id!r}")
        case_groups[case_id] = group_id

    token_groups: dict[str, str] = {}
    for index, row in enumerate(input_rows):
        context = f"input[{index}]"
        case_id = _required_string(row, "case_id", context)
        group_id = _required_string(row, "group_id", context)
        tokens = _lineage_tokens(row, context, require_explicit=require_explicit)
        for token in tokens:
            prior_group = token_groups.setdefault(token, group_id)
            if prior_group != group_id:
                raise EvaluationValidationError(
                    f"lineage token {token!r} crosses groups {prior_group!r} and {group_id!r}"
                )
        lineage = row.get("lineage")
        if not isinstance(lineage, Mapping):
            continue
        for field in ("variant_of", "related_case_ids", "parent_ids"):
            raw = lineage.get(field)
            references = (
                [raw]
                if field == "variant_of" and raw is not None
                else list(_sequence(raw, f"{context}.lineage.{field}"))
                if raw is not None
                else []
            )
            for reference in references:
                referenced_case = _required_string(
                    {"value": reference}, "value", f"{context}.lineage.{field}"
                )
                if referenced_case not in case_groups:
                    raise EvaluationValidationError(
                        f"{context}.lineage.{field} references unknown case {referenced_case!r}"
                    )
                if case_groups[referenced_case] != group_id:
                    raise EvaluationValidationError(
                        f"lineage case reference {referenced_case!r} crosses groups"
                    )


def _index_cases(rows: Iterable[JsonRow], kind: str) -> dict[str, JsonRow]:
    indexed: dict[str, JsonRow] = {}
    for index, row in enumerate(rows):
        context = f"{kind}[{index}]"
        case_id = _required_string(row, "case_id", context)
        if case_id in indexed:
            raise EvaluationValidationError(f"duplicate {kind} case_id {case_id!r}")
        indexed[case_id] = row
    return indexed


def _validate_split(row: JsonRow, *, split: str | None, context: str) -> str:
    group_id = _required_string(row, "group_id", context)
    assigned = stable_split(group_id)
    declared = row.get("split")
    if declared is not None and declared != assigned:
        raise EvaluationValidationError(f"{context}.split does not match deterministic group split")
    if split is not None and assigned != split:
        raise EvaluationValidationError(
            f"{context} group {group_id!r} belongs to {assigned}, not {split}"
        )
    return assigned


def _validate_public_exposure(rows: Iterable[JsonRow], split: str | None) -> None:
    for index, row in enumerate(rows):
        if row.get("public_exposed") is True and split != "development":
            raise EvaluationValidationError(
                f"input[{index}] is public/exposed data and may only be in development"
            )


def _validate_input_cases(inputs: Mapping[str, JsonRow]) -> None:
    for case_id, row in inputs.items():
        payment = row.get("payment")
        if isinstance(payment, Mapping) and "amount" in payment:
            _require_integer_centavos(payment["amount"], f"input {case_id}.payment.amount")
        candidates = _sequence(row.get("candidates", []), f"input {case_id}.candidates")
        candidate_ids: set[str] = set()
        for index, candidate in enumerate(candidates):
            if not isinstance(candidate, Mapping):
                raise EvaluationValidationError(
                    f"input {case_id}.candidates[{index}] must be an object"
                )
            candidate_id = _required_string(
                candidate, "candidate_id", f"input {case_id}.candidates[{index}]"
            )
            if candidate_id in candidate_ids:
                raise EvaluationValidationError(
                    f"duplicate candidate_id {candidate_id!r} in input {case_id}"
                )
            candidate_ids.add(candidate_id)
            validate_allocation(candidate, context=f"input {case_id}.candidate {candidate_id}")


def _result_key(row: JsonRow, context: str, default_method: str | None) -> tuple[str, str]:
    case_id = _required_string(row, "case_id", context)
    method_value = row.get("method", default_method)
    method = method_value if isinstance(method_value, str) and method_value.strip() else None
    if method is None:
        raise EvaluationValidationError(f"{context} requires method")
    return case_id, method


def validate_join(
    inputs: Iterable[JsonRow],
    results: Iterable[JsonRow],
    labels: Iterable[JsonRow],
    *,
    split: str | None = None,
    method: str | None = None,
    require_lineage: bool = False,
) -> ValidatedJoin:
    """Strictly join one input and label per case and one result per method."""

    if split is not None and split not in SPLIT_NAMES:
        raise EvaluationValidationError(f"unknown evaluation split {split!r}")
    input_rows = list(inputs)
    label_rows = list(labels)
    result_rows = list(results)
    input_map = _index_cases(input_rows, "input")
    label_map = _index_cases(label_rows, "label")
    if set(input_map) != set(label_map):
        raise EvaluationValidationError("input and label case IDs are incomplete or have extras")
    _validate_input_cases(input_map)
    _validate_public_exposure((*input_rows, *label_rows), split)
    if require_lineage:
        validate_lineage(input_rows)
    for index, row in enumerate(input_rows):
        _validate_split(row, split=split, context=f"input[{index}]")
    for index, row in enumerate(label_rows):
        _validate_split(row, split=split, context=f"label[{index}]")

    indexed_results: dict[tuple[str, str], JsonRow] = {}
    methods: set[str] = set()
    for index, row in enumerate(result_rows):
        key = _result_key(row, f"result[{index}]", method)
        case_id, method_name = key
        if case_id not in input_map:
            raise EvaluationValidationError(f"result[{index}] has unknown case_id {case_id!r}")
        if key in indexed_results:
            raise EvaluationValidationError(f"duplicate result for case_id/method {key!r}")
        if method is not None and method_name != method:
            raise EvaluationValidationError(
                f"result[{index}] method differs from requested {method!r}"
            )
        input_group = input_map[case_id].get("group_id")
        result_group = row.get("group_id", input_group)
        if result_group != input_group:
            raise EvaluationValidationError(f"result[{index}] group_id disagrees with input")
        _validate_split(row, split=split, context=f"result[{index}]")
        indexed_results[key] = row
        methods.add(method_name)
    # A requested method defines the expected result set even when the result
    # file is empty; otherwise a missing file would look like a valid empty
    # evaluation.  Manifest creation intentionally omits results and therefore
    # leaves ``method`` unset.
    if method is not None:
        methods.add(method)
    expected_keys = {(case_id, method_name) for case_id in input_map for method_name in methods}
    if indexed_results.keys() != expected_keys:
        missing = sorted(expected_keys - indexed_results.keys())
        extra = sorted(indexed_results.keys() - expected_keys)
        raise EvaluationValidationError(
            f"result join is incomplete (missing={missing}, extra={extra})"
        )
    label_by_case = {case_id: label_map[case_id] for case_id in input_map}
    for case_id, label in label_by_case.items():
        if label.get("group_id") != input_map[case_id].get("group_id"):
            raise EvaluationValidationError(f"label for {case_id!r} disagrees with input group_id")
    _validate_labels_against_inputs(input_map, label_by_case)
    return ValidatedJoin(
        inputs=input_map,
        labels=label_by_case,
        results=indexed_results,
        methods=tuple(sorted(methods)),
        split=split,
    )


def _label_semantic_support(label: JsonRow) -> str:
    if "semantic_support" not in label:
        raise EvaluationValidationError("label.semantic_support is required")
    value = label.get("semantic_support")
    if value in {"supported", "unsupported", "unknown"}:
        return cast(str, value)
    raise EvaluationValidationError(
        "label.semantic_support must be supported, unsupported, or unknown"
    )


def _observation_citation(result: JsonRow) -> bool | None:
    value = result.get("citation_location_valid")
    if value is None:
        return None
    if not isinstance(value, bool):
        raise EvaluationValidationError("result.citation_location_valid must be boolean or null")
    return value


def _expected_allocations(label: JsonRow) -> tuple[JsonRow, ...]:
    raw = label.get("expected_allocation")
    if raw is None:
        raw = label.get("acceptable_allocations")
    if raw is None:
        return ()
    values: Sequence[Any]
    if isinstance(raw, Mapping):
        values = (raw,)
    else:
        values = _sequence(raw, "label.acceptable_allocations")
    allocations: list[JsonRow] = []
    for index, allocation in enumerate(values):
        if not isinstance(allocation, Mapping):
            raise EvaluationValidationError(f"label allocation {index} must be an object")
        validate_allocation(allocation, context=f"label allocation {index}")
        allocations.append(allocation)
    return tuple(allocations)


def _label_answerable(label: JsonRow) -> bool:
    value = label.get("answerable")
    if not isinstance(value, bool):
        raise EvaluationValidationError("label.answerable must be boolean")
    acceptable = label.get("acceptable_candidate_ids", [])
    if not isinstance(acceptable, Sequence) or isinstance(acceptable, (str, bytes)):
        raise EvaluationValidationError("label.acceptable_candidate_ids must be a list")
    if len(set(str(item) for item in acceptable)) != len(acceptable):
        raise EvaluationValidationError("label.acceptable_candidate_ids must be unique")
    expected = _expected_allocations(label)
    if value and (not acceptable or not expected):
        raise EvaluationValidationError(
            "answerable labels require an acceptable candidate or allocation"
        )
    if not value and (acceptable or expected):
        raise EvaluationValidationError(
            "unanswerable labels cannot name acceptable candidates or allocations"
        )
    return value


def _validate_labels_against_inputs(
    inputs: Mapping[str, JsonRow], labels: Mapping[str, JsonRow]
) -> None:
    for case_id, label in labels.items():
        answerable = _label_answerable(label)
        semantic_support = _label_semantic_support(label)
        if answerable and semantic_support == "unsupported":
            raise EvaluationValidationError(
                f"answerable label {case_id!r} cannot be semantically unsupported"
            )
        if not answerable and semantic_support == "supported":
            raise EvaluationValidationError(
                f"unanswerable label {case_id!r} cannot be semantically supported"
            )
        candidate_rows = _sequence(inputs[case_id].get("candidates", []), "input.candidates")
        candidates = {
            _required_string(candidate, "candidate_id", f"input {case_id}.candidate"): candidate
            for candidate in candidate_rows
            if isinstance(candidate, Mapping)
        }
        acceptable = label.get("acceptable_candidate_ids", [])
        if not isinstance(acceptable, Sequence) or isinstance(acceptable, (str, bytes)):
            raise EvaluationValidationError("label.acceptable_candidate_ids must be a list")
        acceptable_ids = [
            _required_string({"value": value}, "value", f"label {case_id}.acceptable_candidate_ids")
            for value in acceptable
        ]
        missing = sorted(set(acceptable_ids) - set(candidates))
        if missing:
            raise EvaluationValidationError(f"label {case_id!r} names missing candidates {missing}")
        expected = _expected_allocations(label)
        if expected:
            for expected_allocation in expected:
                matches = [
                    candidate_id
                    for candidate_id, candidate in candidates.items()
                    if allocation_matches(expected_allocation, candidate)
                ]
                if not matches or not set(matches).intersection(acceptable_ids):
                    raise EvaluationValidationError(
                        f"label {case_id!r} expected allocation does not match "
                        "an acceptable input candidate"
                    )


def _validate_observation(
    result: JsonRow,
    *,
    context: str,
    method_name: str | None = None,
    ranker_timing: bool = False,
    allow_unmaterialized: bool = False,
) -> None:
    status = result.get("status")
    statuses = {
        "PROPOSED",
        "DEFERRED",
        "NEEDS_REVIEW",
        "ERROR",
        "FAILED",
        "UNAVAILABLE",
    }
    if allow_unmaterialized:
        statuses.add("OBSERVED")
    if not isinstance(status, str) or status.upper() not in statuses:
        raise EvaluationValidationError(f"{context}.status is not a supported observation outcome")
    for field in (
        "input_fingerprint",
        "candidate_fingerprint",
        "evidence_fingerprint",
        "validator_identity",
    ):
        _required_string(result, field, context)
    duration = result.get("duration_ms")
    if duration is not None and (
        isinstance(duration, bool)
        or not isinstance(duration, (int, float))
        or not math.isfinite(float(duration))
        or duration < 0
    ):
        raise EvaluationValidationError(
            f"{context}.duration_ms must be null or a finite non-negative number"
        )
    if ranker_timing and (result.get("timing_scope") != "features+predict" or duration is None):
        raise EvaluationValidationError(f"{context} must time features+predict")
    provider_method = str(method_name or result.get("method", "")).strip().lower()
    if provider_method in {"direct", "hybrid", "provider-direct", "provider-hybrid"}:
        if status.upper() != "UNAVAILABLE":
            raise EvaluationValidationError(
                "Direct/Hybrid observations are unavailable until an authentic loader exists"
            )
    _observation_citation(result)
    if status.upper() == "PROPOSED":
        _required_string(result, "candidate_id", context)
        validate_allocation(result.get("allocation"), context=f"{context}.allocation")


def _candidate_for_id(input_row: JsonRow, candidate_id: str) -> JsonRow | None:
    for candidate in _sequence(input_row.get("candidates", []), "input.candidates"):
        if isinstance(candidate, Mapping) and candidate.get("candidate_id") == candidate_id:
            return candidate
    return None


def _proposal_is_correct(input_row: JsonRow, label: JsonRow, result: JsonRow) -> bool:
    candidate_id = result.get("candidate_id")
    acceptable = label.get("acceptable_candidate_ids", [])
    if not isinstance(acceptable, Sequence) or isinstance(acceptable, (str, bytes)):
        return False
    if not isinstance(candidate_id, str) or candidate_id not in acceptable:
        return False
    candidate = _candidate_for_id(input_row, candidate_id)
    actual = result.get("allocation")
    if candidate is None or not isinstance(actual, Mapping):
        return False
    if not allocation_matches(candidate, actual):
        return False
    expected = _expected_allocations(label)
    if not expected:
        return True
    return any(allocation_matches(candidate, actual) for candidate in expected)


def _metric_counts(join: ValidatedJoin, method: str) -> JsonObject:
    cases = len(join.inputs)
    answerable = 0
    proposals = 0
    correct_proposals = 0
    supported = unsupported = semantic_unknown = 0
    citation_valid = citation_invalid = citation_unknown = 0
    answerable_correct = 0
    deferred = correct_deferrals = unnecessary_deferrals = 0
    errors = unavailable = 0
    payment_value = 0
    durations: list[float] = []

    for case_id, input_row in join.inputs.items():
        label = join.labels[case_id]
        result = join.results[(case_id, method)]
        answerable_case = _label_answerable(label)
        answerable += int(answerable_case)
        payment = input_row.get("payment")
        if isinstance(payment, Mapping) and "amount" in payment:
            payment_value += _require_integer_centavos(
                payment["amount"], f"input {case_id}.payment.amount"
            )
        outcome = str(result["status"]).upper()
        duration = result.get("duration_ms")
        if isinstance(duration, (int, float)) and not isinstance(duration, bool):
            durations.append(float(duration))
        if outcome in {"ERROR", "FAILED"}:
            errors += 1
            continue
        if outcome == "UNAVAILABLE":
            unavailable += 1
            continue
        if outcome == "PROPOSED":
            proposals += 1
            correct = _proposal_is_correct(input_row, label, result)
            correct_proposals += int(correct)
            if answerable_case:
                answerable_correct += int(correct)
            # A proposal with the wrong candidate or allocation is an observed
            # unsupported recommendation even when the case itself is
            # answerable.  Semantic labels remain separate from citation data.
            support = "unsupported" if not correct else _label_semantic_support(label)
            if support == "supported":
                supported += 1
            elif support == "unsupported":
                unsupported += 1
            else:
                semantic_unknown += 1
            citation = _observation_citation(result)
            if citation is True:
                citation_valid += 1
            elif citation is False:
                citation_invalid += 1
            else:
                citation_unknown += 1
        elif outcome in {"DEFERRED", "NEEDS_REVIEW"}:
            deferred += 1
            if answerable_case:
                unnecessary_deferrals += 1
            else:
                correct_deferrals += 1

    def rate(numerator: int, denominator: int) -> float | None:
        return numerator / denominator if denominator else None

    ordered_durations = sorted(durations)
    timing = {
        "measured": len(durations),
        "unknown": cases - len(durations),
        "mean_ms": sum(durations) / len(durations) if durations else None,
        "p95_ms": ordered_durations[max(0, math.ceil(len(durations) * 0.95) - 1)]
        if durations
        else None,
    }
    provenance = {
        "input_sha256": fingerprint_rows(join.inputs.values()),
        "target_sha256": fingerprint_rows(join.labels.values()),
        "input_count": cases,
        "target_count": cases,
    }

    return {
        "protocol": PROTOCOL_VERSION,
        "split": join.split,
        "method": method,
        "provenance": provenance,
        "denominators": {
            "all_cases": cases,
            "answerable_cases": answerable,
            "proposals": proposals,
            "deferred": deferred,
            "citation_labeled_proposals": citation_valid + citation_invalid,
            "semantic_labeled_proposals": supported + unsupported,
            "payment_value_centavos": payment_value,
        },
        "support": {
            "supported": supported,
            "unsupported": unsupported,
            "unknown": semantic_unknown,
            "denominator": supported + unsupported,
        },
        "resolution": {
            "answerable_correct": answerable_correct,
            "denominator": answerable,
            "rate": rate(answerable_correct, answerable),
        },
        "proposal": {
            "proposals": proposals,
            "correct": correct_proposals,
            "precision": rate(correct_proposals, proposals),
            "all_case_coverage": rate(proposals, cases),
        },
        "deferral": {
            "deferred": deferred,
            "correct": correct_deferrals,
            "unnecessary": unnecessary_deferrals,
            "denominator": deferred,
        },
        "outcomes": {
            "errors": errors,
            "unavailable": unavailable,
            "error_denominator": cases,
            "unavailable_denominator": cases,
        },
        "citation_location": {
            "valid": citation_valid,
            "invalid": citation_invalid,
            "unknown": citation_unknown,
            "denominator": citation_valid + citation_invalid,
            "validity": rate(citation_valid, citation_valid + citation_invalid),
        },
        "semantic_support": {
            "supported": supported,
            "unsupported": unsupported,
            "unknown": semantic_unknown,
            "denominator": supported + unsupported,
            "support_rate": rate(supported, supported + unsupported),
        },
        "risk_vs_coverage": {
            "unsupported_proposals": unsupported,
            "proposal_coverage": rate(proposals, cases),
            "observed_unsupported_rate": rate(unsupported, proposals),
        },
        "timing": timing,
        "provider_usage": {"input_tokens": None, "output_tokens": None, "cost_usd": None},
    }


def _check_fingerprints(
    join: ValidatedJoin,
    *,
    ranker_timing: bool = False,
    allow_unmaterialized: bool = False,
) -> None:
    for (case_id, method), result in join.results.items():
        _validate_observation(
            result,
            context=f"result {case_id}/{method}",
            method_name=method,
            ranker_timing=ranker_timing,
            allow_unmaterialized=allow_unmaterialized,
        )
        input_row = join.inputs[case_id]
        expected_input = fingerprint_value(dict(input_row))
        expected_candidates = fingerprint_value(input_row.get("candidates", []))
        expected_evidence = fingerprint_value(input_row.get("evidence", []))
        if result["input_fingerprint"] != expected_input:
            raise EvaluationValidationError(f"result {case_id}/{method} input fingerprint mismatch")
        if result["candidate_fingerprint"] != expected_candidates:
            raise EvaluationValidationError(
                f"result {case_id}/{method} candidate fingerprint mismatch"
            )
        if result["evidence_fingerprint"] != expected_evidence:
            raise EvaluationValidationError(
                f"result {case_id}/{method} evidence fingerprint mismatch"
            )


def validate_observation_parity(results: Iterable[JsonRow]) -> None:
    """Require paired methods to share the exact input/candidate/evidence/validator set."""

    by_case: defaultdict[str, list[JsonRow]] = defaultdict(list)
    for row in results:
        case_id = _required_string(row, "case_id", "result")
        by_case[case_id].append(row)
    fields = (
        "input_fingerprint",
        "candidate_fingerprint",
        "evidence_fingerprint",
        "validator_identity",
    )
    for case_id, rows in by_case.items():
        if len(rows) < 2:
            continue
        fingerprints = {tuple(row.get(field) for field in fields) for row in rows}
        if len(fingerprints) != 1:
            raise EvaluationValidationError(
                f"paired observations for {case_id!r} use different inputs or validators"
            )


def evaluate_observations(
    inputs: Iterable[JsonRow],
    labels: Iterable[JsonRow],
    results: Iterable[JsonRow],
    *,
    split: str = "validation",
    method: str | None = None,
    require_lineage: bool = True,
    ranker_timing: bool = False,
) -> JsonObject:
    """Evaluate recorded observations without executing any method."""

    join = validate_join(
        inputs,
        results,
        labels,
        split=split,
        method=method,
        require_lineage=require_lineage,
    )
    _check_fingerprints(join, ranker_timing=ranker_timing)
    for label in join.labels.values():
        _label_answerable(label)
    if len(join.methods) != 1:
        raise EvaluationValidationError(
            "evaluate_observations expects one method; use paired comparison separately"
        )
    return _metric_counts(join, join.methods[0])


def _materialize_threshold_results(
    inputs: Mapping[str, JsonRow], raw_results: Iterable[JsonRow], threshold: float
) -> list[JsonObject]:
    materialized: list[JsonObject] = []
    for raw in raw_results:
        result = dict(raw)
        status = str(result.get("status", "")).upper()
        ranked = result.get("ranked_candidates", [])
        if status in {"ERROR", "FAILED", "UNAVAILABLE"}:
            materialized.append(result)
            continue
        if not isinstance(ranked, Sequence) or isinstance(ranked, (str, bytes)) or not ranked:
            result["status"] = "DEFERRED"
            result.pop("candidate_id", None)
            result.pop("allocation", None)
            materialized.append(result)
            continue
        first = ranked[0]
        if not isinstance(first, Mapping):
            raise EvaluationValidationError("ranked_candidates entries must be objects")
        candidate_id = _required_string(first, "candidate_id", "ranked_candidates[0]")
        score = first.get("score")
        if (
            isinstance(score, bool)
            or not isinstance(score, (int, float))
            or not math.isfinite(float(score))
        ):
            raise EvaluationValidationError("ranked candidate score must be a finite number")
        if float(score) < threshold:
            result["status"] = "DEFERRED"
            result.pop("candidate_id", None)
            result.pop("allocation", None)
        else:
            candidate = _candidate_for_id(inputs[result["case_id"]], candidate_id)
            if candidate is None:
                raise EvaluationValidationError(
                    f"ranked candidate {candidate_id!r} is absent from input"
                )
            result["status"] = "PROPOSED"
            result["candidate_id"] = candidate_id
            result["allocation"] = dict(candidate)
        materialized.append(result)
    return materialized


def evaluate_ranker_thresholds(
    inputs: Iterable[JsonRow],
    labels: Iterable[JsonRow],
    ranked_results: Iterable[JsonRow],
    *,
    split: str = "validation",
    thresholds: Sequence[float] = RANKER_THRESHOLDS,
) -> dict[str, JsonObject]:
    """Produce raw operating-point metrics from one unmodified ranked trace."""

    if split != "validation":
        raise EvaluationValidationError("ranker threshold selection is validation-only")
    requested = tuple(float(value) for value in thresholds)
    if requested != RANKER_THRESHOLDS:
        raise EvaluationValidationError("v2 selection requires thresholds 0,.25,.5,.75,.9,1")
    input_rows = list(inputs)
    label_rows = list(labels)
    raw_rows = list(ranked_results)
    join = validate_join(input_rows, raw_rows, label_rows, split=split, require_lineage=True)
    if len(join.methods) != 1:
        raise EvaluationValidationError("ranker threshold selection expects one ranked method")
    _check_fingerprints(join, ranker_timing=True, allow_unmaterialized=True)
    materialized = {
        threshold: _materialize_threshold_results(join.inputs, raw_rows, threshold)
        for threshold in requested
    }
    return {
        str(threshold): evaluate_observations(
            input_rows,
            label_rows,
            rows,
            split=split,
            method=join.methods[0],
            require_lineage=True,
            ranker_timing=True,
        )
        for threshold, rows in materialized.items()
    }


def _without_hash(payload: JsonRow, field: str) -> JsonObject:
    return {key: value for key, value in payload.items() if key != field}


def _validate_manifest_metadata(manifest: JsonRow, *, split: str | None = None) -> str:
    if manifest.get("protocol") != PROTOCOL_VERSION:
        raise EvaluationValidationError("manifest protocol mismatch")
    manifest_split = manifest.get("split")
    if manifest_split not in SPLIT_NAMES or (split is not None and manifest_split != split):
        raise EvaluationValidationError("manifest split mismatch")
    manifest_hash = manifest.get("manifest_sha256")
    if (
        not isinstance(manifest_hash, str)
        or len(manifest_hash) != 64
        or fingerprint_value(_without_hash(manifest, "manifest_sha256")) != manifest_hash
    ):
        raise EvaluationValidationError("manifest hash mismatch")
    public_exposed = manifest.get("public_exposed")
    if not isinstance(public_exposed, bool):
        raise EvaluationValidationError("manifest public_exposed must be boolean")
    if public_exposed and manifest_split != "development":
        raise EvaluationValidationError("public/exposed manifests must be development-only")
    for field in ("input_sha256", "target_sha256"):
        value = manifest.get(field)
        if (
            not isinstance(value, str)
            or len(value) != 64
            or value.lower() != value
            or any(character not in "0123456789abcdef" for character in value)
        ):
            raise EvaluationValidationError(f"manifest {field} must be a SHA-256 hash")
    for field in ("input_count", "target_count"):
        value = manifest.get(field)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise EvaluationValidationError(f"manifest {field} must be a non-negative integer")
    return manifest_hash


def _selection_metric_signature(metrics: JsonObject) -> tuple[Any, ...]:
    if metrics.get("protocol") != PROTOCOL_VERSION or metrics.get("split") != "validation":
        raise EvaluationValidationError("selection metrics must be validation v2 metrics")
    method = _required_string(metrics, "method", "selection metrics").strip().lower()
    if method not in {"ranker", "shadow-ranker"}:
        raise EvaluationValidationError("selection metrics must come from a ranker")
    provenance = metrics.get("provenance")
    if not isinstance(provenance, Mapping):
        raise EvaluationValidationError("selection metrics require provenance")
    input_hash = provenance.get("input_sha256")
    target_hash = provenance.get("target_sha256")
    if (
        not isinstance(input_hash, str)
        or len(input_hash) != 64
        or input_hash.lower() != input_hash
        or any(character not in "0123456789abcdef" for character in input_hash)
        or not isinstance(target_hash, str)
        or len(target_hash) != 64
        or target_hash.lower() != target_hash
        or any(character not in "0123456789abcdef" for character in target_hash)
    ):
        raise EvaluationValidationError("selection metric provenance requires SHA-256 hashes")
    input_count = provenance.get("input_count")
    target_count = provenance.get("target_count")
    if (
        isinstance(input_count, bool)
        or not isinstance(input_count, int)
        or input_count < 0
        or isinstance(target_count, bool)
        or not isinstance(target_count, int)
        or target_count < 0
    ):
        raise EvaluationValidationError("selection metric provenance requires integer counts")
    denominators = metrics.get("denominators")
    if not isinstance(denominators, Mapping):
        raise EvaluationValidationError("selection metrics require denominators")
    invariant_denominators: list[int] = []
    for field in ("all_cases", "answerable_cases", "payment_value_centavos"):
        value = denominators.get(field)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise EvaluationValidationError(f"selection metric denominator {field} is invalid")
        invariant_denominators.append(value)
    if input_count != invariant_denominators[0] or target_count != invariant_denominators[0]:
        raise EvaluationValidationError("selection metric provenance/count denominator mismatch")
    return (
        method,
        input_hash,
        target_hash,
        input_count,
        target_count,
        *invariant_denominators,
    )


def _validate_selection_metric_shape(metrics: JsonObject) -> None:
    support = metrics.get("support")
    if not isinstance(support, Mapping):
        raise EvaluationValidationError("selection metrics require support counts")
    support_counts: list[int] = []
    for field in ("supported", "unsupported", "unknown", "denominator"):
        value = support.get(field)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise EvaluationValidationError(f"selection metric support count {field} is invalid")
        support_counts.append(value)
    if support_counts[3] != support_counts[0] + support_counts[1]:
        raise EvaluationValidationError("selection metric support denominator mismatch")
    denominators = metrics.get("denominators")
    if not isinstance(denominators, Mapping):
        raise EvaluationValidationError("selection metrics require denominators")
    proposal = metrics.get("proposal")
    if not isinstance(proposal, Mapping):
        raise EvaluationValidationError("selection metrics require proposal counts")
    proposal_count = proposal.get("proposals")
    coverage = proposal.get("all_case_coverage")
    if (
        isinstance(proposal_count, bool)
        or not isinstance(proposal_count, int)
        or proposal_count < 0
        or not isinstance(coverage, (int, float))
        or isinstance(coverage, bool)
        or not math.isfinite(float(coverage))
    ):
        raise EvaluationValidationError("selection metric proposal counts are invalid")
    all_cases = denominators.get("all_cases")
    denominator_proposals = denominators.get("proposals")
    if (
        isinstance(all_cases, bool)
        or not isinstance(all_cases, int)
        or proposal_count > all_cases
        or denominator_proposals != proposal_count
        or float(coverage) < 0
        or float(coverage) > 1
        or float(coverage) != (proposal_count / all_cases if all_cases else 0.0)
        or support_counts[3] + support_counts[2] != proposal_count
        or support_counts[2] > proposal_count
    ):
        raise EvaluationValidationError(
            "selection metric proposal counts or denominator are inconsistent"
        )


def freeze_selection_artifact(
    threshold_metrics: Mapping[str, JsonObject], *, manifest: JsonRow | None = None
) -> JsonObject:
    """Freeze validation-only threshold selection for a later, explicit run."""

    if manifest is None or manifest.get("split") != "validation":
        raise EvaluationValidationError("selection requires a verified validation manifest")
    manifest_hash = _validate_manifest_metadata(manifest, split="validation")
    expected_thresholds = {str(threshold) for threshold in RANKER_THRESHOLDS}
    if set(threshold_metrics) != expected_thresholds:
        raise EvaluationValidationError("selection requires every v2 ranker threshold")
    eligible: list[tuple[float, JsonObject]] = []
    signatures: list[tuple[Any, ...]] = []
    for threshold_text, metrics in threshold_metrics.items():
        threshold = float(threshold_text)
        if threshold not in RANKER_THRESHOLDS:
            raise EvaluationValidationError(f"unexpected ranker threshold {threshold_text!r}")
        signatures.append(_selection_metric_signature(metrics))
        _validate_selection_metric_shape(metrics)
    first_signature = signatures[0]
    if any(signature != first_signature for signature in signatures[1:]):
        raise EvaluationValidationError(
            "selection thresholds disagree on method, provenance, or denominators"
        )
    _, input_hash, target_hash, input_count, target_count, *_ = first_signature
    if (
        input_hash != manifest.get("input_sha256")
        or target_hash != manifest.get("target_sha256")
        or input_count != manifest.get("input_count")
        or target_count != manifest.get("target_count")
    ):
        raise EvaluationValidationError("selection metrics do not match validation manifest")
    for threshold_text, metrics in threshold_metrics.items():
        threshold = float(threshold_text)
        support = cast(Mapping[str, Any], metrics["support"])
        proposal = cast(Mapping[str, Any], metrics["proposal"])
        proposals = cast(int, proposal["proposals"])
        if proposals > 0 and support["unsupported"] == 0 and support["unknown"] == 0:
            eligible.append((threshold, metrics))
    selected: float | None
    if eligible:
        selected = max(
            eligible,
            key=lambda item: (
                cast(Mapping[str, Any], item[1]["proposal"])["all_case_coverage"],
                item[0],
            ),
        )[0]
        fallback = None
    else:
        selected = None
        fallback = "defer_all"
    payload: JsonObject = {
        "protocol": PROTOCOL_VERSION,
        "selection_split": "validation",
        "manifest_sha256": manifest_hash,
        "thresholds": list(RANKER_THRESHOLDS),
        "threshold_metrics": dict(threshold_metrics),
        "selected_threshold": selected,
        "fallback": fallback,
    }
    payload["selection_sha256"] = fingerprint_value(payload)
    return payload


def verify_selection_artifact(artifact: JsonRow) -> None:
    expected = artifact.get("selection_sha256")
    if (
        not isinstance(expected, str)
        or fingerprint_value(_without_hash(artifact, "selection_sha256")) != expected
    ):
        raise EvaluationValidationError("selection artifact hash mismatch")
    if artifact.get("selection_split") != "validation":
        raise EvaluationValidationError("selection artifact is not validation-only")


def freeze_manifest(
    inputs: Iterable[JsonRow],
    targets: Iterable[JsonRow],
    *,
    split: str,
    public_exposed: bool = False,
) -> JsonObject:
    """Create a manifest binding protocol, split, row counts, and input/target hashes."""

    if split not in SPLIT_NAMES:
        raise EvaluationValidationError(f"unknown evaluation split {split!r}")
    input_rows = list(inputs)
    target_rows = list(targets)
    validate_lineage(input_rows, require_explicit=True)
    validate_join(input_rows, [], target_rows, split=split, require_lineage=False)
    if public_exposed and split != "development":
        raise EvaluationValidationError("only development manifests may be public/exposed")
    manifest: JsonObject = {
        "protocol": PROTOCOL_VERSION,
        "split": split,
        "public_exposed": public_exposed,
        "input_count": len(input_rows),
        "target_count": len(target_rows),
        "input_sha256": fingerprint_rows(input_rows),
        "target_sha256": fingerprint_rows(target_rows),
    }
    manifest["manifest_sha256"] = fingerprint_value(manifest)
    return manifest


def verify_manifest(
    manifest: JsonRow, inputs: Iterable[JsonRow], targets: Iterable[JsonRow]
) -> None:
    """Verify that current rows still match a previously frozen manifest."""

    _validate_manifest_metadata(manifest)
    input_rows = list(inputs)
    target_rows = list(targets)
    if manifest.get("input_count") != len(input_rows) or manifest.get("target_count") != len(
        target_rows
    ):
        raise EvaluationValidationError("manifest row count mismatch")
    if manifest.get("input_sha256") != fingerprint_rows(input_rows) or manifest.get(
        "target_sha256"
    ) != fingerprint_rows(target_rows):
        raise EvaluationValidationError("manifest input or target hash mismatch")


def _read_jsonl(path: Path) -> list[JsonObject]:
    rows: list[JsonObject] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise EvaluationValidationError(f"invalid JSONL at {path}:{line_number}") from exc
            if not isinstance(value, dict):
                raise EvaluationValidationError(
                    f"JSONL row at {path}:{line_number} must be an object"
                )
            rows.append(value)
    return rows


def _reserve_final_ledger(ledger_path: Path, manifest_hash: str) -> None:
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    payload = _canonical_bytes({"protocol": PROTOCOL_VERSION, "manifest_sha256": manifest_hash})
    try:
        descriptor = os.open(ledger_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise FinalAccessError("reserved-final access ledger has already been used") from exc
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
    except BaseException:
        try:
            ledger_path.unlink()
        except FileNotFoundError:
            pass
        raise


def read_split(
    input_path: Path,
    target_path: Path,
    *,
    split: str,
    manifest: JsonRow,
    authorize_final: bool = False,
    manifest_hash: str | None = None,
    ledger_path: Path | None = None,
) -> tuple[list[JsonObject], list[JsonObject]]:
    """Open one split, guarding reserved-final before either file is opened."""

    if split not in SPLIT_NAMES:
        raise EvaluationValidationError(f"unknown evaluation split {split!r}")
    # Validate the frozen manifest itself before touching the input, target, or
    # one-time ledger paths.  A forged self-hash must fail before file access.
    expected_hash = _validate_manifest_metadata(manifest, split=split)
    if split == FINAL_SPLIT:
        if not authorize_final:
            raise FinalAccessError("reserved-final access requires explicit authorization")
        if manifest_hash != expected_hash:
            raise FinalAccessError("reserved-final access requires the frozen manifest hash")
        if ledger_path is None:
            raise FinalAccessError("reserved-final access requires a one-time ledger path")
        _reserve_final_ledger(ledger_path, expected_hash)
    inputs = _read_jsonl(input_path)
    targets = _read_jsonl(target_path)
    verify_manifest(manifest, inputs, targets)
    return inputs, targets


__all__ = [
    "FINAL_SPLIT",
    "EvaluationValidationError",
    "FinalAccessError",
    "RANKER_THRESHOLDS",
    "SPLIT_NAMES",
    "ValidatedJoin",
    "allocation_key",
    "allocation_matches",
    "evaluate_observations",
    "evaluate_ranker_thresholds",
    "fingerprint_rows",
    "fingerprint_value",
    "freeze_manifest",
    "freeze_selection_artifact",
    "read_split",
    "sha256_bytes",
    "stable_split",
    "validate_allocation",
    "validate_join",
    "validate_lineage",
    "validate_observation_parity",
    "verify_manifest",
    "verify_selection_artifact",
]
