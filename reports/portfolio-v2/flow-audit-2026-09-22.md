# Flow audit and live interpretation progress

This follow-up responds to the owner's request to audit the complete app flow,
check the earlier work, and show what interpretation is doing while it runs.
The existing financial approval boundary and public provider spending limits
remain in force.

## Earlier work and reproduced gaps

- **Complete Reconcile portfolio enhancements** added a persisted decision trace,
  comparisons and controlled variants. That trace described completed execution;
  it did not stream progress during an interpretation request.
- **Investigate portfolio reconciliation** diagnosed session-token rotation across
  browser tabs. Its later prompt-size fix did not resolve that separate issue.
- On the deployed visual update (`2370497`), opening a second tab and then opening
  a case in the original tab produced `403: CSRF token required`.
- The previous interpretation action displayed a single busy label until the full
  response arrived. A previous failure could remain visible during a new attempt.
- DeepSeek actions disappeared on resolved proposals without explaining why.
  Eligibility follows proposal state and provenance; it does not indicate that
  some payments have a different provider subscription.

## Verification environment

Browser inspection uses the actual React app and API, with screenshots at desktop
and mobile sizes. The local API uses an isolated, schema-only Neon Free branch,
`test-flow-progress-20260922`, created for this audit with an expiry. It contains
synthetic test records, not copied production records. PostgreSQL tests use a
separate guarded schema within that branch. Provider test responses are identified
as stubs and do not count as live DeepSeek evidence.

The existing Render Free service and public provider limits are unchanged:
USD 0.50 per UTC day, USD 5 per UTC month, with the existing session, attempt,
token and concurrency limits. No model promotion or benchmark is part of this audit.

## Implementation contract

The additional POST interpretation stream sends stage events from actual workflow
boundaries and ends with the existing interpretation response. Authentication,
CSRF, provider access and admission checks run before streaming. The worker uses
its own database session; the request's authentication transaction is released
before provider work. Event buffering and stream duration are bounded.

Leaving the proposal aborts the browser request, ignores obsolete responses and
signals cancellation to the workflow. An already-running provider HTTP call may
finish and incur usage; accounting is preserved. Cancellation is checked before
recording a result. A timeout does not claim that a concurrent save could not have
completed: the user is told to refresh the saved state. Financial application
remains a separate, explicit reviewer action.

The browser retries only a structured `csrf_required` rejection, once, after a
shared session refresh. It does not retry an interrupted provider stream or an
arbitrary permission/network failure. Completed-stage labels and elapsed time
describe observed work; there are no fabricated thoughts, percentages or delays.
Saved interpretation results retain their existing cache/provider provenance.

## Observed financial flow

The bundled synthetic payment was opened through the browser. Its approval
dialog separated MXN 54,000 cash from MXN 1,000 credit and required an explicit
confirmation. After approval, invoice balances were zero. An explicit reversal
restored unapplied cash to MXN 54,000 and invoice balances to MXN 30,000 and
MXN 25,000, retaining the application and reversal in the decision history.

The conflicting-instructions case was corrected through the browser to allocate
MXN 10,000 to `case-correction-second`. Saving created revision 2 in `PROPOSED`
state without applying it. Unsaved edits disabled comparison and case-lab actions.
The interface explained that a reviewer supplied this revision's allocation.

Opening a second local tab rotated the session token. Opening a new case from
the original tab then succeeded through automatic session recovery, reproducing
the exact previously failing interaction without a user-visible error.

The repository's synthetic CSV/TXT import packet was uploaded through the browser:
five rows accepted, zero rejected, explicit commit, matching job `SUCCEEDED`, and
a new review-queue entry. The imported message was readable in the source dialog
at 390px, with document width equal to viewport width. Source content and provenance
remain separate. Downloading the application export returned HTTP 200; CSV content
is covered by existing backend tests rather than inferred from that browser click.

Screenshots are retained outside the repository in the owner's output directory:
`outputs/reconcile-flow-audit-2026-09-22/`, including the reproduced stale-session
error, approval confirmation and restored balances.

## Rendered streaming checks

An untracked, explicitly test-only wrapper used the production workflow and
DeepSeek adapter with an in-memory HTTP response delayed by 6 or 15 seconds.
It could not send requests to DeepSeek. The fresh correction case displayed
completed preparation stages and `Waiting for DeepSeek` before receiving a result,
on 1440px desktop and 390px mobile. Both Direct and Hybrid returned the stub's
abstention as a persisted review-required revision. Financial actions were disabled
during execution and balances were unchanged afterward. These captures are
`desktop-stream-stub-progress.png` and `mobile-stream-stub-progress.png`.

During another Direct run, the browser showed the provider waiting stage at ten
seconds. Navigating to Cases aborted the stream without leaving an error or late
busy indicator. Reopening after the delayed provider finished still showed revision
5, proving that this interrupted run did not persist a late revision.

Two additional gaps found during this audit were corrected. Model-visible prompt
serialization omits redundant server bookkeeping while preserving candidate,
payment, invoice, credit and citation facts. The full typed request still governs
validation and cache identity; the prompt version advances to v2. The 8,000-byte
and 6,000-token limits are unchanged. In the accumulated browser workspace, the
previously failing imported payment reached the stub provider with nine invoices
and ten candidates and saved a review-required revision.
The nine-invoice/nine-candidate regression measured 6,102 bytes for Direct and
6,847 for Hybrid, compared with the observed 8,147 and 8,892 before compaction.

New interpretation revisions persist the observed stages using the existing
decision-trace schema. The browser showed all eight live-path stages, with unknown
individual timings explicitly unmeasured. Repeating Direct returned a validated
cache result and a new trace explicitly marking the provider call skipped. The
successful record stage was completed and unapplied cash stayed MXN 54,000.
Existing immutable revisions are not rewritten. These are local stub checks;
live hosted provider acceptance is recorded separately below.

Hybrid also completed in the accumulated browser workspace with ten ranked
candidates. Its persisted trace marked ranking completed. At 390px, the completed
history remained readable and the document width matched the viewport width.

## Local acceptance

- `uv run pytest -m 'not postgres'`: 1,104 passed, 66 deselected.
- Provider prompt regression file: nine passed, including bounded larger context.
- Workflow PostgreSQL file: six passed, covering progress before provider
  completion, live/cache revision persistence and financial separation.
- Focused PostgreSQL route/auth checks: two passed, 58 deselected, including the
  public interpretation stream and structured CSRF rejection.
- Frontend: 47 tests passed; TypeScript check and production build passed.
- Ruff passed; mypy passed across 44 source files; `git diff --check` passed.

The offline suite ran before a final normalization that makes the nested stored
trace agree with the completed top-level record stage. All six workflow PostgreSQL
tests were rerun afterward and passed in 121.13 seconds (one warning), followed by
Ruff, mypy and diff checks. Frontend files were unchanged after their passing checks.
These tests used stub provider responses, not live inference.

## Deployment acceptance

Local implementation and acceptance are complete. The owner has authorized
deployment to the existing preview service. The associated pull request records
the merged commit, Render deployment identity and subsequent bounded hosted
Direct/Hybrid verification; hosted results are not inferred from local tests.
Screenshots and the final deployment record are retained in the owner's
`outputs/reconcile-flow-audit-2026-09-22/` directory.
