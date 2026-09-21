# Public provider access

The hosted preview remains invite only by default. Set
`RECONCILE_PUBLIC_PROVIDER_ACCESS=1` only when the server-side DeepSeek
configuration is live and its daily, monthly, session and execution budgets
have been reviewed. The flag applies only to `RECONCILE_MODE=preview`.

Public access is evaluated for each request. Ordinary preview sessions keep
their stored `provider_access=false`, so changing the flag back to `0`
immediately revokes their effective access without a migration or session
cleanup. Sessions unlocked with `RECONCILE_PROVIDER_INVITE_SHA256` retain
their stored invitation access when the public flag is disabled.

The API key stays server side. Visitors can request only the existing bounded
Direct or Hybrid interpretation flow after the normal CSRF, workspace, source,
validation and quota checks. The provider kill switch and configured
credentials, five requests per session and 25 attempts per UTC day remain
required. Public preview uses `RECONCILE_LLM_DAILY_BUDGET_USD` and
`RECONCILE_LLM_MONTHLY_BUDGET_USD`, defaulting to USD 0.10/day and USD 1/month
and accepting at most USD 0.50/day and USD 5/month. The daily value is also the
per-UTC-day execution reservation cap, with an execution ID formed from the
configured prefix and that UTC date, so a prior day cannot consume the next
day's execution allowance. The legacy
`RECONCILE_LLM_EXECUTION_BUDGET_USD` value remains the USD 0.05 cap for local
or invite-only runtime and is ignored for public preview.
