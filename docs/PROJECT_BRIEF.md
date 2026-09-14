# Reconcile — Codex implementation brief

Version: 1.1 · 2026-09-14 · Render Free / Neon Free / DeepSeek only

## 1. Mandate

Act as the lead software engineer and applied-ML engineer. Build Reconcile as a maintainable Python-first cash-application exception resolver, not a chatbot, scenario viewer, or general accounting platform.

The product helps a bookkeeper determine which invoices an incoming customer payment belongs to, inspect supporting evidence, resolve uncertainty, and record an approved allocation. Its research question is: how much ambiguous payment-allocation work can the system remove without increasing incorrect allocations?

Success requires working file ingestion, a deterministic baseline, an actual trained candidate-ranking model, bounded interpretation of unstructured payment evidence, transactional application/reversal, and honest evaluation. Model development is part of the project, not an optional dependency badge. A model that fails to improve the baseline must remain experimental or in shadow mode; do not manufacture a winning result.

The initial data is synthetic. The software must nevertheless execute real parsing, inference, persistence, and export. Labels belong to the evaluation system, never the application. Do not call synthetic performance production accuracy or imply banking regulatory compliance.

Treat this brief as the source of truth. Make ordinary implementation decisions yourself, record material departures, and continue through the currently authorized phases. Ask only when credentials, ownership, destructive actions, spending, publication, or financial semantics genuinely require the owner. A blocked external dependency does not justify stopping unrelated local work.

### Binding cost constraint

Use the owner's existing Codex access, gh/Render/Neon CLIs, and DeepSeek credential. The deployment budget is **USD 0 in new infrastructure charges**, within verified free allowances. DeepSeek inference is the only permitted usage-based runtime service and consumes the owner's existing credit; never create an OpenAI API dependency, add another provider subscription, top up credit, enable auto-recharge, or upgrade a plan. A CLI being installed does not establish the provisioned resource's plan or remaining allowance.

Use exactly one Render Free web service and one new Reconcile-owned Neon Free project. Serve the React build and FastAPI from the same origin. A bounded job consumer runs only within that web service's lifecycle; the separately runnable worker is for local development, not a second hosted resource. Train models and run large benchmarks locally. Deploy only the compact verified inference artifact and input-free aggregate reports.

Read `docs/FREE_DEPLOYMENT.md` before implementing hosting or live inference. Its no-new-infrastructure-spend rule overrides any looser cost language elsewhere in this package. Preserve all domain, ML, and evaluation acceptance gates. Free hosting changes operating capacity, not correctness requirements. If a hard zero-cost boundary cannot be verified with the current account settings, prepare the deployment and report the exact limitation instead of claiming zero-cost operation or silently accepting overages.

## 2. Initial scope and user experience

Support one documented bank CSV schema, one outstanding-invoice CSV schema, optional credit-note CSV, and UTF-8 payment-message TXT. All amounts are MXN. Support one payment allocated to one to three invoices, a partial payment against one invoice, and at most one credit note per proposal. Unsupported relationships route to review rather than being approximated.

Exclude scanned-document OCR, arbitrary PDFs, foreign exchange, refunds, fees, write-offs, bank APIs, automatic ERP posting, billing, and autonomous application. Do not add Spark, Kafka, Airflow, a vector database, microservices, or a general agent framework merely to match job-description keywords.

Build three primary views: Imports, Review Queue, Allocation Detail. Add a compact evaluation view backed by measured report artifacts. On Allocation Detail show the payment, cash applications, credit applications, remaining balances, cited source spans, plausible alternatives, and concrete review actions. Put execution traces behind a secondary control. Include keyboard navigation, accessible labels, loading/error/empty states, and responsive layouts tested at desktop and mobile widths. Use an English UI initially; source messages can be English or Spanish.

The normal path must be:

`import actual files -> validate/persist -> generate proposals -> inspect/correct -> explicitly apply -> export -> optionally reverse`

Offer a sample-packet download and import flow through that same path. No frontend-only adapter, canned answer lookup, invented timing, decorative live trace, or hidden scenario switch. Persisted model output is a labeled replay, not fresh inference. Render/Neon/API connectivity must be visible without displacing the business task with repeated warnings.

### Required example, with enough evidence to support a unique allocation

A 54,000 MXN payment has a misleading exact-amount invoice candidate. Other open invoices are 101 for 30,000 and 102 for 25,000. Credit note 103 has 1,000 available and explicitly applies to invoice 102. A payment message identifies invoices 101 and 102 and credit note 103.

Expected cash: 30,000 to 101 and 24,000 to 102. Expected credit: 1,000 from 103 to 102. Cash consumed is 54,000; invoices settled total 55,000. The misleading invoice remains unchanged.

This is a development example, not a branch to hardcode. Removing the message or credit linkage may make the allocation unresolved. Equal totals alone cannot establish invoice identity. Where several allocations remain equally supported, return NEEDS_REVIEW or a set of alternatives; do not recover an arbitrary hidden generator intention.

## 3. Architecture and contracts

Use a modular monolith: React/TypeScript frontend; FastAPI/Pydantic API; SQLAlchemy/Alembic/PostgreSQL persistence; a Python worker from the same codebase; scikit-learn for ranking; a small provider adapter for evidence interpretation. Use uv and one JS package manager with committed lockfiles. Verify compatible installed runtime and package versions before pinning them. No unrelated upgrades.

Suggested layout; create modules when their behavior exists, not empty architectural scaffolding:

```
backend/src/reconcile/
  api/             # transport and access control
  domain/          # monetary rules, allocation/reversal, state transitions
  ingest/          # parsers and normalization
  matching/        # retrieval, enumeration, features, baseline, ranking
  interpretation/  # provider adapter and bounded workflow
  persistence/     # ORM, repositories, transactions, migrations
  jobs/            # durable job leasing and worker entrypoint
backend/tests/
frontend/src/
evaluation/        # generator, training, evaluators; excluded from runtime
contracts/
docs/
deploy/
```

Domain modules must not import FastAPI, LangGraph, or provider SDKs. Keep one allocation validator shared by every inference mode and by application. Maintain equivalent feature transforms in offline training and online inference. Generate or validate frontend API types from the server contract; do not maintain divergent financial rules in TypeScript.

Core records: Workspace, ImportBatch, SourceVersion, Customer, Payment, Invoice, CreditNote, EvidenceSpan, AllocationCandidate, Proposal, CashApplication, CreditApplication, ReviewDecision, ModelRun, Job, AuditEvent. Extend only when a concrete behavior needs it.

Minimum version-1 import contract, finalized in Phase 0:

- Bank CSV: source_account_id, transaction_id, booking_date, payer_name, reference, amount, currency.
- Invoice CSV: customer_id, customer_name, invoice_id, issued_date, due_date, balance_as_of, outstanding_amount, currency.
- Credit CSV: customer_id, credit_note_id, balance_as_of, available_amount, currency, optional explicit invoice_id linkage.
- Messages: UTF-8 TXT plus recorded message time and a user-supplied payment/context association. Keep supplied associations distinguishable from inferred relationships; do not infer correct answers from filenames. Never silently treat upload time as message time.

Document exact date/decimal formats, required versus nullable fields, unique-key namespaces, and CSV quoting. Display row errors before committing an import. Preserve original bytes and offsets while normalizing for comparison. Limit the hosted preview to 1 MiB per file, 4 MiB per batch, 5 MiB of source bytes per workspace, and 50 MiB of retained source bytes globally. Enforce row/entity caps as well as byte caps; indexes, parsed rows, and audit records also consume space. The local profile may use 2 MiB per file and 10 MiB per batch. Tighten limits when measurements or current free-tier capacity require it. Performance datasets run locally, not through an unrestricted public-upload endpoint.

Every business record and query is workspace-scoped. Evidence identifies an immutable source version and its original row or text span. Record decision-time/as-of timestamps separately from upload and processing timestamps. Track parser, feature, rule, model, prompt, and dataset versions where they affect results.

Proposals: PROCESSING, PROPOSED, NEEDS_REVIEW, STALE, REJECTED, APPLIED, REVERSED, FAILED. Keep job completion separate from financial application. A human correction creates a new proposal revision with explicit provenance; never mutate an already applied allocation.

### Financial invariants

Parse decimal money strings exactly; store integer centavos for MXN. Reject unsupported currency, NaN, excessive precision, negative applications, and out-of-range amounts. Do not parse money through binary floats or silently round malformed input.

For active, unreversed applications:

- Cash applied to invoices plus unapplied cash equals the available payment amount.
- Cash and credit are separate records. Credit is never counted as cash.
- An invoice cannot consume more cash plus credit than its available balance.
- A credit note cannot be consumed beyond its available credit.
- Payment, customer, invoice, credit, and workspace relationships must be valid.
- A proposed balanced combination is not automatically an evidence-supported combination.

Define the opening-snapshot contract before implementation. Initial invoice and credit balances are explicit available balances as of a cutoff. Do not apply historical payments already reflected in those balances, subtract an already-accounted credit again, or silently overwrite opening balances during reimport. In the first release, conflicting snapshots block application pending explicit resolution. Keep imports that predate the active opening snapshot outside the eligible matching window unless a supported policy explains them.

Use stable source transaction IDs with account/source namespaces for bank deduplication. An exact file hash detects a repeated import, but identical dates, amounts, and descriptions do not prove that two payments are duplicates. Report changed content under an existing source ID as a conflict.

Application accepts the proposal revision, expected source/balance versions, authenticated reviewer, and idempotency key. Inside one PostgreSQL transaction, lock affected records in a stable order, revalidate ownership and versions, recompute availability, insert applications and audit events, and commit. Competing proposals sharing an invoice/credit/payment must not overconsume it. An idempotency key reused with a different payload is a conflict, not a successful retry.

Source or balance changes invalidate affected pending proposals. Changes after application create a review flag; they never rewrite financial history. Reversal is explicit, audited, idempotent, and restores only the amounts consumed by that application. No deletion of applied history.

### Jobs and workflow

Use a PostgreSQL-backed job table with atomic claims, expiring leases, attempt limits, recovery, and durable completion. SKIP LOCKED may be used to claim jobs, not to omit financial records during application. Never hold a financial transaction open while calling the model.

Keep the worker runnable as a separate process. Use a bounded, typed LangGraph path only for exception interpretation when checkpoints/resumption provide value. Verify at the HTTP-entrypoint level that real requests execute the compiled graph, not a separate manual fallback. State must contain the actual observations and source content needed downstream; logging tool output without supplying it to interpretation is insufficient.

Check cancellation and budgets between stages; cancel in-flight work only where the transport supports it. Interrupted jobs can retry. Business effects remain idempotent; do not claim exactly-once provider calls. Graph state is never the financial source of truth.

## 4. Reuse policy

Inspect only relevant paths from these user-owned repositories, read-only:

- RonaldoJ24/incident-lens: FastAPI boundaries, persistence patterns, provider handling, checkpoint ideas, CI/deployment templates, trace and evidence identifiers.
- RonaldoJ24/supplierops-lab: review UI patterns, cash-versus-adjustment conventions where applicable, schema validation, approval separation, redaction, and failure tests.

Begin with a bounded reuse inventory in docs/REUSE.md: source repository, source commit/path, behavior, intended reuse, test required, and license/attribution. Source docs are leads, not proof that implementations are correct. Read the concrete functions and their callers before copying.

Do not copy entire repositories, histories, lockfiles, databases, credentials, deployment IDs, fixture answers, old model metrics, Electron IPC code into a web server, or model-selection conclusions. Do not port token-overlap checks as semantic entailment. Do not assume imported files in SupplierOps already produce authoritative extracted facts. Do not copy Incident Lens workflow dispatch/checkpoint behavior without testing the actual entrypoint.

Extract a small tested component only when reuse is cheaper and clearer than rewriting. The new domain engine and training/evaluation contracts are Reconcile-specific. Do not turn reuse into a preliminary platform-building project. Record additional debt discovered in source repositories; do not modify those repositories under this task.

## 5. Data and evaluation integrity

Build the hidden ledger and intended relationships first; generate visible files separately. Runtime must work on arbitrary schema-valid inputs, not just generator output. Include a human-authored CSV/TXT packet processed through the same parsers.

Private labels, scenario family tags, generator seeds, expected answers, and historical outcomes unavailable at decision time must not enter prompts, feature columns, retrieval indexes, API responses, or production images. Neutral IDs and shuffled order must not encode the answer. Model metadata may identify a dataset version, never its labels.

Keep input-only data and evaluation targets in separate locations and interfaces. Use explicit runtime build allowlists and a test proving that application code and images cannot load target files. Implementing agents must not inspect sealed test labels to fix results. The evaluation runner can read them after the system is frozen. Record who authored/reviewed labels; agent-generated does not mean independently or manually validated by a domain expert.

Generate 5,000 payment-case groups initially, not 5,000 correlated candidate rows. Partition approximately 60% training, 20% validation, 10% calibration, 10% final test at the group level. Variants of one latent case stay together. Include disjoint entity/business groups and held-out language/template families. Keep related invoice/payment histories out of both sides of a purported unseen-entity evaluation. Fit preprocessing on training only. Report actual group and candidate counts.

Build an additional 50-packet challenge suite: 30 development, 20 sealed test. Cover exact reference, single partial payment, bundled invoices, credit linkage, abbreviated/ambiguous payer identity, misleading equal-amount alternatives, no valid match, conflicting evidence, out-of-scope cases, and prompt-like document content. Include genuinely underdetermined cases labeled NEEDS_REVIEW. Use reviewed acceptable-allocation sets where equivalent answers are defensible, not an arbitrary exact label.

A small challenge set diagnoses failures; it is not a production-accuracy benchmark. Different random seeds alone do not demonstrate distribution shift. Freeze generator/template/split manifests and record hashes before final evaluation. After test failures influence changes, retire those cases into regression data and create a new untouched test version. Do not repeatedly tune on the same test set and call it held out.

Synthetic data is the initial source. Do not scrape personal financial records, access the owner's connected accounts, or request real customer documents as a prerequisite for local work. Document a later permissioned-data contract: decision-time bank/invoice snapshots, messages available then, final reviewed allocations, retention/publication permissions, consistent pseudonymization, and adjudication. Post-reconciliation records must not leak the answer into a pre-reconciliation test.

## 6. Matching, ML, and model boundaries

### Deterministic baseline

Retrieve plausible invoices using documented reference, identity, date, currency, and available-balance features. An abbreviated name is uncertain identity, not authorization. Honor exact references where valid; do not guess solely from equal amounts.

Start with at most ten candidate invoices, groups of one to three, and one eligible credit. Record truncation. Measure whether retrieval contains all records necessary for the correct allocation and whether enumeration generates it. Route unsupported splits to review rather than brute-forcing arbitrary distributions. A credit's target must be supported explicitly or remain ambiguous. No undocumented oldest-first rule.

### Actual learned model

Implement a reproducible scikit-learn pipeline with a regularized logistic-regression baseline and one tree-based challenger. Avoid a broad hyperparameter search. Run training and large evaluation locally, not on Render or against the hosted Neon preview. Train candidate correctness scores and rank within each payment context; do not misrepresent a classifier as a dedicated learning-to-rank algorithm.

Features may include reference similarity, identity evidence, date distance, amount residual, group size, credit compatibility, explicit remittance mentions, and missing/conflicting signals. Use histories only when available as of the decision. Weight/group examples so payments with many negative candidates do not dominate. Test that artificial IDs, row order, and irrelevant wording changes do not become shortcuts.

Choose bounded model sizes and one inference thread; measure serving memory against the selected free instance before release. Export a versioned model artifact with training split, feature schema, dependency versions, seed, metrics, and digest. Load only an internally built, integrity-checked artifact; never deserialize an uploaded model. Verify offline/online feature parity and predictions after save/load. Demonstrate actual runtime inference with model ID and version.

Compare models on validation before choosing. Calibration is a separate experiment on disjoint groups. Raw classifier output is a ranking score, not operational confidence. To claim a probability for the selected proposal, evaluate calibration after selection; candidate-level calibration alone does not establish top-choice reliability. Select routing thresholds without touching final-test labels. All applications still require human approval in this release.

If ML does not improve a defensible validation precision/coverage trade-off, retain the simpler path as default and serve the learned model only as a clearly labeled experimental/shadow mode. Report it as not promoted. This is a valid experiment result, not a reason to change labels or expand scope.

### Runtime language model — DeepSeek only

Use the owner's existing DeepSeek API credential. Default to `deepseek-flash` at `https://api.deepseek.com`; keep the model ID configurable and verify it through the authenticated model catalog when available and a minimal, explicitly budgeted structured smoke request. Do not infer model access from an old repository or silently substitute a different provider. Current official pricing lists the Flash alias; record the requested and response model IDs and verification date because aliases can change their backing model.

For chat completions explicitly pass `thinking: {type: "disabled"}`, `response_format: {type: "json_object"}`, `stream: false`, and `max_tokens: 2048`. In an OpenAI-compatible SDK, pass the thinking field using the documented extra-body mechanism; the API base must remain DeepSeek. A direct small httpx adapter is also acceptable. No OpenAI API key or billing account is required by this application. JSON mode is not schema adherence or semantic correctness: explicitly request JSON, parse the final content, and validate with strict Pydantic schemas. Empty, malformed, truncated, unknown-ID, or unsupported outputs remain failures/review states. No second-model escalation or unbounded repair loop.

The interpreter receives actual relevant source spans, candidate IDs, validated balances/amounts needed to understand the relationship, and decision-time context. It may propose evidence-backed relationships and extracted amounts/references, but authoritative money comes from the records and deterministic validation. It cannot call apply/reverse, invent policy, execute SQL, access arbitrary URLs, or change identity/authorization. Treat document instructions as untrusted content. A keyword filter alone is not a complete prompt-injection defense. Escape content in the UI and verify output schema, IDs, spans, and financial constraints.

A citation proves that a span exists, not that it entails the interpretation. Evaluate contradictory and negated evidence directly. Provider failures yield explicit unavailable/failed/needs-review states, never a replay disguised as live output. Rules-only and learned-ranker modes remain usable without live inference, with their actual mode shown.

Use at most ten candidate invoices and five relevant evidence spans. Target 2,000–3,000 input tokens and enforce a 6,000-token input ceiling including system/schema overhead; cap output at 2,048 tokens. Use a verified tokenizer or a conservative bound, not an unqualified character-to-token estimate. Truncating needed evidence produces an explicit review outcome. One interpretation call per ambiguous payment, at most one retry for transient transport/rate/server errors, a 20-second per-attempt deadline and 45-second overall bound, with concurrency one in the free profile. Do not retry bad credentials or insufficient credit. Reject oversized/truncated requests rather than increasing the model context or token cap silently.

Cache successful interpretation by workspace, source hashes/versions, candidate context, model/configuration, prompt/schema version, and budget policy. Source edits invalidate the key. Label replay/cache results and distinguish application-cache hits from provider prompt-cache hits. Never cache financial application decisions. Keep stable instructions before variable content, but do not pad prompts to induce caching or add a separate cache service. Cache and quota state live in Neon.

Live calls require an explicit enable switch and a nonzero configured budget. The example free-profile safety ceilings are USD 0.10 per UTC day and USD 1.00 per UTC calendar month, not an instruction to spend those amounts or evidence of a provider-enforced cap. Keep hosted provider access invite-gated initially, no more than five requests per session and 25 attempts globally per day; retries count. The smaller money/call/token limit wins. Reserve worst-case estimated cost transactionally before every attempt, persist usage and outstanding reservations, and stop when exhausted. Retain reservations for unknown-billing outcomes; a timeout may still be billed. An app budget cannot cap charges from other applications sharing the API key. No auto-top-ups or automatic budget increases.

Verify and date a rate card from official DeepSeek pricing at implementation. Budget using uncached peak rates, without assuming off-peak or cache discounts. Track observed input/output/cache token counts and response model identity; missing usage is unknown, not zero. If the model or rates cannot be verified, leave live inference disabled and continue local/model-free work. Perform model comparisons only through explicit budgeted commands, never on deployment, page load, ordinary CI, or every generated case.

### Comparisons

Evaluate rules only, learned ranker without LLM, direct LLM, and hybrid. Controlled reasoning comparisons use identical parsed records, candidate sets, evidence availability, output contracts, and validators. Measure candidate retrieval separately and then compare complete file-to-allocation paths. Use the same model/budget for direct-LLM and hybrid interpretation unless the report explicitly treats model choice as another variable. Include timeouts and missing outputs in denominators. Do not weaken the baseline prompt to make hybrid win.

Report candidate recall, feasible-candidate recall, per-payment ranking, exact cash-and-credit allocation correctness, proposal precision, coverage, abstention on underdetermined cases, incorrectly allocated value, invalid outputs, failures, latency, tokens, and measured/estimated cost. Define denominators. Compare paired cases and report per-family counts and failures. Include an ablation removing remittance evidence and a counterfactual changing relevant evidence while preserving superficial context.

## 7. Phases and exit gates

### Phase 0 — Preflight, ownership, and contracts

Verify the current directory, git status, CLI versions and authentication for gh, Render, and Neon without printing secrets. Read existing AGENTS.md instructions. Do not recursively scan unrelated repositories or home directories.

Create a new private RonaldoJ24/reconcile repository unless that exact owned repository already exists and is verified as this project. Never overwrite a similarly named repository or dirty checkout. Inventory only needed existing resources. Create new project-owned cloud resources later, not during exploratory scaffolding.

Freeze input schemas, snapshot semantics, domain invariants, API shapes, model boundaries, and phase statuses. Complete the reuse inventory. Establish an initial project commit and short-lived phase branches. Configure tooling, migrations, test commands, and dependency locks. Phase 0 must lead into code, not become an extensive design-document exercise.

Exit: tests run, no secrets tracked, contracts are explicit, and source repositories are untouched.

### Phase 1 — Ingestion and transactional domain core

Implement actual CSV/TXT parsers, bounded source storage, row-level validation reporting, source versioning, import conflicts, opening snapshots, baseline retrieval/allocation, review revisions, application, reversal, and export. Use PostgreSQL for integration tests, not a SQLite substitute for locking/concurrency behavior.

Start financial property tests now. Reject unsupported rows visibly and report accepted/rejected counts. Neutralize CSV formula injection on export and enforce upload size/row/encoding limits. No binary document processing yet.

Exit: from an empty database, arbitrary schema-valid files produce a reviewable baseline proposal; application/reversal persist; reimport and repeated requests cannot duplicate effects.

### Phase 2 — Usable end-to-end application

Build the three screens against the actual API. Add job processing, source inspection, revision checks, correction, explicit application/reversal, and export. Protect workspace access from the first public boundary. Use server-issued unguessable, expiring credentials; no trusted client-supplied workspace ID. Cookie authentication needs CSRF/origin protections and appropriate secure cookie settings.

Implement an isolated synthetic public-preview mode and a local/private mode. Public preview accepts only bounded generated sample packets; do not imply arbitrary confidential uploads are safe because a banner requests synthetic data. Until proper private-user access and retention controls pass, private uploads remain disabled on the public deployment.

Exit: Playwright drives fresh-file import, review/correction, application, restart/reload, export, and reversal against a real backend. Validate mobile/desktop layout, not just build success.

### Phase 3 — Dataset, ML training, and model-serving evidence

Implement the generator, disjoint splits, challenge manifests, independent evaluator, rules comparison, two scikit-learn candidates, artifact verification, and online ranking. Lock final-test data before model selection. Finish the required tests for feature parity and save/load equivalence.

Exit: reproducible train/evaluate commands; a real model artifact; observed runtime predictions; validation results and an explicit promotion or shadow-mode decision. Synthetic scope is visible everywhere metrics are summarized.

### Phase 4 — Bounded LLM interpretation

Implement the provider adapter and compiled exception workflow. Use actual imported observations. Build the direct-model baseline and hybrid mode; keep ledger writes outside model tools. Add structured failure, citation-existence checks, evidence-change regressions, timeout/cancellation behavior, cache invalidation, and total-call/token limits.

Exit: authorized live calls are observed on development cases; altered evidence reaches inference and changes or invalidates the result; provider failure cannot produce a fake finding or financial application. When credentials/budget are absent, mark this phase blocked, not complete.

### Phase 5 — Freeze and release evaluation

Freeze code, feature/parser/prompt/model versions, dataset manifests, and routing thresholds. Run the sealed test once for the release and record what was accessed. Human/owner review of challenge semantics is a separate status, not invented by an agent.

Engineering gates: zero financial-invariant violations across at least 1,000 generated stateful operation sequences; 25 repeated contested-balance races; deterministic idempotency/reversal tests; source-staleness tests; restart/lease recovery; cross-workspace denial; provider failure/injection tests; frontend E2E; artifact/secret scans. Counts do not replace reviewing coverage or test quality.

Run a local workload of 1,000 payments and 10,000 invoices. Proposed budgets are warm list/detail p95 below 500 ms and non-provider matching below 30 seconds on recorded hardware. Separate provider latency, cold starts, and hosted measurements. A miss is reported, not hidden by dropping hard cases.

Write machine-readable metrics and a short human-readable comparison with sample sizes, failures, denominators, provenance, cache behavior, and limitations. No fabricated success threshold such as 99% accuracy. Do not require hybrid to win; require an honest result and safe behavior. Do not promote unsupported autonomous decisions.

Exit: all hard safety gates pass; limitations and unmeasured items are explicit; versioned results reproduce. A small synthetic result is never called real-world validation.

### Phase 6 — Render / Neon deployment and independent smoke

Use the installed CLIs and their current help/docs. Create only new Reconcile-owned resources. Record project/service IDs, region, plan, repository, and deployed SHA without secrets. Never repurpose Incident Lens/SupplierOps services or databases. Use verified free quotas only. No plan upgrades, paid workers, persistent disks, cron jobs, paid logging, billing changes, registries requiring paid storage, or additional subscriptions. An unavailable free resource is a deployment blocker, not permission to incur charges. Inspect shared usage, build/bandwidth overage behavior, and GitHub Actions budgets before creating resources; never alter account-wide billing or other applications without authorization.

Neon: dedicated project/database and isolated test environment; pooled connections for runtime and direct connections for migrations/session-bound operations; limited pool sizes and credentials. Test migrations and relevant locking against the actual selected endpoints. Never run destructive integration tests on the deployed application database. Use bounded private BYTEA/text storage in PostgreSQL for the small CSV/TXT preview, with hashes, quotas, and lazy cleanup of expired synthetic workspaces. Set a conservative overall database-usage stop before the verified storage allowance; do not delete applied private records to reclaim space. Runtime pooling should start small (for example two connections and no overflow), with recovery tested against the actual endpoint. Do not assume pooled session locks behave like direct connections; do not rely on Render's ephemeral disk. This is an explicit initial small-file trade-off, not the storage design for large documents.

Render: require exactly one Free same-origin web deployment serving the built frontend and FastAPI. Use the provider-issued hostname, no new domain purchase, and no separate Node frontend server. Configure explicit deployment from a reviewed main commit rather than rebuilds for every agent push. The no-new-cost preview profile runs one managed queue consumer within the web service lifecycle, while keeping the standalone worker entrypoint for local/approved always-on deployment. Use one web process for this profile. Run blocking work outside the ASGI event loop. Jobs/checkpoints remain durable in Neon; resume expired leases after wake/restart. Do not promise processing while a free service sleeps, create artificial keep-alives, or poll the database continuously when idle. Wake the consumer on enqueue, startup/recovery, and authenticated active-user requests; stop polling when there is no pending work. Release DB transactions/connections between tasks. Platform liveness checks must not query Neon; keep bounded database readiness checks separate. Do not add scheduled pings, unattended retry sweeps, or a LISTEN connection that prevents the intended idle behavior. Show actual starting/queued/running/unavailable states. Do not deploy a separate worker under this brief. Preserve its local entrypoint for testing and a future separately authorized hosting decision.

Use an idempotent startup migration command and appropriate direct-connection lock rather than assuming a paid pre-deploy command exists. Apply migrations once under that lock before serving; fail readiness when schema/state is incompatible. Store secrets through supported secret mechanisms, not visible command arguments, logs, repo files, or frontend variables. Build an image excluding evaluation labels, private datasets, local env files, and credentials. Serve only verified internally produced model artifacts.

Before exposing a provider-backed preview, enforce the invite gate, transactional per-session/global call/token/estimated-money limits, bounded inputs, expiry/cleanup, same-origin protections, and a provider kill switch. Render and GitHub quota alerts are not necessarily hard spending caps; if bandwidth overage cannot be blocked safely under the account settings, record the risk and do not call the setup guaranteed free. A session-only limit is insufficient because visitors can create sessions. Never run load tests against the shared free deployment.

Smoke from outside the process: fresh isolated session, import, actual rules/model/provider mode, source inspection, apply, repeated apply, source update, review flag/reversal, export, and persistence across restart/redeploy where feasible. Record exactly which smoke steps ran. No static fallback when the backend is unavailable.

Exit: hosted synthetic beta tied to a green CI SHA, durable state/source storage, verified actual inference mode, bounded costs, documented wake limitations and rollback. Repository remains private unless publication is explicitly authorized. Do not call this production-ready finance software.

### Phase 7 — Permissioned validation, not automatic execution

Provide a data-request template and import adapters for later permissioned historical cases. Plan approximately 50–100 initial cases and a subsequent prospective batch. Measure reviewed allocation errors, corrections, active review time, and model cost. Do not claim collected data, contacted users, domain review, or time savings that did not occur. No autonomous outreach or account access.

## 8. Engineering, commits, and token discipline

One coordinator owns domain contracts and integration. Use at most two concurrent implementers, assigned non-overlapping files in separate worktrees. Only the coordinator changes shared financial schemas/migrations unless explicitly delegated. Tests and review are distinct activities, not additional agents recursively reviewing each other.

Use short-lived branches and small, coherent commits: for example, `feat(ingest): validate invoice snapshots`, `feat(ledger): apply allocations transactionally`, `test(ledger): reject concurrent over-allocation`, `feat(ml): add candidate ranking pipeline`, `fix(workflow): preserve evidence in model context`, `chore(deploy): add bounded preview profile`.

Commit behavior and its tests together where appropriate. Review staged diffs, preserve unrelated user changes, and do not force-push or rewrite shared history. PRs summarize behavior, exact checks, and known limits. Merge only after relevant checks pass. Release a tagged version only after its gates. Do not make ceremonial commits per generated file or conceal failing work in a large final commit.

Use small cohesive functions, strict types at boundaries, dependency injection where it improves testing, and narrowly scoped error handling. Comments explain non-obvious invariants, concurrency/precision decisions, or external constraints—not what the next line does. No redundant docstrings, marketing prose in code, unexplained bypasses, swallowed exceptions, placeholder success, or TODOs presented as working functionality. Do not add dependencies without a concrete need.

Keep root AGENTS.md short. Do not copy this whole brief into it. After initial intake, read only the active phase and relevant contract files. Use targeted search and bounded file ranges instead of recursive dumps. Do not paste raw datasets, lockfiles, full logs, or all source repositories into model context. Store detailed command output locally and return a summary plus relevant error lines.

Use the selected main coding model for design/integration; delegate bounded, well-specified work to the smaller model. Do not spawn agents merely for parallelism or enable Max/Ultra globally. Run targeted checks during iteration and the full offline suite at phase/release boundaries. Live provider tests and final-test evaluation must be explicit budgeted commands, not hooks on every save or ordinary CI job.

Runtime and development token budgets are different. Track available Codex usage with the owner's existing tooling when available; do not invent exact token totals. For runtime, log model, input/output tokens when observed, reasoning tokens when reported, latency, retries, cache hits, and estimated cost from a dated rate card. Missing usage is unknown, not zero. Reserve budget before concurrent calls; fail closed when the authorized total budget is exhausted. Do not pad prompts merely to trigger provider caching.

Maintain docs/STATUS.md after each phase in at most roughly 500 words: completed behavior, current phase, exact checks/results, last commit, blockers, deviations, and next bounded task. Preserve detailed metrics elsewhere. Phase summaries should not repeatedly rewrite the entire project plan.

## 9. Commands and completion report

Implement a small documented command surface, including a no-charge `make cost-preflight` plan/quota/configuration audit with no generation calls, plus commands such as `make setup`, `make dev`, `make check`, `make test-integration`, `make data-dev`, `make train`, `make evaluate-dev`, `make evaluate-release`, `make smoke-live`, and `make perf`. The exact tooling may vary, but each documented command must exist and run. Offline tests must not require provider credentials. Release evaluation and live tests require explicit flags/budgets. Guard destructive test commands against non-test databases.

At the end of the authorized work, report what actually runs; phase status; commit/PR/check references; actual model and inference mode; data/split/artifact hashes; measured versus unmeasured results; deployment IDs/URL/SHA if deployed; blockers; and the next task. Never imply a task succeeded because its code was written. Do not claim future background work.

The first execution is authorized for Phases 0–2 only: deliver the real deterministic file-to-allocation vertical slice and a checkpoint. Later executions advance to ML, interpretation, evaluation, and deployment using this same brief. Do not attempt the entire roadmap in a single unfocused run or stop after producing only a plan.
