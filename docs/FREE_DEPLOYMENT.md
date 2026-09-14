# Reconcile — zero-new-infrastructure-cost deployment

Version 1.1, checked 2026-09-14. These are implementation instructions and proposed safeguards, not a deployed or measured system. This file supersedes earlier optional paid hosting and OpenAI-runtime defaults. The owner has existing Codex, gh, Render CLI, Neon CLI, and DeepSeek access.

## Architecture and permitted resources

Use one Render **Free** web service serving the compiled React UI and FastAPI on one origin. One lifecycle-managed, concurrency-one consumer processes durable Neon jobs while the service is awake; keep a standalone local worker command but do not provision a hosted worker. No separate frontend server, Redis, message broker, object store, disk, GPU, paid domain, or cron. Use the provider-issued HTTPS hostname.

Use one new Reconcile-owned **Neon Free** project. All persistent records, small bounded CSV/TXT source payloads, jobs/checkpoints, quotas, and interpretation-cache metadata belong there. Temporary local files can be recreated. Train scikit-learn locally and include only an integrity-checked compact inference artifact in the runtime build. Exclude training data, hidden labels, notebooks, and benchmark workloads from runtime. Limit inference threads and measure memory on the actual free instance.

The hosted preview remains synthetic-only with real parsing, inference, persistence, and review behavior. Visitor provider access is invite-gated and rate-limited. Do not solve cost by replacing actual inference with a hidden prerecorded answer.

## Required cost preflight

Check current plans, remaining allowance, billing/overage settings, and resource ownership using current supported CLI/API operations. Record sanitized findings. Never infer account quotas or billing state from public documentation alone. The checks are read-only unless creation of a new project resource is already authorized. Do not change account-wide billing, remove a payment method, pause another site, or alter another database.

Documented Render Free limits include sleeping after 15 minutes without incoming traffic and a shared allowance of 750 free instance-hours per workspace per calendar month. Build and bandwidth quotas are also shared. Card-backed accounts can incur overage charges; a pipeline spend limit is not proof that bandwidth has a hard cap. Where a supported scope-specific zero-overage setting exists, use it only within the owner's permission. If no hard stop can be verified, disclose the exposure and withhold the uncertain deployment step. Do not promise a guaranteed zero bill, invent a bandwidth cap, or bypass limits with keep-alives or additional accounts.

Neon's current Free information lists 100 CU-hours and 0.5 GB storage per project, with scale-to-zero after inactivity. Verify actual entitlements and keep headroom for all branches, indexes, audit rows, and source payloads. Use local PostgreSQL for repeated destructive tests. A continuously polled database is not an idle database.

GitHub private-repository Actions use a finite allowance. Use only standard Linux runners inside verified no-overage settings, cancel superseded runs, avoid duplicated push/PR jobs, and keep artifact retention/storage small. If the quota or no-charge boundary is unavailable, run checks locally and disable that project's automatic paid exposure. Do not make the repository public merely to change CI pricing.

## Sleep-aware execution

Wake the consumer from a real enqueue, startup recovery, or authenticated interactive activity. Stop DB polling once the queue is empty; release connections and transactions. Retry timers may exist only for bounded outstanding work. No scheduled pings or idle cleanup loops. Platform liveness must not query Neon, call DeepSeek, or wake the queue. Separate readiness checks should be deliberate and bounded.

Jobs may pause when Render sleeps or restarts. Preserve attempts, leases, sources, quota reservations and checkpoints in Neon. A user returning to the site can trigger recovery. Never report continuous background processing or exactly-once model billing. Never hold a ledger transaction across an API call.

Run migrations from a guarded startup step using an appropriate direct connection and migration lock. Serve only once the schema is ready. Do not rely on a paid-only pre-deploy hook, shell, or one-off-job feature.

## Small-storage policy

Hosted limits: 1 MiB/file, 4 MiB/import batch, 5 MiB/source bytes per synthetic workspace and 50 MiB retained source bytes globally; enforce row/entity limits too. Start with a conservative 300 MiB overall database admission stop and lower it if the account allowance is smaller. This is headroom, not a guarantee of exact SQL storage accounting.

Expire synthetic guest workspaces after 24 hours of inactivity, using bounded cleanup on startup and real requests, not paid scheduling or always-on polling. Persist global billing reservations separately so deleting a workspace cannot reset the spend limit. Store only compact validated interpretation/cache data and bounded observability records. No unbounded logs or response bodies. Never delete applied private financial history as a storage-management trick.

## DeepSeek runtime contract

Default model: `deepseek-flash`. Verify the current model catalog/ID and log the actual response model ID/date. Use DeepSeek's official API base, existing key, non-thinking mode, JSON output, server-side schema validation, and a maximum output of 2,048 tokens. A model alias can change underneath the application; rerun relevant development compatibility checks when that happens. Do not silently change providers or model tiers.

Target 2,000–3,000 input tokens; maximum 6,000 including instructions/schema. One interpretation per ambiguous payment, one transient retry at most, concurrency one, bounded deadlines. No live inference on page load, health probes, CI, every generated case, or rules-resolved cases. Missing evidence or exhausted budgets produce explicit review/unavailable states.

Live calls are disabled by default. Proposed local/app safety ceilings are USD 0.10/day and USD 1.00/month, plus five requests per visitor session and 25 attempts globally per day. These limits are not automatic spending authorization or DeepSeek account-wide limits. Each enablement/benchmark command is explicit and uses existing authorized credit. No top-up, auto-recharge, account change, or cap increase.

Reserve a conservative peak-price, uncached-input maximum in PostgreSQL before every call and retry; concurrent operations must not bypass quotas. Count retries and retain reservations when billing is unknown after a timeout. Reconcile estimates with observed usage. Provider-side or other-app use of the same key is outside this application cap. Unknown prices/model identity disable live calls until verified. Do not claim an approximate token estimate is a guaranteed account-level hard cap.

Cache validated interpretations by workspace, immutable sources, candidate context, prompt/schema version, model/configuration and budget policy. Replay is labeled, and new sources invalidate caches. Financial writes always revalidate current state. No separate paid cache service.

## Deployment and evaluation cadence

Phase 0–2: implement and test locally; no automatic deployment. Phase 3: local synthetic generation/training and verified artifact packaging. Phase 4: explicitly budgeted small DeepSeek comparison. Phase 5: local release/performance tests; no load testing the shared public preview. Phase 6: cost preflight, one reviewed deployment, bounded external smoke. Do not re-deploy on every agent commit or create PR preview services by default.

Deliver sanitized evidence of `plan=free`, resource IDs, deployed SHA, database persistence, actual model mode, sleep/recovery behavior, remaining allowance where observable, and overage protections/limitations. A free preview is an interview/testing surface, not an availability or production-finance compliance promise.
