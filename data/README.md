# Reconcile ML v1 generated data

This directory contains the frozen synthetic Phase 3 dataset produced with
seed `20260914`. The four main group splits are exactly 3,000 train, 1,000
validation, 500 calibration, and 500 sealed final-test groups. The challenge
suite has 30 development groups and 20 sealed-test groups.

Decision-time inputs live under `generated/ml-v1/inputs/` (with challenge
inputs under `generated/ml-v1/challenge/inputs/`). They contain only payment,
invoice, credit, message, and candidate observations. Evaluation targets are
separate under the corresponding `targets/` directories and contain scenario,
template, status, acceptable candidates, seed, and label provenance. Runtime
code must never load targets.

`manifest.json` and `challenge-manifest.json` freeze generator/data versions,
counts, candidate totals, split families, and SHA-256 digests. The sealed final
and challenge-test target digests are recorded before model selection. Do not
open sealed target rows during development; use the manifests for integrity
checks. Labels are agent-generated and not independently domain-validated.

Regenerate deterministically from the repository root with:

```sh
PYTHONPATH=backend/src python3 -m reconcile.ml.data --output data/generated/ml-v1
PYTHONPATH=backend/src python3 -m reconcile.ml.data --output data/generated/ml-v1 --validate
```

## Evaluation v2 cases

`eval-v2/cases.jsonl` and `eval-v2/labels.jsonl` hold the 180 synthetic cases that
Claude subagents wrote blind for the pre-registered v2 run, with inputs and labels
in separate files. Labels follow accounting truth, including allocations the system
cannot represent. They are AI-written, not independently reviewed, and now public,
so they are development data for any later change. Results, run outputs and
deviations are in [`reports/eval-v2/`](../reports/eval-v2/README.md). Runtime code
never loads these files, and `.dockerignore` keeps `data/` out of the image.
