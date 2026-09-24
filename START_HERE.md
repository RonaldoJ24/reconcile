# Start Reconcile in local Codex — free deployment edition

Update 2026-09-23: the owner switched the runtime model from DeepSeek to OpenAI `gpt-6-luna`. The DeepSeek references below describe the original plan; `AGENTS.md` and `contracts/LLM_V1.md` hold the current provider rules.

Version 1.1, 2026-09-14. This package replaces the previous cost/provider defaults. It contains specifications, not an implemented application. Use the existing Codex session and available subscription model; this project requires no separate OpenAI API subscription. Do not alter global Codex settings.

Extract into the intended project directory. For an existing checkout, inspect differences before replacing instructions; preserve completed code, local changes, and STATUS. Do not reset the repository.

## Kickoff

```text
Read AGENTS.md and docs/PROJECT_BRIEF.md, including its binding cost constraint. Read docs/FREE_DEPLOYMENT.md before hosting or live inference.

Implement Phases 0–2 only now: actual file ingestion, deterministic matching, review/correction, transactional application/reversal, persistence and export, with a working API/UI and passing relevant tests. No cloud deployment, model training, or provider benchmark in this first execution.

I already have gh, Render CLI, Neon CLI and a DeepSeek API key. Use only my existing access. The eventual deployment is one Render Free web service, one dedicated Neon Free project, and DeepSeek Flash with thinking disabled. No new paid infrastructure, no OpenAI API dependency, no separate hosted worker, no GPU, no paid disk, no paid domain, no auto-top-ups. The product can run rules/model-free paths without spending API credit.

Inspect authentication, ownership, plans and remaining shared quotas without displaying secrets. Keep the new repository private. Use local PostgreSQL for tests; only use an isolated new-project Neon test environment inside verified free allowances when needed. Do not alter existing projects, account-wide billing, resources, or credentials.

Freeze domain contracts before parallelizing. You own financial semantics and integration. At most two bounded implementers may work in non-overlapping worktrees. Reuse only tested small components from incident-lens and supplierops-lab, read-only.

Make coherent behavior-and-test commits, review diffs, run checks and merge through normal PRs. Keep comments necessary and AGENTS.md/STATUS.md concise. Do not substitute fixture answers for live behavior.

Report actual behavior, exact tests/results, commits/PRs, blockers and the next phase. CLI authentication is not proof of free capacity; specification files are not proof of deployment.
```

## Continuation when code already exists

```text
Read AGENTS.md, docs/STATUS.md, the active phase in docs/PROJECT_BRIEF.md, and docs/FREE_DEPLOYMENT.md. Preserve existing completed work. Apply the free-deployment/DeepSeek constraint without restarting or replanning the project. Implement only the currently requested phase. Keep ML/evaluation gates intact. Any unavailable free quota blocks deployment, not unrelated local implementation. Do not incur charges or top up accounts.
```

## Phase 4: explicitly budgeted DeepSeek work

Use existing DeepSeek credentials only in backend/local secret storage. The provided environment example defaults live calls off; enabling them is an explicit phase-4 action within approved existing credit. Begin with a minimal structured development smoke, not thousands of requests. Respect the configured caps. When credit/capacity is unavailable, record provider work as blocked while preserving working rules/ML paths.

## Phase 6: hosting

Run cost-preflight before provisioning. Render Free/Neon Free and no-new-charge GitHub settings must be verified in the actual account. Use the current CLI help; do not invent billing/plan-control flags. Deploy only after the relevant application gates pass. Report any unblocked bandwidth/build-overage exposure before creating a resource; never claim guaranteed zero cost from the `free` instance label alone.
