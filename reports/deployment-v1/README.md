# Phase 6 deployment preflight

The deployment is prepared but intentionally withheld. The reviewed Blueprint
contains one Render Free Docker web service, explicit deploys only, no worker,
disk, cron, domain, or paid resource, and only pooled runtime/direct migration
database secrets. Live provider access remains off and the exposed DeepSeek key
was not placed in deployment configuration.

The existing Reconcile Neon Free project remains dedicated to this project. A
guarded direct-connection migration reached `0004_preview_lifecycle` on the
expiring test branch, and the pooled endpoint observed the same revision. The
main database was read only and remains unmigrated. Its measured database size
was 7,520,256 bytes, below the application's conservative 300 MiB admission
stop.

The hosted profile ran locally from the production frontend build against the
pooled test endpoint. Desktop and mobile browser checks completed validation,
commit, matching, correction, application, reload persistence, export, and
reversal. This was not an external hosted smoke. Docker/Podman was unavailable,
so no container-build result is claimed.

## Deployment blocker

Render CLI authentication and resource ownership are verified, but the CLI does
not expose whether the workspace has a payment method or a hard stop for shared
bandwidth overage. Dashboard inspection reached an interactive GitHub sign-in.
Render's documented Free behavior can charge overage when a payment method is
present, and the existing `incident-lens-api` service already shares the
workspace allowance. Under the project brief, the Reconcile service must not be
created until the owner verifies a no-charge boundary. No existing service or
account setting was changed.

GitHub Actions is enabled, but its allowance/no-overage state was unavailable to
the authenticated API. No workflow was added; the candidate relies on recorded
local and isolated-Neon checks instead of introducing unverified paid exposure.

Credential rotation remains listed in `docs/ROTATION_CHECKLIST.md`. The provider
preview must stay disabled until a replacement DeepSeek key and invite digest
are installed through supported secret storage.
