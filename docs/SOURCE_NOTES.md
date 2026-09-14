# Source and decision notes — free deployment revision

Prepared 2026-09-14 from the supplied Reconcile project package and the owner's instruction to use existing Render/Neon/GitHub free access and their DeepSeek API. The full project/evaluation specification is retained; hosting and provider defaults are revised. These are specifications, not an implemented or deployed application.

## Official documentation consulted for this revision

- Render free instances and limitations: https://render.com/docs/free
- Render build pipeline quotas and spending controls: https://render.com/docs/build-pipeline
- Neon pricing: https://neon.com/pricing
- Neon free-to-production FAQ: https://neon.com/faqs/postgres-services-free-to-production
- Neon maintained free-plan FAQ source: https://github.com/neondatabase/website/blob/main/content/faqs/free-plan-limits-and-quotas.md
- GitHub Actions billing: https://docs.github.com/en/billing/concepts/product-billing/github-actions
- DeepSeek model catalog and pricing: https://api-docs.deepseek.com/quick_start/pricing/
- DeepSeek thinking toggle: https://api-docs.deepseek.com/guides/thinking_mode/
- DeepSeek JSON output: https://api-docs.deepseek.com/guides/json_mode/

The Neon pages were available in indexed official-source results; direct page retrieval returned an unsupported Markdown content type. No private account entitlements, balances, payment methods, or API key were inspected. Codex must verify actual limits through the owner's authenticated CLI/account access before provisioning. The DeepSeek model-list endpoint was not successfully fetched in this revision; account-specific model access is unverified.

The official DeepSeek pricing page on this date lists `deepseek-flash` and non-thinking support. For budgeting, Flash peak uncached-input/output prices are USD 0.30 / USD 1.20 per million tokens; off-peak prices are half. These are dated prices, not a permanent guarantee. Example arithmetic for 3,000 uncached input and 1,000 output tokens is USD 0.0021 at peak per call (USD 2.10 for 1,000 identical calls), before retries. Do not assume provider-cache discounts in a worst-case reservation.

## Sources preserved for code reuse

Audit RonaldoJ24/incident-lens and RonaldoJ24/supplierops-lab locally, read-only, at their actual current commits. Concrete behavior and tests determine reuse; old docs are not proof. No source code or credentials are included here.

## Boundaries

The free profile uses one service and a consumer that runs only while the service is awake. Small source payloads and financial state persist in bounded PostgreSQL storage. No hosted training, always-on processing, or zero-overage guarantee is implied by a free plan label.

User approval of a future phase is separate from evidence that it ran. No cloud resources, repository, provider credit purchase, billing change, or deployment was created by preparing this package.
