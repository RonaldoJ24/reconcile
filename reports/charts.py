"""Draw the evaluation charts used by the README, the reports and the landing page.

Usage: python3 reports/charts.py

Every figure comes from published run outputs (`report.json` and `analysis.json`);
none is typed by hand. Charts get shared without the text around them, so each one
carries its own sample sizes and a synthetic-data footnote. Runs on the same cases
are listed in `RUNS`; another run becomes another bar.
"""

import json
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs/images/results"

WIDTH = 760
FONT = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif"
NAVY, INK, MUTED, LINE, TRACK = "#112d4e", "#183047", "#5c7084", "#dbe3ea", "#eef2f5"
BEFORE = "#b3c1cd"
RIGHT, WRONG, DEFERRED, MISSED = "#1f7a4d", "#c2413b", "#7f9bb3", "#dfe6ec"
UNAVAILABLE = "#d9a441"
SOURCE = "Synthetic results, not real-world accuracy. Source: github.com/RonaldoJ24/reconcile"

# Runs on the same 178 cases, oldest first. The last one is the model the product uses.
RUNS = [
    {
        "key": "deepseek",
        "name": "DeepSeek",
        "color": "#8fb0d0",
        "path": ROOT / "reports/eval-v2/run",
        "footnote": "DeepSeek run, 2026-09-23: pre-registered, 178 synthetic cases "
        "written by AI agents, run once.",
    },
    {
        "key": "gpt-6-luna",
        "name": "GPT-6 Luna",
        "color": "#1a65a8",
        "path": ROOT / "reports/eval-v2-openai/run",
        "footnote": "GPT-6 Luna run, 2026-09-23: the same 178 AI-written cases, "
        "after they were public.",
    },
]
for run in RUNS:
    run["report"] = json.loads((run["path"] / "report.json").read_text())
    run["analysis"] = json.loads((run["path"] / "analysis.json").read_text())
CURRENT = RUNS[-1]
COMPARISON_FOOTNOTE = (
    "Same 178 synthetic cases written by AI agents. DeepSeek run pre-registered; "
    "GPT-6 Luna run after the cases were public.",
    SOURCE,
)
SCOPES = [("reserved-final", "Held-out split"), ("all-cases", "All cases")]
CATEGORY_NAMES = {
    "abbreviated_folios": "Abbreviated invoice numbers",
    "ambiguous": "Ambiguous, no single answer",
    "conflicting_instructions": "Conflicting instructions",
    "credit_note_netting": "Credit note netted",
    "descriptive_reference": "Described, not numbered",
    "exact_folio": "Exact invoice number",
    "more_than_three_invoices": "More than three invoices",
    "overpayment": "Overpayment",
    "partial_across_invoices": "Partial across invoices",
    "partial_payment": "Partial payment",
    "prompt_injection": "Hidden instructions",
    "short_payment_fee": "Short by a bank fee",
    "typos_and_formats": "Typos and odd formats",
    "wrong_payer_or_customer": "Wrong payer or customer",
}
CAUSE_NAMES = {
    "correct cash, credit note missing from the candidates": (
        "Cash on the right invoice, but the credit note was never offered"
    ),
    "allocation spans more invoices than the chosen candidate": (
        "The payment covered more invoices than the proposal"
    ),
    "unanswerable case": "No right answer existed",
    "correct invoice missing from the candidates": "The right invoice was never retrieved",
    "wrong candidate while the correct one was available": (
        "Chose wrong while the right allocation was offered"
    ),
}


def end_to_end(run, scope, method):
    return run["report"]["end_to_end"][scope][method]


# The rules and the ranker do not depend on the model, so every run must agree on them.
for run in RUNS[1:]:
    for scope, _ in SCOPES:
        for method in ("rules", "ranker_excluding_validation"):
            if end_to_end(run, scope, method) != end_to_end(RUNS[0], scope, method):
                raise SystemExit(f"{run['name']} disagrees on {method} for {scope}")


def text(x, y, value, *, size=15, weight=400, fill=INK, anchor="start", halo=False):
    # A white halo keeps labels readable where they cross gridlines.
    outline = ' stroke="#ffffff" stroke-width="4" paint-order="stroke"' if halo else ""
    return (
        f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" font-weight="{weight}" '
        f'fill="{fill}" text-anchor="{anchor}"{outline}>{escape(str(value))}</text>'
    )


def rect(x, y, width, height, fill, radius=0):
    return (
        f'<rect x="{x:.1f}" y="{y:.1f}" width="{max(width, 0):.1f}" height="{height:.1f}" '
        f'rx="{radius}" fill="{fill}"/>'
    )


def header(title, subtitle):
    return [
        text(32, 46, title, size=23, weight=700, fill=NAVY),
        text(32, 72, subtitle, size=14, fill=MUTED),
    ]


def legend(y, items):
    parts, x = [], 32
    for label, color in items:
        parts.append(rect(x, y - 11, 13, 13, color, 3))
        parts.append(text(x + 20, y, label, size=13, fill=INK))
        x += 34 + len(label) * 6.5
    return parts


def svg(name, height, title, body, footnote):
    # A solid panel keeps dark text readable on GitHub's dark theme.
    notes = [
        text(32, height - 38 + 17 * index, line, size=12, fill=MUTED)
        for index, line in enumerate(footnote)
    ]
    content = "\n  ".join(
        [
            f"<title>{escape(title)}</title>",
            f'<rect x="0.5" y="0.5" width="{WIDTH - 1}" height="{height - 1}" rx="14" '
            f'fill="#ffffff" stroke="{LINE}"/>',
            *body,
            *notes,
        ]
    )
    (OUT / name).write_text(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{height}" '
        f'viewBox="0 0 {WIDTH} {height}" role="img" font-family="{escape(FONT)}">\n'
        f"  {content}\n</svg>\n"
    )


def percent(numerator, denominator):
    return f"{numerator / denominator:.0%}" if denominator else "n/a"


def resolution_chart():
    systems = [("Rules only (before)", BEFORE, RUNS[0], "rules")] + [
        (f"Rules, then {run['name']}", run["color"], run, "rules_then_direct") for run in RUNS
    ]
    top, bottom, left, right = 128, 388, 84, WIDTH - 32
    body = header(
        "Answerable payments resolved automatically",
        "Rules only, then the rules plus each model reading the customer's note.",
    )
    body += legend(106, [(label, color) for label, color, _, _ in systems])
    for step in range(5):
        y = bottom - (bottom - top) * step / 4
        body.append(f'<line x1="{left}" y1="{y:.1f}" x2="{right}" y2="{y:.1f}" stroke="{LINE}"/>')
        body.append(text(left - 10, y + 5, f"{step * 25}%", size=12, fill=MUTED, anchor="end"))
    group_width = (right - left) / len(SCOPES)
    bar, gap = 70, 16
    for index, (scope, name) in enumerate(SCOPES):
        center = left + group_width * (index + 0.5)
        start = center - (len(systems) * bar + (len(systems) - 1) * gap) / 2
        answerable = end_to_end(RUNS[0], scope, "rules")["answerable"]
        for position, (_, color, run, method) in enumerate(systems):
            correct = end_to_end(run, scope, method)["correct"]
            height = (bottom - top) * correct / answerable
            x = start + position * (bar + gap)
            body.append(rect(x, bottom - height, bar, height, color, 4))
            strong = run is CURRENT and method == "rules_then_direct"
            body.append(
                text(
                    x + bar / 2,
                    bottom - height - 26,
                    percent(correct, answerable),
                    size=20,
                    weight=700,
                    fill=NAVY if strong else MUTED,
                    anchor="middle",
                    halo=True,
                )
            )
            body.append(
                text(
                    x + bar / 2,
                    bottom - height - 8,
                    f"{correct} of {answerable}",
                    size=12,
                    fill=MUTED,
                    anchor="middle",
                    halo=True,
                )
            )
        cases = end_to_end(RUNS[0], scope, "rules")["cases"]
        body.append(text(center, bottom + 28, name, size=16, weight=700, anchor="middle"))
        body.append(
            text(
                center,
                bottom + 48,
                f"{answerable} answerable of {cases} cases",
                size=13,
                fill=MUTED,
                anchor="middle",
            )
        )
    svg(
        "resolution.svg",
        500,
        "Answerable payments resolved automatically, rules only and with each model",
        body,
        COMPARISON_FOOTNOTE,
    )


def rescue_chart():
    body = header(
        "Payments the rules missed, resolved by each model",
        "Answerable payments the rules sent to a person, and how many each model then got right.",
    )
    left, right, y = 210, WIDTH - 110, 110
    for scope, name in SCOPES:
        body.append(text(32, y + 4, name, size=16, weight=700))
        y += 18
        for run in RUNS:
            counts = run["analysis"]["after_rules"][scope]
            share = counts["resolved"] / counts["answerable"]
            label = f"{run['name']}, {counts['resolved']} of {counts['answerable']}"
            body.append(text(32, y + 17, label, size=13, fill=INK))
            body.append(rect(left, y, right - left, 24, TRACK, 5))
            body.append(rect(left, y, (right - left) * share, 24, run["color"], 5))
            body.append(
                text(
                    right + 14,
                    y + 19,
                    percent(counts["resolved"], counts["answerable"]),
                    size=18,
                    weight=700,
                    fill=NAVY,
                )
            )
            y += 32
        y += 20
    svg(
        "rescue.svg",
        y + 62,
        "Payments the rules missed, resolved by each model",
        body,
        COMPARISON_FOOTNOTE,
    )


def unavailable(row):
    # The report has no unavailable field; it is whatever the other outcomes leave over.
    counted = row.get("proposals", 0) + row["correct_deferrals"] + row["unnecessary_deferrals"]
    return row["cases"] - counted


def precision_chart():
    body = header(
        "How often a proposal was right",
        "Each bar is one model's proposals, split into right and wrong; length shows how many.",
    )
    body += legend(106, [("Right", RIGHT), ("Wrong", WRONG)])
    left, right, y = 210, WIDTH - 210, 128
    largest = max(
        end_to_end(run, scope, "rules_then_direct")["proposals"]
        for run in RUNS
        for scope, _ in SCOPES
    )
    for scope, name in SCOPES:
        body.append(text(32, y + 4, name, size=16, weight=700))
        y += 18
        for run in RUNS:
            row = end_to_end(run, scope, "rules_then_direct")
            correct, wrong, proposals = row["correct"], row.get("unsupported", 0), row["proposals"]
            body.append(text(32, y + 17, f"{run['name']}, {proposals} proposals", size=13))
            x = left
            for count, color in ((correct, RIGHT), (wrong, WRONG)):
                width = (right - left) * count / largest
                body.append(rect(x, y, width, 24, color))
                if width >= 24:
                    body.append(
                        text(
                            x + width / 2,
                            y + 17,
                            count,
                            size=12,
                            weight=700,
                            fill="#ffffff",
                            anchor="middle",
                        )
                    )
                x += width
            body.append(
                text(
                    x + 12,
                    y + 18,
                    f"{percent(correct, proposals)} right, {wrong} wrong",
                    size=14,
                    weight=700,
                    fill=NAVY,
                )
            )
            y += 32
        y += 20
    svg(
        "precision.svg",
        y + 62,
        "How often each model's proposals were right",
        body,
        COMPARISON_FOOTNOTE,
    )


def outcome_row(y, label, row, left, right):
    cases = row["cases"]
    segments = [
        (row.get("correct", 0), RIGHT, "#ffffff"),
        (row.get("unsupported", 0), WRONG, "#ffffff"),
        (row["correct_deferrals"], DEFERRED, "#ffffff"),
        (row["unnecessary_deferrals"], MISSED, INK),
        (unavailable(row), UNAVAILABLE, INK),
    ]
    parts = [text(32, y + 15, label, size=15, weight=700)]
    summary = f"{row.get('correct', 0)} right, {row.get('unsupported', 0)} wrong, of {cases}"
    parts.append(text(32, y + 33, summary, size=12, fill=MUTED))
    x = left
    for count, color, ink in segments:
        width = (right - left) * count / cases
        parts.append(rect(x, y, width, 32, color))
        if width >= 26:
            parts.append(
                text(x + width / 2, y + 21, count, size=13, weight=700, fill=ink, anchor="middle")
            )
        x += width
    return parts


OUTCOME_LEGEND = [
    ("Right", RIGHT),
    ("Wrong", WRONG),
    ("To a person, correctly", DEFERRED),
    ("To a person, but answerable", MISSED),
    ("Unavailable", UNAVAILABLE),
]


def outcomes_chart():
    body = header(
        "Where every case ended",
        "Each bar covers all of a method's cases: right, wrong, or sent to a person.",
    )
    body += legend(106, OUTCOME_LEGEND)
    left, right, y = 250, WIDTH - 32, 136
    models = [(f"Rules, then {run['name']}", run, "rules_then_direct") for run in RUNS]
    rows = {
        "reserved-final": [("Rules only", RUNS[0], "rules"), *models],
        "all-cases": [
            ("Rules only", RUNS[0], "rules"),
            ("Shadow ranker", RUNS[0], "ranker_excluding_validation"),
            *models,
        ],
    }
    for scope, name in SCOPES:
        cases = end_to_end(RUNS[0], scope, "rules")["cases"]
        body.append(text(32, y + 8, f"{name}, {cases} cases", size=13, weight=700, fill=MUTED))
        y += 22
        for label, run, method in rows[scope]:
            body += outcome_row(y, label, end_to_end(run, scope, method), left, right)
            y += 50
        y += 14
    body.append(
        text(
            32,
            y + 4,
            "The shadow ranker is scored on 138 cases: "
            "the 40 validation cases chose its threshold.",
            size=12,
            fill=MUTED,
        )
    )
    svg("outcomes.svg", y + 70, "Where every case ended, by method", body, COMPARISON_FOOTNOTE)


def categories_chart(run):
    body = header(
        f"Results by case type, rules then {run['name']}",
        "All 178 cases. Each bar is one type of case, split by outcome.",
    )
    body += legend(106, OUTCOME_LEGEND)
    left, right, y = 250, WIDTH - 120, 128

    def order(item):
        _, counts = item
        return (-counts.get("right", 0) / counts["cases"], counts.get("wrong", 0), item[0])

    for key, counts in sorted(run["analysis"]["by_category"].items(), key=order):
        cases = counts["cases"]
        missed = counts.get("deferred_reachable", 0) + counts.get("deferred_unreachable", 0)
        segments = [
            (counts.get("right", 0), RIGHT, "#ffffff"),
            (counts.get("wrong", 0), WRONG, "#ffffff"),
            (counts.get("deferred_correctly", 0), DEFERRED, "#ffffff"),
            (missed, MISSED, INK),
            (counts.get("unavailable", 0), UNAVAILABLE, INK),
        ]
        body.append(text(32, y + 18, CATEGORY_NAMES.get(key, key), size=14))
        x = left
        for count, color, ink in segments:
            width = (right - left) * count / cases
            body.append(rect(x, y, width, 26, color))
            if width >= 22:
                body.append(
                    text(
                        x + width / 2, y + 18, count, size=12, weight=700, fill=ink, anchor="middle"
                    )
                )
            x += width
        right_label = f"{counts.get('right', 0)} of {cases} right"
        body.append(text(right + 12, y + 18, right_label, size=12, fill=MUTED))
        y += 36
    svg(
        f"categories-{run['key']}.svg",
        y + 66,
        f"Results by case type for rules then {run['name']}",
        body,
        (run["footnote"], SOURCE),
    )


def errors_chart(run):
    analysis = run["analysis"]
    wrong = analysis["wrong_by_cause"]
    total = sum(wrong.values())
    seen = analysis["direct_seen_reachable"]
    proposals = end_to_end(run, "all-cases", "rules_then_direct")["proposals"]
    body = header(
        f"{run['name']}: all {total} wrong proposals, by cause",
        f"{total} of {proposals} proposals across all 178 cases were wrong.",
    )
    left, right, y = 32, WIDTH - 90, 104
    largest = max(wrong.values(), default=1)
    for cause, count in sorted(wrong.items(), key=lambda item: (-item[1], item[0])):
        body.append(text(left, y + 14, CAUSE_NAMES.get(cause, cause), size=15))
        body.append(rect(left, y + 24, (right - left) * count / largest, 24, WRONG, 4))
        x = left + (right - left) * count / largest + 12
        body.append(text(x, y + 42, count, size=18, weight=700, fill=NAVY))
        y += 66
    body.append(rect(32, y + 2, WIDTH - 64, 58, TRACK, 8))
    body.append(
        text(
            48,
            y + 26,
            f"When the right allocation was among the candidates ({seen['cases']} cases):",
            size=14,
            weight=700,
        )
    )
    deferred, mistaken = seen["cases"] - seen["proposed"], seen["proposed"] - seen["correct"]
    body.append(
        text(
            48,
            y + 47,
            f"{seen['correct']} resolved correctly, {deferred} sent to a person, {mistaken} wrong.",
            size=14,
        )
    )
    svg(
        f"errors-{run['key']}.svg",
        y + 132,
        f"Every wrong proposal by {run['name']}, by cause",
        body,
        (run["footnote"], SOURCE),
    )


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    for stale in OUT.glob("*.svg"):
        stale.unlink()
    resolution_chart()
    precision_chart()
    rescue_chart()
    outcomes_chart()
    for run in RUNS:
        categories_chart(run)
        errors_chart(run)
    print(f"wrote {len(list(OUT.glob('*.svg')))} charts to {OUT.relative_to(ROOT)}")
