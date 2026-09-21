# Controlled evidence and reliability checks

Registered cases can switch among the original message, an ambiguous message and
a prompt-like message. The server owns these synthetic bytes. A changed variant
supersedes the active message, preserves its immutable source and earlier decisions,
increments the payment version, and schedules ordinary matching. Reopening or
requesting the unchanged active variant resumes it. Restoring the original message
reuses its source; it does not reset balances or erase review history.

A changed variant, its audit event and the follow-up work are committed together.
Pending decisions and their comparisons become stale. A new comparison requires
the newly processed revision. Applied and reversed histories cannot be rewritten
through a variant request. These controls are scoped to the current session.

The reliability lab labels its injected inputs as synthetic before execution:

| Experiment | Real check exercised | Required state |
| --- | --- | --- |
| Invalid allocation | Shared financial allocation validator rejects an excessive amount | An available invoice |
| Invalid citation | Shared result validator rejects an unknown citation source | An available invoice |
| Stale apply | Transaction service rejects a deliberately mismatched version token | A proposed revision |
| Duplicate apply | Transaction service reuses the original approval payload and idempotency key | An already applied revision with its original operation identity |

The response reports the invoked validator, expected behavior, observed result,
application ID when applicable, and financial row counts and sums before/after.
The server derives pass/fail from these observations. The duplicate experiment
cannot create the first application; the reviewer must use the ordinary explicit
confirmation flow first. The experiment audit itself is not a financial effect.

These checks establish only the named validation or transaction behavior. They do
not evaluate semantic support, provider accuracy, adversarial robustness in general,
or production accounting suitability. No provider request or benchmark runs when
using the lab. Engineering evidence is recorded separately in
[the validation report](../reports/portfolio-v2/engineering-validation.md).
