# Reconcile agent instructions

## Read scope
Read `docs/PROJECT_BRIEF.md` for the initial project context. On continuation, read `docs/STATUS.md` and only the active phase and relevant contracts. Never paste the full brief into every task or subagent context. Preserve higher-priority user/workspace instructions.

## Product
Reconcile resolves incoming-payment allocation exceptions. Inputs are bank/invoice/credit CSV and payment-message TXT; output is a reviewable allocation and an explicitly approved persistent application. Initial data is synthetic; parsing, ML/provider inference, persistence, and export must be real. No scenario-answer lookup or browser-only success adapter.

## Non-negotiable boundaries
- Exact integer centavos for MXN. Cash is separate from credit. No over-allocation, duplicate application, silent balance overwrite, or unsupported currency conversion.
- Models propose evidence/relationships. The deterministic domain service owns application and reversal inside PostgreSQL transactions.
- Check workspace ownership, source/balance versions, locks, and idempotency at application time. Never hold a financial transaction open across a provider call.
- Source changes invalidate pending proposals; applied records require explicit reversal, never history rewriting.
- Hidden evaluation labels never enter runtime, features, prompts, or deployed images. Synthetic benchmark performance is not real-world accuracy.
- Do not inspect sealed test labels to improve results. Freeze versions before final evaluation; record test-set exposure honestly.
- Missing credentials, provider failures, cached results, and unmeasured metrics must be explicit. Do not fabricate live inference or measured performance.

## Code and workflow
Use a Python modular monolith with a typed API, PostgreSQL, a reusable worker, scikit-learn ranking, bounded interpretation, and a React/TypeScript UI. Keep domain code independent of API/framework/provider imports. Comments explain non-obvious invariants and constraints only. No unnecessary abstractions, dependencies, generic agent frameworks, or unrelated refactoring.

The coordinator owns shared contracts and integration. At most two implementers work in separate worktrees with non-overlapping ownership. Never recursively spawn reviewers. Run targeted tests while editing; full relevant checks at phase boundaries. Live-model tests and final evaluation are explicit, budgeted commands, not default CI behavior.

Inspect source repositories read-only. Reuse small audited components and their tests, record provenance, and do not copy credentials, databases, old claims, fixture answers, or entire architectures. Preserve unrelated changes.

## Cost and runtime provider
Use OpenAI only for runtime interpretation: configurable `gpt-6-luna`, reasoning effort `none`, JSON output, strict server-side validation, and bounded calls, with the owner's OpenAI API key held only in server-side secret storage. The owner switched from DeepSeek on 2026-09-23; the v2 evaluation was measured with DeepSeek and stays attributed to it. Use the owner's existing Codex session for development, without adding subscriptions or auto-refilling credits.

Read `docs/FREE_DEPLOYMENT.md` before hosting or live calls. Infrastructure is strictly Render Free + Neon Free + existing GitHub free allowances. Exactly one Render web service serves React, API, and a bounded consumer while awake. No hosted standalone worker, paid disk, cron, Redis, object store, GPU, paid domain, or cloud training. Train and load-test locally. Check remaining shared allowances and overage behavior; alerts alone are not a hard zero-cost guarantee. Use explicit budgets and invitation-gated provider access. Free-tier restrictions never waive integrity/evaluation tests.

## Git and deployment
Use small behavior-focused commits, short-lived branches, and PRs with exact checks and known limits. Do not force-push shared history. Never equate a written test with a passed test. Keep the new repository private until publication is authorized.

Use existing gh/Render/Neon CLI access, after verifying ownership and current help. Create only new Reconcile-owned resources. No unrelated databases/services, destructive operations, billing changes, paid resources, or additional subscriptions. Stop only the blocked deployment step if the free plan or cost boundary cannot be verified; continue useful local work. Never print credentials or put them in command arguments/frontend code.

No idle DB polling or automatic keep-alives. Liveness probes must not query Neon. The free Render preview must use durable Neon state and bounded source storage; do not rely on local disk or promise an always-running free worker. Apply quotas, a provider kill switch, and global as well as session limits before exposure. Destructive tests require an isolated guarded test database.

## Token discipline and reporting
Search narrowly; read bounded file ranges. Keep raw datasets, full logs, generated bundles, and lockfile contents out of prompts unless specifically needed. Use the main model for ambiguous/high-risk work and a smaller model for bounded implementation. Do not enable maximum reasoning or multi-agent mode by default.

Update `docs/STATUS.md` after each phase: implemented behavior, exact commands/results, commit, blockers, deviations, next task. Keep it compact. Report observed token usage only; missing counts are unknown. End a run with actual phase status, not promises of background completion.
