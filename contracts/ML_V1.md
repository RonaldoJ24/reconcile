# Reconcile ML v1 contract

Frozen for Phase 3 on 2026-09-14. The learned component ranks candidate
allocations; PostgreSQL application and reversal remain deterministic and always
require reviewer approval.

## Dataset boundary

`make data-dev` deterministically generates 5,000 synthetic payment-case groups
with seed `20260914`: 3,000 train, 1,000 validation, 500 calibration, and 500
sealed final-test groups. Related variants and business/entity histories share one
split. Validation, calibration, and final test each contain entity groups absent
from training; held-out message-template families are recorded only in targets and
manifests. Candidate order and neutral IDs are shuffled independently of labels.

The generator builds a hidden intended ledger first, then emits two interfaces:

- `data/generated/ml-v1/inputs/<split>.jsonl` contains only decision-time payment,
  invoice, credit, message, and candidate-allocation observations.
- `data/generated/ml-v1/targets/<split>.jsonl` contains acceptable candidate IDs,
  expected `PROPOSED`/`NEEDS_REVIEW`, scenario family, generator seed, and label
  provenance. Runtime code and images must not contain or load this directory.

Each input group has one payment, at most ten retrieved invoices, at most one
eligible credit, and bounded candidate groups of one to three invoices. Each target
group may name multiple acceptable candidates or none for a genuinely
underdetermined case. The manifest records exact group/candidate counts and SHA-256
digests. A separate challenge suite contains 30 development and 20 sealed groups
covering the ten families required by the project brief. Labels are agent-generated
and are not independently or manually validated by a domain expert.

The sealed final and challenge-test target files are generated and hashed before
model selection. Phase 3 does not open or evaluate them. `make evaluate-release`
requires an explicit sealed-evaluation flag and belongs to the later freeze/release
phase.

## Candidate features and training

The shared offline/online extractor emits this ordered numeric schema only:

1. all candidate invoice IDs explicitly mentioned;
2. fraction of candidate IDs explicitly mentioned;
3. payer/customer name similarity;
4. normalized absolute cash-total residual;
5. candidate group size;
6. maximum absolute due-date distance in days;
7. explicit eligible-credit compatibility;
8. all candidate IDs mentioned in remittance text;
9. missing customer identity;
10. conflicting evidence signal.

Artificial IDs themselves, row position, candidate order, scenario/template family,
seed, expected answer, and post-reconciliation outcomes are forbidden features.
Preprocessing is fit on training only. Candidate weights sum equally per payment
group so groups with more negatives do not dominate.

`make train` fits exactly two scikit-learn candidates with seed `20260914`: a
regularized logistic-regression pipeline and one bounded histogram gradient-boosted
tree. It compares per-payment validation ranking and proposal precision/coverage;
raw classifier output is a ranking score, never an operational probability.
Calibration data is reserved and reported separately, not used for candidate
selection. No broad hyperparameter search is permitted.

## Artifact and runtime

The selected artifact lives below `artifacts/ranker-ml-v1/` with the fitted pipeline,
fixed feature schema, training/validation dataset hashes, dependency versions,
seed, model ID/version, validation metrics, promotion decision, and model SHA-256.
The runtime loader accepts only this internal fixed path and verifies the recorded
digest before deserialization. Save/load prediction equivalence and shared feature
parity are tested.

Default runtime mode remains `rules-v1`. `RECONCILE_RANKER_MODE=shadow` enables an
actual local artifact prediction during matching and records model ID, version,
ranked candidate, score, and truncation in the proposal trace without changing the
rules proposal. Promotion requires a documented validation precision/coverage
improvement and is not automatic. ML never applies or reverses money.

`deploy/runtime-allowlist.txt` is the build boundary: application code, the verified
artifact, static UI, migrations, and required metadata only. Generated inputs,
targets, manifests, reports, notebooks, caches, and training utilities are excluded.
