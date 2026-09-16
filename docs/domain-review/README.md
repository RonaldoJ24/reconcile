# Independent domain review — not yet performed

This is a synthetic, exposed development packet. It is not a sealed benchmark,
customer dataset, or evidence that an accountant has validated Reconcile. Cases
and draft labels were authored by an agent. All amounts are MXN.

Give a reviewer only `reviewer-packet.md` and `response-form.csv`, without access
to `author-labels.csv`, model outputs, or this repository's implementation. Ask an
accounts-receivable specialist, bookkeeper, or accountant to work independently.
Record their relevant experience and any assumptions about the accounting process
with consent; do not collect unnecessary personal information.

For each case, choose a candidate or **insufficient information**. A proposed
allocation must be supported by the evidence available at decision time, not just
have a matching total. Record the evidence, ambiguity, and any better answer absent
from the candidate list. Confidence is optional, as is elapsed review time. Blank
responses remain missing; they are not counted as deferrals. Do not show expected
labels before responses are submitted.

## Adjudication procedure

1. Preserve the original response file and its SHA-256 digest. Keep identifying
   reviewer metadata separately from any shareable artifact.
2. Compare responses with the separately stored **provisional** author labels.
   Record agreement and disagreement without changing either file.
3. For each disagreement, record case ID, each interpretation, cited evidence,
   unstated business assumptions, and whether the candidate set was incomplete.
4. A human domain reviewer and project owner agree on an adjudicated label or mark
   the case unresolved. Preserve the rationale and date in a separate record.
   If no agreement exists, exclude the case from semantic scoring and disclose it.
5. Version changed inputs/labels, record old/new hashes and the adjudication record,
   and rerun only authorized development checks. Never silently relabel a final set
   or overwrite the original packet. Do not claim external validation until this
   process actually occurs.

Variants in the same pair share a group (`DR-G01` through `DR-G12`). They must stay
together in any later split. All of this packet is exposed development material;
its labels may not be used as independent final evidence. Candidate letters are
local to each case. Repeated invoice labels such as A or B are distinct entities
in each group. No model has been scored on this packet by preparing these files.

## Adjudication record template

Case ID; original input/label hashes; reviewer answer; provisional author answer;
evidence; disagreement type; missing business rule; adjudicated answer or unresolved;
human adjudicator; date; rationale; new version/hashes; authorization for later use.
