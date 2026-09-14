"""Independent, denominator-first evaluation for candidate ranking."""

from __future__ import annotations

import argparse
import json
import math
import os
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from datetime import date
from pathlib import Path
from time import perf_counter_ns
from typing import Any

from .features import candidate_features, score_classifier


def _domain_records(group: Mapping[str, Any]) -> tuple[Any, tuple[Any, ...], tuple[Any, ...]]:
    """Convert the input-only JSONL shape into the rules engine's records."""

    from reconcile.domain.types import CreditFact, InvoiceFact, PaymentFact

    payment = group.get("payment")
    payment = payment if isinstance(payment, Mapping) else {}
    group_id = str(group.get("group_id", ""))
    payment_record = PaymentFact(
        source_account_id="ml-evaluator",
        transaction_id=group_id,
        booking_date=date.fromisoformat(str(payment.get("booking_date"))),
        payer_name=str(payment.get("payer_name", "")),
        reference=str(payment.get("reference", "")),
        amount=int(payment.get("amount", 0)),
        customer_id=(str(payment["customer_id"]) if payment.get("customer_id") else None),
    )
    invoices: list[Any] = []
    raw_invoices = group.get("invoices", ())
    if isinstance(raw_invoices, Sequence) and not isinstance(raw_invoices, str):
        for invoice in raw_invoices:
            if not isinstance(invoice, Mapping):
                continue
            invoices.append(
                InvoiceFact(
                    customer_id=str(invoice.get("customer_id", "")),
                    invoice_id=str(invoice.get("invoice_id", "")),
                    customer_name=str(invoice.get("customer_name", "")),
                    issued_date=date.fromisoformat(str(invoice.get("issued_date"))),
                    due_date=date.fromisoformat(str(invoice.get("due_date"))),
                    balance_as_of=date.fromisoformat(str(invoice.get("balance_as_of"))),
                    outstanding_amount=int(invoice.get("outstanding_amount", 0)),
                )
            )
    credits: list[Any] = []
    credit = group.get("credit")
    if isinstance(credit, Mapping):
        credits.append(
            CreditFact(
                customer_id=str(credit.get("customer_id", "")),
                credit_note_id=str(credit.get("credit_note_id", "")),
                balance_as_of=date.fromisoformat(str(credit.get("balance_as_of"))),
                available_amount=int(credit.get("available_amount", 0)),
                invoice_id=(str(credit["invoice_id"]) if credit.get("invoice_id") else None),
            )
        )
    return payment_record, tuple(invoices), tuple(credits)


def _candidate_allocation_key(candidate: Mapping[str, Any]) -> tuple[Any, ...]:
    def line_key(name: str, fields: tuple[str, ...]) -> tuple[tuple[Any, ...], ...]:
        raw = candidate.get(name, ())
        if not isinstance(raw, Sequence) or isinstance(raw, str):
            return ()
        rows: list[tuple[Any, ...]] = []
        for line in raw:
            if isinstance(line, Mapping):
                rows.append(
                    tuple(line.get(field) for field in fields) + (int(line.get("amount", 0)),)
                )
        return tuple(sorted(rows))

    raw_ids = candidate.get("invoice_ids", ())
    invoice_ids = (
        tuple(sorted(str(value) for value in raw_ids))
        if isinstance(raw_ids, Sequence) and not isinstance(raw_ids, str)
        else ()
    )
    return (
        invoice_ids,
        line_key("cash", ("invoice_id",)),
        line_key("credits", ("credit_note_id", "invoice_id")),
    )


def _proposal_key(proposal: Any) -> tuple[Any, ...]:
    invoice_ids = tuple(sorted({line.invoice_id for line in (*proposal.cash, *proposal.credits)}))
    return (
        invoice_ids,
        tuple(sorted((line.invoice_id, line.amount) for line in proposal.cash)),
        tuple(
            sorted((line.credit_note_id, line.invoice_id, line.amount) for line in proposal.credits)
        ),
    )


def rules_scores(groups: Iterable[Mapping[str, Any]]) -> dict[str, list[float]]:
    """Score only candidates exactly enumerated by deterministic rules."""

    from reconcile.domain.matching import propose
    from reconcile.domain.types import ProposalStatus

    result: dict[str, list[float]] = {}
    for group in groups:
        payment, invoices, credits = _domain_records(group)
        proposal = propose(payment, invoices, credits, {"message": str(group.get("message", ""))})
        candidates = group.get("candidates", ())
        if proposal.status != ProposalStatus.PROPOSED or not isinstance(candidates, Sequence):
            result[str(group.get("group_id", ""))] = []
            continue
        proposed_key = _proposal_key(proposal)
        result[str(group.get("group_id", ""))] = [
            1.0 if _candidate_allocation_key(candidate) == proposed_key else 0.0
            for candidate in candidates
            if isinstance(candidate, Mapping)
        ]
    return result


def evaluate_rules(
    groups: Iterable[Mapping[str, Any]],
    targets: Iterable[Mapping[str, Any]],
    *,
    split: str = "validation",
) -> dict[str, Any]:
    """Evaluate rules-v1; unsupported/unmatched proposals are abstentions."""

    group_rows = _groups(groups)
    return evaluate_scores(
        group_rows,
        targets,
        rules_scores(group_rows),
        proposal_threshold=0.5,
        split=split,
    )


def guard_evaluation_split(split: str) -> None:
    """Require an explicit operator opt-in before any sealed split is read."""

    normalized = split.strip().lower().replace("_", "-")
    if normalized in {"final", "final-test", "sealed", "challenge-test", "challenge-sealed"}:
        if os.getenv("ALLOW_SEALED_EVAL") != "1":
            raise RuntimeError(
                "sealed evaluation requires ALLOW_SEALED_EVAL=1"
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
    answer_bearing_count = sum(
        bool(_acceptable(targets.get(str(group.get("group_id", "")), {})))
        or _expected_allocation(targets.get(str(group.get("group_id", "")), {})) is not None
        for group in groups
    )

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
        payment = group.get("payment")
        try:
            payment_value = (
                abs(int(payment.get("amount", 0))) if isinstance(payment, Mapping) else 0
            )
        except (TypeError, ValueError):
            payment_value = 0
        value_denominator += payment_value
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
        if should_propose and not winner_correct:
            incorrect_value += payment_value
        expected = _expected_allocation(target)
        answer_bearing = bool(acceptable)
        if expected is not None or answer_bearing:
            exact_allocation_correct += int(
                _allocation_key(winner) == expected if expected is not None else winner_correct
            )

    exact_allocation_total = answer_bearing_count

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
            "answer_bearing_groups": answer_bearing_count,
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
            "proposals": proposals,
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
        result[str(group.get("group_id", ""))] = score_classifier(model, rows)
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


def evaluate_development(root: Path = Path("data/generated/ml-v1")) -> dict[str, Any]:
    """Evaluate the verified artifact on validation and development challenge data only."""

    guard_evaluation_split("validation")
    guard_evaluation_split("development")
    from .artifact import load_artifact
    from .train import read_jsonl

    loaded = load_artifact()
    validation_groups = read_jsonl(root / "inputs/validation.jsonl")
    validation_targets = read_jsonl(root / "targets/validation.jsonl")
    development_groups = read_jsonl(root / "challenge/inputs/development.jsonl")
    development_targets = read_jsonl(root / "challenge/targets/development.jsonl")
    return {
        "artifact": {
            "model_id": loaded.model_id,
            "model_version": loaded.model_version,
        },
        "validation": evaluate(loaded.model, validation_groups, validation_targets),
        "development": evaluate(loaded.model, development_groups, development_targets),
        "rules_validation": evaluate_rules(validation_groups, validation_targets),
        "rules_development": evaluate_rules(development_groups, development_targets),
    }


evaluate_dev = evaluate_development


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Evaluate the verified ranker on validation and development data"
    )
    parser.add_argument("--root", type=Path, default=Path("data/generated/ml-v1"))
    parser.add_argument("--report", type=Path, default=Path("reports/ml-v1/development.json"))
    args = parser.parse_args()
    payload = {
        "scope": "synthetic-agent-generated-not-domain-validated",
        **evaluate_development(args.root),
    }
    rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(rendered, encoding="utf-8")
    print(rendered, end="")


if __name__ == "__main__":
    main()
