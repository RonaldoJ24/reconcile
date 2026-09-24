"""Draw the evaluation charts used by the README, the v2 report and the landing page.

Usage: python3 reports/charts.py

Every figure comes from published run outputs (`report.json` and `analysis.json`);
none is typed by hand. Charts get shared without the text around them, so each one
carries its own sample sizes and a synthetic-data footnote. A later evaluation adds
an entry to `SYSTEMS`, and it becomes another bar measured on the same protocol.
"""

import json
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / "reports/eval-v2/run"
OUT = ROOT / "docs/images/results"

REPORT = json.loads((RUN / "report.json").read_text())
ANALYSIS = json.loads((RUN / "analysis.json").read_text())
END_TO_END = REPORT["end_to_end"]

WIDTH = 760
FONT = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif"
NAVY, INK, MUTED, LINE, TRACK = "#112d4e", "#183047", "#5c7084", "#dbe3ea", "#eef2f5"
BEFORE, NOW = "#b3c1cd", "#1a65a8"
RIGHT, WRONG, DEFERRED, MISSED = "#1f7a4d", "#c2413b", "#7f9bb3", "#dfe6ec"
FOOTNOTE = (
    "Evaluation v2, 2026-09-23: 178 synthetic cases written by AI agents; "
    "pre-registered, run once.",
    "Synthetic results, not real-world accuracy. Source: github.com/RonaldoJ24/reconcile",
)

# Bars per group in the headline chart, as (label, color, method key in report.json).
SYSTEMS = [
    ("Rules only (before)", BEFORE, "rules"),
    ("Rules, then DeepSeek (now)", NOW, "rules_then_direct"),
]
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


def footnote(height):
    return [
        text(32, height - 38 + 17 * i, line, size=12, fill=MUTED) for i, line in enumerate(FOOTNOTE)
    ]


def svg(name, height, title, body):
    # A solid panel keeps dark text readable on GitHub's dark theme.
    content = "\n  ".join(
        [
            f"<title>{escape(title)}</title>",
            f'<rect x="0.5" y="0.5" width="{WIDTH - 1}" height="{height - 1}" rx="14" '
            f'fill="#ffffff" stroke="{LINE}"/>',
            *body,
            *footnote(height),
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
    top, bottom, left, right = 128, 388, 84, WIDTH - 32
    body = header(
        "Answerable payments resolved automatically",
        "Before: rules only. Now: the rules, then DeepSeek reads the customer's note.",
    )
    body += legend(106, [(label, color) for label, color, _ in SYSTEMS])
    for step in range(5):
        y = bottom - (bottom - top) * step / 4
        body.append(f'<line x1="{left}" y1="{y:.1f}" x2="{right}" y2="{y:.1f}" stroke="{LINE}"/>')
        body.append(text(left - 10, y + 5, f"{step * 25}%", size=12, fill=MUTED, anchor="end"))
    group_width = (right - left) / len(SCOPES)
    bar, gap = 88, 22
    for index, (scope, name) in enumerate(SCOPES):
        center = left + group_width * (index + 0.5)
        start = center - (len(SYSTEMS) * bar + (len(SYSTEMS) - 1) * gap) / 2
        answerable = END_TO_END[scope]["rules"]["answerable"]
        for position, (_, color, method) in enumerate(SYSTEMS):
            correct = END_TO_END[scope][method]["correct"]
            height = (bottom - top) * correct / answerable
            x = start + position * (bar + gap)
            body.append(rect(x, bottom - height, bar, height, color, 4))
            label_y = bottom - height
            body.append(
                text(
                    x + bar / 2,
                    label_y - 26,
                    percent(correct, answerable),
                    size=22,
                    weight=700,
                    fill=NAVY if color == NOW else MUTED,
                    anchor="middle",
                    halo=True,
                )
            )
            body.append(
                text(
                    x + bar / 2,
                    label_y - 8,
                    f"{correct} of {answerable}",
                    size=12,
                    fill=MUTED,
                    anchor="middle",
                    halo=True,
                )
            )
        cases = END_TO_END[scope]["rules"]["cases"]
        body.append(text(center, bottom + 28, name, size=16, weight=700, fill=INK, anchor="middle"))
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
        "Answerable payments resolved automatically, before and after DeepSeek",
        body,
    )


def rescue_chart():
    body = header(
        "Payments the rules missed, resolved by DeepSeek",
        "Answerable payments the rules sent to a person, and how many DeepSeek then got right.",
    )
    left, right, y = 190, WIDTH - 120, 118
    for scope, name in SCOPES:
        counts = ANALYSIS["after_rules"][scope]
        share = counts["resolved"] / counts["answerable"]
        body.append(text(32, y + 21, name, size=16, weight=700))
        body.append(rect(left, y, right - left, 30, TRACK, 6))
        body.append(rect(left, y, (right - left) * share, 30, NOW, 6))
        body.append(
            text(
                right + 14,
                y + 22,
                percent(counts["resolved"], counts["answerable"]),
                size=22,
                weight=700,
                fill=NAVY,
            )
        )
        body.append(
            text(
                left,
                y + 50,
                f"{counts['resolved']} of {counts['answerable']} answerable payments "
                "the rules could not match",
                size=12,
                fill=MUTED,
            )
        )
        y += 80
    svg("rescue.svg", y + 62, "Payments the rules missed, resolved by DeepSeek", body)


def outcome_row(y, label, row, left, right):
    cases = row["cases"]
    segments = [
        (row.get("correct", 0), RIGHT, "#ffffff"),
        (row.get("unsupported", 0), WRONG, "#ffffff"),
        (row["correct_deferrals"], DEFERRED, "#ffffff"),
        (row["unnecessary_deferrals"], MISSED, INK),
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


def outcomes_chart():
    body = header(
        "Where every case ended",
        "Each bar covers all of a method's cases: right, wrong, or sent to a person.",
    )
    body += legend(
        106,
        [
            ("Right", RIGHT),
            ("Wrong", WRONG),
            ("Sent to a person, correctly", DEFERRED),
            ("Sent to a person, but answerable", MISSED),
        ],
    )
    left, right, y = 250, WIDTH - 32, 136
    rows = {
        "reserved-final": [("Rules only", "rules"), ("Rules, then DeepSeek", "rules_then_direct")],
        "all-cases": [
            ("Rules only", "rules"),
            ("Shadow ranker", "ranker_excluding_validation"),
            ("Rules, then DeepSeek", "rules_then_direct"),
        ],
    }
    for scope, name in SCOPES:
        cases = END_TO_END[scope]["rules"]["cases"]
        body.append(text(32, y + 8, f"{name}, {cases} cases", size=13, weight=700, fill=MUTED))
        y += 22
        for label, method in rows[scope]:
            body += outcome_row(y, label, END_TO_END[scope][method], left, right)
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
    svg("outcomes.svg", y + 70, "Where every case ended, by method", body)


def categories_chart():
    body = header(
        "Results by case type, all 178 cases",
        "Rules, then DeepSeek. Each bar is one type of case, split by outcome.",
    )
    body += legend(
        106,
        [
            ("Right", RIGHT),
            ("Wrong", WRONG),
            ("Sent to a person, correctly", DEFERRED),
            ("Sent to a person, but answerable", MISSED),
        ],
    )
    left, right, y = 250, WIDTH - 120, 128

    def order(item):
        _, counts = item
        return (-counts.get("right", 0) / counts["cases"], counts.get("wrong", 0), item[0])

    for key, counts in sorted(ANALYSIS["by_category"].items(), key=order):
        cases = counts["cases"]
        missed = counts.get("deferred_reachable", 0) + counts.get("deferred_unreachable", 0)
        segments = [
            (counts.get("right", 0), RIGHT, "#ffffff"),
            (counts.get("wrong", 0), WRONG, "#ffffff"),
            (counts.get("deferred_correctly", 0), DEFERRED, "#ffffff"),
            (missed, MISSED, INK),
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
        body.append(
            text(
                right + 12,
                y + 18,
                f"{counts.get('right', 0)} of {cases} right",
                size=12,
                fill=MUTED,
            )
        )
        y += 36
    svg("categories.svg", y + 66, "Results by case type for rules, then DeepSeek", body)


def errors_chart():
    wrong = ANALYSIS["wrong_by_cause"]
    total = sum(wrong.values())
    seen = ANALYSIS["direct_seen_reachable"]
    proposals = END_TO_END["all-cases"]["rules_then_direct"]["proposals"]
    body = header(
        f"All {total} wrong proposals should have gone to a person",
        f"{total} of {proposals} proposals across all 178 cases, grouped by cause.",
    )
    left, right, y = 32, WIDTH - 90, 104
    largest = max(wrong.values())
    for cause, count in sorted(wrong.items(), key=lambda item: (-item[1], item[0])):
        body.append(text(left, y + 14, CAUSE_NAMES.get(cause, cause), size=15))
        body.append(rect(left, y + 24, (right - left) * count / largest, 24, WRONG, 4))
        body.append(
            text(
                left + (right - left) * count / largest + 12,
                y + 42,
                count,
                size=18,
                weight=700,
                fill=NAVY,
            )
        )
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
    svg("errors.svg", y + 132, "Every wrong proposal, by cause", body)


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    for draw in (resolution_chart, rescue_chart, outcomes_chart, categories_chart, errors_chart):
        draw()
    print(f"wrote {len(list(OUT.glob('*.svg')))} charts to {OUT.relative_to(ROOT)}")
