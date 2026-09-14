"""Independent, denominator-first evaluation for candidate ranking."""

from __future__ import annotations

import math
import os
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from time import perf_counter_ns
from typing import Any

from .features import candidate_features


def guard_evaluation_split(split: str) -> None:
    """Require an explicit operator opt-in before any sealed split is read."""

    normalized = split.strip().lower().replace("_", "-")
    if normalized in {"final", "final-test", "sealed", "challenge-test", "challenge-sealed"}:
        if os.getenv("ALLOW_SEALED_EVAL") != "1":
            raise RuntimeError(
                "sealed evaluation requires ALLOW_SEALED_EVAL=1; Phase 3 does not run it"
            )


def _groups(items: Iterable[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    return list(items)


def _target_map(targets: Iterable[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    return {
        str(target["group_id"]): target
        for target in targets
        if isinstance(target, Mapping) and target.get("group_id") is not None
    }


def _acceptable(target: Mapping[str, Any]) -> set[str]:
    raw = target.get("acceptable_candidate_ids", ())
    if not isinstance(raw, Sequence) or isinstance(raw, str):
        return set()
    return {str(value) for value in raw}


def _family(target: Mapping[str, Any]) -> str:
    for key in ("family", "scenario_family", "family_name"):
        value = target.get(key)
        if isinstance(value, str) and value:
            return value
    metadata = target.get("family_metadata")
    if isinstance(metadata, Mapping):
        value = metadata.get("family") or metadata.get("name")
        if isinstance(value, str) and value:
            return value
    return "unknown"


def _candidate_id(candidate: Mapping[str, Any]) -> str:
    return str(candidate.get("candidate_id", ""))


def _scores_for_group(
    group: Mapping[str, Any],
    score_values: Sequence[float],
) -> list[tuple[str, float, Mapping[str, Any]]]:
    candidates = group.get("candidates", ())
    if not isinstance(candidates, Sequence) or isinstance(candidates, (str, bytes)):
        return []
    rows = [candidate for candidate in candidates if isinstance(candidate, Mapping)]
    return [
        (_candidate_id(candidate), float(score), candidate)
        for candidate, score in zip(rows, score_values)
    ]


def _allocation_key(candidate: Mapping[str, Any]) -> tuple[Any, ...]:
    def lines(key: str) -> tuple[tuple[str, int], ...]:
        raw = candidate.get(key, ())
        if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
            return ()
        result: list[tuple[str, int]] = []
        for line in raw:
            if isinstance(line, Mapping):
                try:
                    line_id = str(line.get("invoice_id", line.get("credit_note_id", "")))
                    result.append((line_id, int(line.get("amount", 0))))
                except (TypeError, ValueError):
                    continue
        return tuple(sorted(result))

    invoice_ids = candidate.get("invoice_ids", ())
    normalized_ids = (
        tuple(sorted(str(value) for value in invoice_ids))
        if isinstance(invoice_ids, Sequence) and not isinstance(invoice_ids, str)
        else ()
    )
    return normalized_ids, lines("cash"), lines("credits")


def _expected_allocation(target: Mapping[str, Any]) -> tuple[Any, ...] | None:
    value = target.get("acceptable_allocations")
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or not value:
        return None
    # A target may provide a single allocation object or a list of objects.
    first = value[0]
    if isinstance(first, Mapping):
        return _allocation_key(first)
    return None


def _evaluate_rows(
    groups: list[Mapping[str, Any]],
    targets: Mapping[str, Mapping[str, Any]],
    score_map: Mapping[str, Sequence[float]],
    *,
    proposal_threshold: float | None,
) -> dict[str, Any]:
    group_count = len(groups)
    candidate_count = 0
    retrieved = 0
    retrieval_total = 0
    ranked_total = 0
    ranked_correct = 0
    proposals = 0
    correct_proposals = 0
    abstentions = 0
    underdetermined = 0
    abstained_underdetermined = 0
    exact_allocation_correct = 0
    exact_allocation_total = 0
    incorrect_value = 0
    value_denominator = 0
    latencies: list[float] = []

    for group in groups:
        group_id = str(group.get("group_id", ""))
        target = targets.get(group_id, {})
        acceptable = _acceptable(target)
        candidates = group.get("candidates", ())
        candidate_count += (
            len(candidates)
            if isinstance(candidates, Sequence) and not isinstance(candidates, str)
            else 0
        )
        required = target.get("retrieved_candidate_ids", target.get("required_candidate_ids"))
        if not isinstance(required, Sequence) or isinstance(required, str):
            required = acceptable
        required_set = {str(value) for value in required}
        if required_set:
            retrieval_total += 1
            candidate_ids = {
                _candidate_id(candidate)
                for candidate in candidates
                if isinstance(candidate, Mapping)
            }
            retrieved += int(required_set <= candidate_ids)

        started = perf_counter_ns()
        scored = _scores_for_group(group, score_map.get(group_id, ()))
        latencies.append((perf_counter_ns() - started) / 1_000_000)
        if not scored:
            abstentions += 1
            if not acceptable:
                underdetermined += 1
                abstained_underdetermined += 1
            continue
        ranked_total += 1
        ranked = sorted(scored, key=lambda row: (-row[1], row[0]))
        winner_id, winner_score, winner = ranked[0]
        winner_correct = winner_id in acceptable
        ranked_correct += int(winner_correct)
        should_propose = proposal_threshold is None or winner_score >= proposal_threshold
        if should_propose:
            proposals += 1
            correct_proposals += int(winner_correct)
        else:
            abstentions += 1
        if not acceptable:
            underdetermined += 1
            abstained_underdetermined += int(not should_propose)
        payment = group.get("payment")
        try:
            payment_value = (
                abs(int(payment.get("amount", 0))) if isinstance(payment, Mapping) else 0
            )
        except (TypeError, ValueError):
            payment_value = 0
        value_denominator += payment_value
        if should_propose and not winner_correct:
            incorrect_value += payment_value
        expected = _expected_allocation(target)
        if expected is not None:
            exact_allocation_total += 1
            exact_allocation_correct += int(_allocation_key(winner) == expected)

    latency = {
        "count": len(latencies),
        "mean_ms": sum(latencies) / len(latencies) if latencies else 0.0,
        "p95_ms": sorted(latencies)[max(0, math.ceil(len(latencies) * 0.95) - 1)]
        if latencies
        else 0.0,
    }
    return {
        "denominators": {
            "groups": group_count,
            "candidates": candidate_count,
            "ranked_groups": ranked_total,
            "proposals": proposals,
            "retrieval_groups": retrieval_total,
            "exact_allocation_groups": exact_allocation_total,
            "payment_value": value_denominator,
        },
        "retrieval": {
            "candidate_recall": retrieved / retrieval_total if retrieval_total else None,
            "numerator": retrieved,
            "denominator": retrieval_total,
        },
        "ranking": {
            "top1_accuracy": ranked_correct / ranked_total if ranked_total else None,
            "numerator": ranked_correct,
            "denominator": ranked_total,
        },
        "exact_allocation": {
            "accuracy": exact_allocation_correct / exact_allocation_total
            if exact_allocation_total
            else None,
            "numerator": exact_allocation_correct,
            "denominator": exact_allocation_total,
        },
        "proposal": {
            "precision": correct_proposals / proposals if proposals else None,
            "coverage": proposals / group_count if group_count else None,
            "abstention_rate": abstentions / group_count if group_count else None,
            "abstentions": abstentions,
            "underdetermined_groups": underdetermined,
            "abstained_underdetermined": abstained_underdetermined,
        },
        "incorrectly_allocated_value": {
            "amount": incorrect_value,
            "denominator": value_denominator,
            "rate": incorrect_value / value_denominator if value_denominator else None,
        },
        "latency_ms": latency,
        "candidate_recall": retrieved / retrieval_total if retrieval_total else None,
        "ranking_accuracy": ranked_correct / ranked_total if ranked_total else None,
        "precision": correct_proposals / proposals if proposals else None,
        "coverage": proposals / group_count if group_count else None,
        "abstention": abstentions / group_count if group_count else None,
        "incorrect_value": incorrect_value,
        "latency": latency,
    }


def evaluate_scores(
    groups: Iterable[Mapping[str, Any]],
    targets: Iterable[Mapping[str, Any]],
    scores: Mapping[str, Sequence[float]],
    *,
    proposal_threshold: float | None = None,
    split: str = "validation",
) -> dict[str, Any]:
    """Evaluate supplied raw ranking scores without treating them as probabilities."""

    guard_evaluation_split(split)
    group_rows = _groups(groups)
    target_rows = _target_map(targets)
    overall = _evaluate_rows(group_rows, target_rows, scores, proposal_threshold=proposal_threshold)
    by_family: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for group in group_rows:
        by_family[_family(target_rows.get(str(group.get("group_id", "")), {}))].append(group)
    overall["by_family"] = {
        family: _evaluate_rows(
            family_groups,
            target_rows,
            scores,
            proposal_threshold=proposal_threshold,
        )
        for family, family_groups in sorted(by_family.items())
    }
    return overall


def model_scores(model: Any, groups: Iterable[Mapping[str, Any]]) -> dict[str, list[float]]:
    """Generate raw classifier ranking scores using the shared feature transform."""

    result: dict[str, list[float]] = {}
    for group in groups:
        candidates = group.get("candidates", ())
        if not isinstance(candidates, Sequence) or isinstance(candidates, (str, bytes)):
            result[str(group.get("group_id", ""))] = []
            continue
        rows = [
            candidate_features(group, candidate)
            for candidate in candidates
            if isinstance(candidate, Mapping)
        ]
        if not rows:
            result[str(group.get("group_id", ""))] = []
            continue
        if hasattr(model, "decision_function"):
            raw = model.decision_function(rows)
        else:
            raw = model.predict(rows)
        result[str(group.get("group_id", ""))] = [float(value) for value in raw]
    return result


def evaluate(
    model: Any,
    groups: Iterable[Mapping[str, Any]],
    targets: Iterable[Mapping[str, Any]],
    *,
    proposal_threshold: float | None = None,
    split: str = "validation",
) -> dict[str, Any]:
    """Evaluate a fitted model independently from training/model selection."""

    group_rows = _groups(groups)
    return evaluate_scores(
        group_rows,
        targets,
        model_scores(model, group_rows),
        proposal_threshold=proposal_threshold,
        split=split,
    )
