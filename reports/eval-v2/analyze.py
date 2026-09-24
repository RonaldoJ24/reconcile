"""Post-scoring analysis of the v2 run: per-category outcomes and every wrong proposal.

This is descriptive and post hoc. It was written after the results were known and is
not part of the registered protocol; the registered metrics live in `run/report.json`.

Usage: python3 reports/eval-v2/analyze.py reports/eval-v2/run

It reads only published run outputs and refuses to run before `score --final` has
written the one-time ledger, so it cannot be used to peek at reserved-final labels.
"""

import collections
import json
import sys
from pathlib import Path

run = Path(sys.argv[1])
if not (run / "final-access.ledger").exists():
    raise SystemExit("score --final has not run; refusing to read the final labels")


def rows(name):
    path = run / name
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


meta = {row["case_id"]: row for row in rows("meta.jsonl")}
rules = {row["case_id"]: row for row in rows("rules.jsonl")}
direct = {row["case_id"]: row for row in rows("direct.jsonl")}
inputs = {
    row["case_id"]: row
    for name in ("development", "validation", "reserved-final", "unreachable")
    for row in rows(f"inputs-{name}.jsonl")
}
expected = {}
for split in ("development", "validation", "reserved-final"):
    for label in rows(f"labels-{split}.jsonl"):
        expected[label["case_id"]] = label.get("expected_allocation")
for row in rows("inputs-unreachable.jsonl"):
    expected[row["case_id"]] = row["expected_allocation"]


def lines(allocation):
    if not allocation:
        return None
    cash = tuple(
        sorted((line["invoice_id"], line["amount"]) for line in allocation.get("cash", []))
    )
    credits = tuple(
        sorted(
            (line["credit_note_id"], line["invoice_id"], line["amount"])
            for line in allocation.get("credits", [])
        )
    )
    return cash, credits


def decision(case_id):
    return rules[case_id] if rules[case_id]["status"] == "PROPOSED" else direct.get(case_id)


def provider_called(case_id):
    row = direct.get(case_id)
    return bool(row and row["recording"]["outcome"].get("provider_called"))


def outcome(case_id):
    row = decision(case_id)
    status = (row or {}).get("status", "UNAVAILABLE")
    info = meta[case_id]
    if status == "PROPOSED":
        right = info["answerable"] and lines(expected.get(case_id)) == lines(row.get("allocation"))
        return "right" if right else "wrong"
    if status in {"NEEDS_REVIEW", "DEFERRED"}:
        if not info["answerable"]:
            return "deferred_correctly"
        return "deferred_unreachable" if not info["reachable"] else "deferred_reachable"
    return "unavailable"


def cause(case_id, chosen, target):
    # Every wrong proposal is classified by why the selector should have abstained.
    info = meta[case_id]
    if not info["answerable"]:
        return "unanswerable case"
    if info["reachable"]:
        return "wrong candidate while the correct one was available"
    if target and chosen and chosen[0] == target[0] and target[1] and not chosen[1]:
        return "correct cash, credit note missing from the candidates"
    if target and len(target[0]) > len(chosen[0]):
        return "allocation spans more invoices than the chosen candidate"
    return "correct invoice missing from the candidates"


by_category = collections.defaultdict(collections.Counter)
by_split = collections.defaultdict(collections.Counter)
errors = []
missed = collections.Counter()
reasons = collections.Counter()
direct_answerable_reachable = collections.Counter()
# Answerable cases the rules sent on, and what the model did with the reachable ones.
after_rules = {"all-cases": collections.Counter(), "reserved-final": collections.Counter()}
seen_reachable = collections.Counter()
for case_id, info in sorted(meta.items()):
    result = outcome(case_id)
    for counter in (by_category[info["category"]], by_split[info["split"]]):
        counter[result] += 1
        counter["cases"] += 1
    if case_id in direct:
        reasons[
            direct[case_id]["recording"]["outcome"].get("reason_code") or "no provider call"
        ] += 1
    if result.startswith("deferred_") and info["answerable"]:
        missed["answerable deferrals"] += 1
        if provider_called(case_id):
            missed["model abstained"] += 1
        elif not inputs[case_id]["evidence"]:
            missed["no note, so no provider call"] += 1
        else:
            missed["note but no candidate, so no provider call"] += 1
    row = decision(case_id)
    if rules[case_id]["status"] != "PROPOSED" and (row or {}).get("status") == "PROPOSED":
        if info["answerable"] and info["reachable"]:
            direct_answerable_reachable["proposals"] += 1
            direct_answerable_reachable["correct"] += result == "right"
    if rules[case_id]["status"] != "PROPOSED" and info["answerable"]:
        for scope in ("all-cases", info["split"]):
            if scope in after_rules:
                after_rules[scope]["answerable"] += 1
                after_rules[scope]["resolved"] += result == "right"
        if info["reachable"] and provider_called(case_id):
            seen_reachable["cases"] += 1
            seen_reachable["proposed"] += (row or {}).get("status") == "PROPOSED"
            seen_reachable["correct"] += result == "right"
    if result == "wrong":
        chosen, target = lines(row.get("allocation")), lines(expected.get(case_id))
        reason = cause(case_id, chosen, target)
        errors.append(
            {
                "case_id": case_id,
                "split": info["split"],
                "category": info["category"],
                "answerable": info["answerable"],
                "reachable": info["reachable"],
                "cause": reason,
                "payment_centavos": info["payment_amount"],
                "unapplied_credit_centavos": (
                    sum(line[2] for line in target[1])
                    if reason.startswith("correct cash")
                    else None
                ),
                "chosen": chosen,
                "expected": target,
            }
        )

summary = {
    "by_category": {name: dict(counts) for name, counts in sorted(by_category.items())},
    "by_split": {name: dict(counts) for name, counts in sorted(by_split.items())},
    "after_rules": {scope: dict(counts) for scope, counts in after_rules.items()},
    "direct_seen_reachable": dict(seen_reachable),
    "direct_on_answerable_reachable": dict(direct_answerable_reachable),
    "direct_reason_codes": dict(reasons),
    "missed_answerable": dict(missed),
    "wrong_proposals": errors,
    "wrong_by_cause": dict(collections.Counter(error["cause"] for error in errors)),
    "misallocated_centavos": sum(error["payment_centavos"] for error in errors),
}
(run / "analysis.json").write_text(
    json.dumps(summary, indent=2, sort_keys=True, default=str) + "\n"
)
print(json.dumps(summary, indent=2, sort_keys=True, default=str))
