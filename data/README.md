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
