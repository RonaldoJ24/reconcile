# Bounded reuse inventory

Inspected read-only on 2026-09-14. Neither source repository or its resources was
modified. Reconcile does not copy either architecture, history, database, fixture
answer, lockfile, metric, or credential.

| Source | Commit and paths | Behavior inspected | Reconcile decision | Required check | License |
| --- | --- | --- | --- | --- | --- |
| `RonaldoJ24/incident-lens` | `9def95ffcc498a73b48f99498dc68c6124f356e9`; `backend/incident_lens/api/{app.py,models.py,store.py}`, `backend/tests/{test_api.py,test_postgres_integration.py}` | Strict Pydantic boundaries, owner-filtered lookups, PostgreSQL transactions, idempotent run creation | Reuse the ideas only. Reconcile uses SQLAlchemy sessions per request and stronger cookie/CSRF ownership; no source copied because the repository has no detected license and its single shared connection is unsuitable for contested financial writes. | Cross-workspace denial, strict unknown-field rejection, PostgreSQL integration and race tests | No detected repository license; no code copied |
| `RonaldoJ24/incident-lens` | same commit; `backend/incident_lens/workflow/graph.py`, `backend/migrations/003_workflow_checkpoints.sql`, `backend/tests/test_phase4_workflow.py` | Durable state/checkpoint and resume tests | Defer provider graph work to Phase 4. Phase 2 jobs reuse only the durable-state principle with a purpose-built PostgreSQL lease table. | Lease expiry/reclaim and duplicate-delivery tests | No detected repository license; no code copied |
| `RonaldoJ24/supplierops-lab` | `ace30bdf5dccd29acc94f9c978fa1e366f10a9ed`; `src/shared/{schemas.ts,state.ts,service.ts,api.ts}` | Integer minor units, strict schemas, correction → approval → submission separation | Reimplement the small domain-specific state machine in Python. Keep correction, approval/application, and reversal separate; do not port Electron IPC or scenario fixtures. | Money boundaries, immutable revision, apply-before-review denial, idempotency tests | MIT; ideas only, no copied source |
| `RonaldoJ24/supplierops-lab` | same commit; `src/renderer/{App.tsx,styles.css}`, `src/renderer/hooks/useSupplierOps.ts` | Review table, evidence drawer, explicit action labels, responsive and reduced-motion states | Reuse interaction principles, not components. Reconcile uses actual HTTP data and no fallback/scenario adapter. | Playwright desktop/mobile, keyboard focus, error/loading/empty states, no fixture fallback | MIT; ideas only, no copied source |

The inspected Incident Lens token-overlap support evaluator is explicitly excluded:
span existence and lexical overlap do not establish allocation entailment.
