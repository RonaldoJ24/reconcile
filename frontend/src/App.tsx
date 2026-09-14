import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  ApiError,
  applyProposal,
  commitImport,
  correctProposal,
  createSession,
  exportUrl,
  getProposal,
  interpretProposal,
  listImports,
  listProposals,
  reverseApplication,
  runJobOnce,
  sampleUrl,
  sourceUrl,
  validateImport,
} from './api'
import type {
  CashLine,
  CreditLine,
  Evidence,
  ImportSummary,
  ImportValidation,
  Interpretation,
  InterpretationMode,
  JobState,
  Mode,
  ProposalDetail,
  ProposalSummary,
  RowIssue,
} from './types'

type Screen = 'imports' | 'queue' | 'detail'

const text = (record: Record<string, unknown> | undefined, ...keys: string[]) => {
  if (!record) return undefined
  for (const key of keys) {
    const value = record[key]
    if (value !== undefined && value !== null && value !== '') return String(value)
  }
  return undefined
}

const numberValue = (value: unknown): number | undefined => {
  if (typeof value === 'number' && Number.isFinite(value)) return value
  if (typeof value === 'string' && /^\d+$/.test(value)) return Number(value)
  return undefined
}

const centsOf = (record: Record<string, unknown> | undefined, ...keys: string[]) => {
  for (const key of keys) {
    const value = record?.[key]
    const numeric = numberValue(value)
    if (numeric !== undefined) return numeric
    if (typeof value === 'string' && /^\d+\.\d{1,2}$/.test(value)) {
      const [whole, fraction = ''] = value.split('.')
      return Number(whole) * 100 + Number(fraction.padEnd(2, '0'))
    }
  }
  return undefined
}

const money = (cents: number | string | undefined) => {
  const value = typeof cents === 'string' ? Number(cents) : cents
  if (!Number.isFinite(value)) return '—'
  return new Intl.NumberFormat('en-MX', { style: 'currency', currency: 'MXN' }).format((value ?? 0) / 100)
}

const dateTime = (value: unknown) => {
  if (!value) return '—'
  const parsed = new Date(String(value))
  return Number.isNaN(parsed.getTime()) ? String(value) : parsed.toLocaleString()
}

const proposalId = (proposal: ProposalSummary | ProposalDetail) =>
  text(proposal as Record<string, unknown>, 'proposal_id', 'proposalId', 'id') ?? ''

function errorText(error: unknown) {
  if (error instanceof ApiError) return `${error.status}: ${error.message}`
  return error instanceof Error ? error.message : 'Unexpected request failure'
}

function readableCode(value: string | undefined) {
  return value ? value.replace(/[_-]+/g, ' ') : undefined
}

function interpretationSource(source: Interpretation['source']) {
  if (source === 'live') return 'Live DeepSeek'
  if (source === 'cache') return 'Validated cache'
  return 'Unavailable'
}

function interpretationSummary(result: Interpretation) {
  if (result.status === 'selected') return 'An existing candidate was selected for review.'
  if (result.status === 'needs_review') return 'The interpreter could not safely select an existing candidate.'
  return 'Interpretation is unavailable; the current proposal is unchanged.'
}

function getList<T>(value: T[] | { items?: T[]; data?: T[] } | undefined): T[] {
  if (Array.isArray(value)) return value
  return value?.items ?? value?.data ?? []
}

function balanceRows(value: ProposalDetail['balances']): Record<string, unknown>[] {
  if (Array.isArray(value)) return value
  if (!value || typeof value !== 'object') return []
  return Object.entries(value).map(([invoiceId, row]) => (
    row && typeof row === 'object' && !Array.isArray(row)
      ? { invoice_id: invoiceId, ...(row as Record<string, unknown>) }
      : { invoice_id: invoiceId, remaining_amount: row }
  ))
}

function App() {
  const [screen, setScreen] = useState<Screen>('imports')
  const [mode, setMode] = useState<Mode>('rules-v1')
  const [proposals, setProposals] = useState<ProposalSummary[]>([])
  const [imports, setImports] = useState<ImportSummary[]>([])
  const [selectedId, setSelectedId] = useState('')
  const [startup, setStartup] = useState(true)
  const [error, setError] = useState('')

  const refresh = useCallback(async () => {
    const [importResult, proposalResult] = await Promise.all([listImports(), listProposals()])
    setImports(getList(importResult as ImportSummary[] | { items?: ImportSummary[]; data?: ImportSummary[] }))
    setProposals(getList(proposalResult as ProposalSummary[] | { items?: ProposalSummary[]; data?: ProposalSummary[] }))
  }, [])

  useEffect(() => {
    let cancelled = false
    void (async () => {
      try {
        const session = await createSession()
        if (!cancelled) setMode(session.mode || 'rules-v1')
        await refresh()
      } catch (cause) {
        if (!cancelled) setError(errorText(cause))
      } finally {
        if (!cancelled) setStartup(false)
      }
    })()
    return () => { cancelled = true }
  }, [refresh])

  const openDetail = (id: string) => {
    setSelectedId(id)
    setScreen('detail')
    setError('')
  }

  const navigate = (next: Screen) => {
    setScreen(next)
    setError('')
  }

  if (startup) return <div className="loading-page" role="status">Starting secure workspace…</div>

  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">Skip to content</a>
      <header className="topbar">
        <div className="brand-lockup">
          <span className="brand-mark" aria-hidden="true">R</span>
          <div><span className="brand">Reconcile</span><span className="brand-subtitle">Payment review</span></div>
        </div>
        <div className="session-meta" aria-label="Runtime mode">
          <span className="mode-dot" aria-hidden="true" />
          <span>rules-v1 · {mode}</span>
          <span className="mode-note">runtime</span>
        </div>
      </header>

      <div className="workspace">
        <aside className="sidebar" aria-label="Primary navigation">
          <p className="eyebrow">Workspace</p>
          <nav>
            <button className={screen === 'imports' ? 'nav-item active' : 'nav-item'} onClick={() => navigate('imports')}>
              <span aria-hidden="true">↥</span> Imports
            </button>
            <button className={screen !== 'imports' ? 'nav-item active' : 'nav-item'} onClick={() => navigate('queue')}>
              <span aria-hidden="true">☷</span> Review queue
              {proposals.length > 0 && <span className="nav-count" aria-label={`${proposals.length} proposals`}>{proposals.length}</span>}
            </button>
          </nav>
          <div className="sidebar-foot">
            <p className="eyebrow">Data boundary</p>
            <p>rules-v1 is deterministic and evidence-backed. Money is never applied without an explicit reviewer action.</p>
            <p className="muted">Use synthetic files in a public preview. Local/private mode is shown exactly as reported by the server.</p>
          </div>
        </aside>

        <main id="main-content" className="main-content">
          <div className="content-wrap">
            {error && <ErrorBanner message={error} onDismiss={() => setError('')} />}
            {screen === 'imports' && <ImportsView imports={imports} onError={setError} onRefresh={refresh} />}
            {screen === 'queue' && <QueueView proposals={proposals} onOpen={openDetail} onRefresh={refresh} onError={setError} />}
            {screen === 'detail' && (
              <DetailView
                id={selectedId}
                onBack={() => navigate('queue')}
                onError={setError}
                onRefresh={refresh}
              />
            )}
          </div>
        </main>
      </div>
    </div>
  )
}

function ErrorBanner({ message, onDismiss }: { message: string; onDismiss: () => void }) {
  return <div className="error-banner" role="alert"><span>{message}</span><button className="icon-button" onClick={onDismiss} aria-label="Dismiss error">×</button></div>
}

function ImportsView({
  imports,
  onError,
  onRefresh,
}: {
  imports: ImportSummary[]
  onError: (message: string) => void
  onRefresh: () => Promise<void>
}) {
  const [bank, setBank] = useState<File>()
  const [invoice, setInvoice] = useState<File>()
  const [credit, setCredit] = useState<File>()
  const [message, setMessage] = useState<File>()
  const [messageTime, setMessageTime] = useState('')
  const [paymentAccount, setPaymentAccount] = useState('')
  const [paymentTransaction, setPaymentTransaction] = useState('')
  const [validation, setValidation] = useState<ImportValidation>()
  const [busy, setBusy] = useState('')
  const [job, setJob] = useState<JobState>()

  const select = (setter: (file: File | undefined) => void) => (event: React.ChangeEvent<HTMLInputElement>) => setter(event.target.files?.[0])

  const validate = async (event: React.FormEvent) => {
    event.preventDefault()
    if (!bank || !invoice) {
      onError('Select both the bank CSV and invoice CSV before validating.')
      return
    }
    setBusy('validate')
    onError('')
    const form = new FormData()
    form.append('bank', bank)
    form.append('invoices', invoice)
    if (credit) form.append('credits', credit)
    if (message) form.append('message', message)
    if (messageTime) form.append('message_time', messageTime)
    if (paymentAccount) form.append('payment_source_account_id', paymentAccount)
    if (paymentTransaction) form.append('payment_transaction_id', paymentTransaction)
    try {
      const result = await validateImport(form)
      setValidation(result)
    } catch (cause) {
      onError(errorText(cause))
    } finally {
      setBusy('')
    }
  }

  const processJobs = async () => {
    setBusy('jobs')
    onError('')
    try {
      for (let attempt = 0; attempt < 20; attempt += 1) {
        const result = await runJobOnce()
        setJob(result)
        const state = String(result.state ?? result.status ?? '').toUpperCase()
        if (!['PENDING', 'RUNNING'].includes(state)) break
      }
      await onRefresh()
    } catch (cause) {
      onError(errorText(cause))
    } finally {
      setBusy('')
    }
  }

  const commit = async () => {
    const batchId = validation && (validation.batch_id ?? validation.batchId)
    if (!batchId) return
    setBusy('commit')
    onError('')
    try {
      const result = await commitImport(batchId)
      setValidation(result)
      await processJobs()
    } catch (cause) {
      onError(errorText(cause))
      setBusy('')
    }
  }

  return (
    <>
      <PageHeading eyebrow="Input" title="Imports" description="Bring in a bank snapshot, open invoices, and optional credit evidence." />
      <section className="notice-card" aria-label="Rules mode notice">
        <div className="notice-icon" aria-hidden="true">i</div>
        <div><strong>rules-v1 · evidence first</strong><p>The server will validate schema and retain source evidence. Validation does not commit business rows; commit and application are separate actions.</p></div>
        <a href={sampleUrl()} className="button button-secondary" download>Download synthetic sample</a>
      </section>

      <section className="panel import-panel">
        <div className="panel-heading"><div><h2>New import</h2><p className="muted">Files are checked before anything is committed.</p></div><span className="step-label">01 / 02</span></div>
        <form onSubmit={validate} className="import-form">
          <FileField id="bank-file" label="Bank CSV" required file={bank} onChange={select(setBank)} hint="source_account_id · transaction_id · amount" />
          <FileField id="invoice-file" label="Invoice CSV" required file={invoice} onChange={select(setInvoice)} hint="customer_id · invoice_id · outstanding_amount" />
          <FileField id="credit-file" label="Credit CSV" file={credit} onChange={select(setCredit)} hint="Optional · explicit invoice links supported" />
          <FileField id="message-file" label="Payment message TXT" file={message} onChange={select(setMessage)} hint="Optional · UTF-8 text with supplied context" />
          <fieldset className="context-fields">
            <legend>Message context <span className="muted">(required when a message is included)</span></legend>
            <label>Message time<input type="text" value={messageTime} onChange={(e) => setMessageTime(e.target.value)} placeholder="2026-09-14T10:00:00-06:00" aria-describedby="context-help" /></label>
            <label>Payment source account ID<input value={paymentAccount} onChange={(e) => setPaymentAccount(e.target.value)} /></label>
            <label>Payment transaction ID<input value={paymentTransaction} onChange={(e) => setPaymentTransaction(e.target.value)} /></label>
            <p id="context-help" className="field-help">The server records these associations as supplied context.</p>
          </fieldset>
          <div className="form-actions"><button className="button button-primary" type="submit" disabled={Boolean(busy)}>{busy === 'validate' ? 'Validating…' : 'Validate files'}</button></div>
        </form>
      </section>

      {validation && <ValidationResult result={validation} onCommit={commit} busy={busy} job={job} />}

      <section className="panel history-panel">
        <div className="panel-heading"><div><h2>Import history</h2><p className="muted">Workspace batches returned by the server.</p></div><button className="button button-quiet" onClick={() => void onRefresh()} disabled={Boolean(busy)}>Refresh</button></div>
        {imports.length === 0 ? <EmptyState title="No imports yet" body="Validate a bank and invoice CSV to start a review." /> : <div className="table-wrap"><table><caption className="sr-only">Import history</caption><thead><tr><th>Batch</th><th>Status</th><th>Created</th><th>Accepted</th></tr></thead><tbody>{imports.map((item, index) => <ImportRow key={String(item.id ?? item.batch_id ?? index)} item={item} />)}</tbody></table></div>}
      </section>
    </>
  )
}

function FileField({ id, label, required, file, onChange, hint }: { id: string; label: string; required?: boolean; file?: File; onChange: (event: React.ChangeEvent<HTMLInputElement>) => void; hint: string }) {
  return <label className="file-field" htmlFor={id}><span className="file-label">{label}{required && <span className="required"> *</span>}</span><span className={file ? 'file-picker has-file' : 'file-picker'}><span>{file?.name ?? 'Choose file'}</span><span className="file-action">Browse</span></span><input id={id} name={id} type="file" accept={id === 'message-file' ? '.txt,text/plain' : '.csv,text/csv'} required={required} onChange={onChange} /><span className="field-help">{hint}</span></label>
}

function ValidationResult({ result, onCommit, busy, job }: { result: ImportValidation; onCommit: () => Promise<void>; busy: string; job?: JobState }) {
  const issues = result.row_issues ?? result.rowIssues ?? result.sources?.flatMap((source) => source.issues ?? []) ?? []
  const accepted = result.accepted_counts ?? result.acceptedCounts
  const rejected = result.rejected_counts ?? result.rejectedCounts
  const batchId = result.batch_id ?? result.batchId
  return <section className="panel validation-panel" aria-live="polite"><div className="panel-heading"><div><p className="eyebrow">Validation result</p><h2>{batchId ? `Batch ${batchId}` : 'Preview complete'}</h2></div><span className={`status-pill ${issues.length ? 'status-review' : 'status-success'}`}>{issues.length ? `${issues.length} issue${issues.length === 1 ? '' : 's'}` : 'Ready to commit'}</span></div><div className="count-grid"><Count label="Accepted rows" value={result.accepted ?? sum(accepted)} tone="good" /><Count label="Rejected rows" value={result.rejected ?? sum(rejected)} tone={sum(rejected) ? 'warn' : 'neutral'} /></div>{(accepted || rejected) && <div className="count-breakdown"><span>Accepted {counts(accepted)}</span><span>Rejected {counts(rejected)}</span></div>}{issues.length > 0 && <IssueTable issues={issues} />}{batchId && <div className="validation-actions"><button className="button button-primary" onClick={() => void onCommit()} disabled={Boolean(busy)}>{busy === 'commit' ? 'Committing…' : 'Commit accepted rows'}</button>{issues.length > 0 && <span className="muted">Rejected rows stay out of the commit; accepted rows can still be committed.</span>}</div>}{job && <div className="job-status"><span className="status-pill">Job {String(job.state ?? job.status ?? 'returned')}</span><span className="muted">The matching worker response is shown as returned; no timing is inferred.</span></div>}</section>
}

function sum(counts?: Record<string, number>) { return counts ? Object.values(counts).reduce((total, count) => total + count, 0) : undefined }
function counts(counts?: Record<string, number>) { return counts ? Object.entries(counts).map(([key, value]) => `${key}: ${value}`).join(' · ') : '—' }
function Count({ label, value, tone }: { label: string; value?: number; tone: string }) { return <div className={`count-card ${tone}`}><span>{label}</span><strong>{value ?? '—'}</strong></div> }
function IssueTable({ issues }: { issues: RowIssue[] }) { return <div className="issue-table table-wrap"><table><caption>Validation issues</caption><thead><tr><th>Row</th><th>Field</th><th>Issue</th></tr></thead><tbody>{issues.map((issue, index) => <tr key={`${issue.row ?? issue.record ?? index}-${issue.field ?? ''}-${index}`}><td>{issue.row ?? issue.record ?? '—'}</td><td>{issue.field ?? '—'}</td><td>{issue.message}{issue.code && <span className="muted"> ({issue.code})</span>}</td></tr>)}</tbody></table></div> }
function ImportRow({ item }: { item: ImportSummary }) { const record = item as Record<string, unknown>; return <tr><td className="mono">{text(record, 'batch_id', 'id') ?? '—'}</td><td><span className="status-pill">{text(record, 'status') ?? '—'}</span></td><td>{dateTime(record.created_at ?? record.createdAt)}</td><td>{sum(item.accepted_counts) ?? '—'}</td></tr> }

function QueueView({ proposals, onOpen, onRefresh, onError }: { proposals: ProposalSummary[]; onOpen: (id: string) => void; onRefresh: () => Promise<void>; onError: (message: string) => void }) {
  return <><PageHeading eyebrow="Work queue" title="Review queue" description="Every proposal is a reviewable suggestion. Select one to inspect evidence and balances." /><div className="toolbar"><span className="muted">{proposals.length} proposal{proposals.length === 1 ? '' : 's'}</span><button className="button button-quiet" onClick={() => void onRefresh().catch((cause) => onError(errorText(cause)))}>Refresh queue</button></div>{proposals.length === 0 ? <EmptyState title="Queue is clear" body="Committed payments will appear here after the matching job runs." /> : <section className="queue-grid" aria-label="Proposals">{proposals.map((proposal, index) => { const id = proposalId(proposal); return <button className="proposal-card" key={id || index} onClick={() => id && onOpen(id)} disabled={!id}><div className="proposal-top"><span className={`status-pill status-${String(proposal.status ?? 'review').toLowerCase()}`}>{proposal.status ?? '—'}</span><span className="mono">#{id.slice(0, 8) || '—'}</span></div><strong>{text(proposal as Record<string, unknown>, 'payer_name', 'payerName') ?? 'Payment'}</strong><span className="proposal-amount">{money(centsOf(proposal as Record<string, unknown>, 'amount_cents', 'amountCents', 'amount'))}</span><span className="proposal-meta">{proposal.currency ?? 'MXN'} · Revision {proposal.revision ?? '—'}</span><span className="view-link">Open allocation <span aria-hidden="true">→</span></span></button>})}</section>}</>
}

function DetailView({ id, onBack, onError, onRefresh }: { id: string; onBack: () => void; onError: (message: string) => void; onRefresh: () => Promise<void> }) {
  const [detail, setDetail] = useState<ProposalDetail>()
  const [loading, setLoading] = useState(true)
  const [cashDraft, setCashDraft] = useState<CashLine[]>([])
  const [creditDraft, setCreditDraft] = useState<CreditLine[]>([])
  const [reviewer, setReviewer] = useState('')
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState('')
  const [appliedId, setAppliedId] = useState('')
  const [interpretation, setInterpretation] = useState<Interpretation>()
  const [interpretationMessage, setInterpretationMessage] = useState('')

  const load = useCallback(async (preserveInterpretation = false) => {
    if (!id) { setLoading(false); return }
    setLoading(true)
    try {
      const result = await getProposal(id)
      setDetail(result)
      const returnedApplicationId = result.application_id ?? result.applicationId
      if (returnedApplicationId) setAppliedId(returnedApplicationId)
      setCashDraft(result.cash ?? result.cash_lines ?? result.cashLines ?? [])
      setCreditDraft(result.credits ?? result.credit_lines ?? result.creditLines ?? [])
      if (!preserveInterpretation && result.interpretation) setInterpretation(result.interpretation)
    } catch (cause) { onError(errorText(cause)) } finally { setLoading(false) }
  }, [id, onError])

  useEffect(() => { void load() }, [load])

  if (loading) return <div className="loading-card" role="status">Loading allocation detail…</div>
  if (!detail) return <EmptyState title="Allocation unavailable" body="The server did not return this proposal." action={<button className="button button-secondary" onClick={onBack}>Back to queue</button>} />

  const payment = detail.payment ?? detail
  const cashLines = detail.cash ?? detail.cash_lines ?? detail.cashLines ?? []
  const creditLines = detail.credits ?? detail.credit_lines ?? detail.creditLines ?? []
  const balances = balanceRows(detail.balances)
  const applicationId = appliedId || detail.application_id || detail.applicationId || text(detail.application, 'id', 'application_id')
  const status = String(detail.status ?? 'NEEDS_REVIEW').toUpperCase()
  const apply = async () => {
    if (!reviewer.trim()) { onError('Reviewer name is required to apply an allocation.'); return }
    setBusy('apply'); onError('')
    try {
      const applied = await applyProposal(id, { expected_revision: detail.revision, version_token: detail.version_token ?? detail.versionToken, reviewer: reviewer.trim(), idempotency_key: crypto.randomUUID() })
      setAppliedId(applied.application_id ?? applied.applicationId ?? '')
      await load(); await onRefresh()
    } catch (cause) { onError(errorText(cause)) } finally { setBusy('') }
  }
  const correct = async (event: React.FormEvent) => {
    event.preventDefault()
    if (!reviewer.trim()) { onError('Reviewer name is required to save a correction.'); return }
    setBusy('correct'); onError('')
    try {
      await correctProposal(id, { expected_revision: detail.revision, cash: cashDraft.map(toCashPayload), credits: creditDraft.map(toCreditPayload), reviewer: reviewer.trim() })
      await load(); await onRefresh()
    } catch (cause) { onError(errorText(cause)) } finally { setBusy('') }
  }
  const reverse = async () => {
    if (!applicationId) return
    if (!reviewer.trim() || !reason.trim()) { onError('Reviewer name and reversal reason are required.'); return }
    setBusy('reverse'); onError('')
    try { await reverseApplication(applicationId, { reviewer: reviewer.trim(), reason: reason.trim(), idempotency_key: crypto.randomUUID() }); await load(); await onRefresh() } catch (cause) { onError(errorText(cause)) } finally { setBusy('') }
  }

  const interpret = async (requestedMode: InterpretationMode) => {
    setBusy(`interpret-${requestedMode}`)
    setInterpretationMessage('')
    onError('')
    try {
      const result = await interpretProposal(id, requestedMode)
      setInterpretation(result.interpretation)
      try {
        await load(true)
        await onRefresh()
      } catch (refreshCause) {
        onError(errorText(refreshCause))
      }
    } catch (cause) {
      const message = cause instanceof ApiError
        ? cause.message
        : cause instanceof Error ? cause.message : 'Interpretation request failed.'
      setInterpretation({
        status: 'unavailable',
        source: 'none',
        mode: requestedMode,
        failure_code: cause instanceof ApiError ? cause.code ?? 'request_failed' : 'request_failed',
      })
      setInterpretationMessage(message)
      try {
        await load(true)
        await onRefresh()
      } catch (refreshCause) {
        onError(errorText(refreshCause))
      }
    } finally {
      setBusy('')
    }
  }

  return <>
    <button className="back-link" onClick={onBack}>← Back to review queue</button>
    <PageHeading eyebrow="Allocation detail" title="Allocation detail" description={`${text(payment, 'payer_name', 'payerName') ?? 'Payment'} · proposal ${id} · revision ${detail.revision ?? '—'} · ${status}`} />
    <section className="detail-grid">
      <div className="detail-main">
        <section className="panel payment-card"><div className="panel-heading"><div><p className="eyebrow">Incoming payment</p><h2>{money(centsOf(payment, 'amount_cents', 'amountCents', 'amount'))}</h2></div><span className={`status-pill status-${status.toLowerCase()}`}>{status}</span></div><dl className="data-list"><Data label="Currency" value={text(payment, 'currency') ?? 'MXN'} /><Data label="Booked" value={dateTime(payment.booking_date ?? payment.bookingDate)} /><Data label="Source account" value={text(payment, 'source_account_id', 'sourceAccountId') ?? '—'} mono /><Data label="Transaction" value={text(payment, 'transaction_id', 'transactionId') ?? '—'} mono /></dl></section>
        <AllocationLines title="Cash applications" lines={cashLines} kind="cash" />
        <AllocationLines title="Credit applications" lines={creditLines} kind="credit" />
        <section className="panel balances-card"><div className="panel-heading"><div><h2>Balances and cash</h2><p className="muted">Authoritative amounts returned by the server.</p></div></div><div className="balance-grid"><Balance label="Unapplied cash" value={centsOf(detail as Record<string, unknown>, 'unapplied_cash', 'unapplied_amount_cents', 'unappliedAmountCents', 'unapplied_amount')} /><Balance label="Payment" value={centsOf(payment, 'amount_cents', 'amountCents', 'amount')} /></div>{balances.length > 0 && <div className="table-wrap"><table><caption>Remaining balances</caption><thead><tr><th>Invoice</th><th>Opening</th><th>Cash used</th><th>Credit used</th><th>Remaining</th></tr></thead><tbody>{balances.map((balance, index) => <tr key={index}><td className="mono">{text(balance, 'invoice_id', 'invoiceId', 'credit_note_id', 'creditNoteId', 'customer_name', 'customerName') ?? '—'}</td><td>{money(centsOf(balance, 'opening_amount_cents', 'openingAmountCents', 'opening_amount', 'available_amount_cents'))}</td><td>{money(centsOf(balance, 'cash_applied', 'cash_applied_cents', 'cashApplied'))}</td><td>{money(centsOf(balance, 'credit_applied', 'credit_applied_cents', 'creditApplied'))}</td><td>{money(centsOf(balance, 'remaining_amount_cents', 'remainingAmountCents', 'remaining_amount', 'available_amount_cents'))}</td></tr>)}</tbody></table></div>}</section>
        <EvidenceSection evidence={detail.evidence ?? []} />
        <AlternativesSection alternatives={detail.alternatives ?? []} />
        <details className="panel trace-panel"><summary>Show execution trace</summary><pre>{JSON.stringify(detail.trace ?? { status: 'not returned' }, null, 2)}</pre></details>
      </div>
      <aside className="detail-side">{(status === 'NEEDS_REVIEW' || interpretation) && <InterpretationAction enabled={status === 'NEEDS_REVIEW'} interpretation={interpretation} message={interpretationMessage} busy={busy} onInterpret={interpret} />}<section className="panel action-panel"><div className="panel-heading"><div><p className="eyebrow">Review action</p><h2>Confirm or correct</h2></div></div><form onSubmit={correct}><label>Reviewer name<input value={reviewer} onChange={(e) => setReviewer(e.target.value)} required placeholder="Your name" /></label><div className="correction-section"><div className="subheading"><h3>Cash lines</h3><button className="button button-quiet" type="button" onClick={() => setCashDraft([...cashDraft, { invoice_id: '', amount_cents: '' }])}>Add line</button></div>{cashDraft.map((line, index) => <LineEditor key={`cash-${index}`} line={line} kind="cash" onChange={(next) => setCashDraft(cashDraft.map((item, itemIndex) => itemIndex === index ? next : item))} onRemove={() => setCashDraft(cashDraft.filter((_, itemIndex) => itemIndex !== index))} />)}</div><div className="correction-section"><div className="subheading"><h3>Credit lines</h3><button className="button button-quiet" type="button" onClick={() => setCreditDraft([...creditDraft, { credit_note_id: '', invoice_id: '', amount_cents: '' }])}>Add line</button></div>{creditDraft.map((line, index) => <LineEditor key={`credit-${index}`} line={line} kind="credit" onChange={(next) => setCreditDraft(creditDraft.map((item, itemIndex) => itemIndex === index ? next : item))} onRemove={() => setCreditDraft(creditDraft.filter((_, itemIndex) => itemIndex !== index))} />)}</div><button className="button button-secondary full-width" type="submit" disabled={Boolean(busy)}>{busy === 'correct' ? 'Saving correction…' : 'Save correction'}</button></form><div className="action-divider" /><button className="button button-primary full-width" onClick={() => void apply()} disabled={Boolean(busy) || status === 'APPLIED' || status === 'REVERSED'}>{busy === 'apply' ? 'Applying…' : status === 'APPLIED' ? 'Applied' : 'Apply allocation'}</button>{status === 'APPLIED' && <><label className="reversal-reason">Reversal reason<input value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Why is this being reversed?" /></label><button className="button button-danger full-width" onClick={() => void reverse()} disabled={Boolean(busy) || !applicationId}>{busy === 'reverse' ? 'Reversing…' : 'Reverse application'}</button></>}</section><ExportCard /></aside>
    </section>
  </>
}

export function InterpretationAction({
  enabled,
  interpretation,
  message,
  busy,
  onInterpret,
}: {
  enabled: boolean
  interpretation?: Interpretation
  message: string
  busy: string
  onInterpret: (mode: InterpretationMode) => Promise<void>
}) {
  return <section className="panel interpretation-panel" aria-labelledby="interpretation-heading">
    <div className="panel-heading">
      <div><p className="eyebrow">Phase 4 interpretation</p><h2 id="interpretation-heading">Review with DeepSeek</h2></div>
    </div>
    <p className="interpretation-help" id="interpretation-help">This is a bounded interpretation of the existing evidence and candidates. DeepSeek never applies money; review and application remain separate.</p>
    <div className="interpretation-actions" role="group" aria-label="Interpretation mode">
      <button className="button button-secondary" type="button" onClick={() => void onInterpret('direct')} disabled={!enabled || Boolean(busy)} aria-describedby="interpretation-help">
        {busy === 'interpret-direct' ? 'Interpreting direct…' : 'Direct'}
      </button>
      <button className="button button-secondary" type="button" onClick={() => void onInterpret('hybrid')} disabled={!enabled || Boolean(busy)} aria-describedby="interpretation-help">
        {busy === 'interpret-hybrid' ? 'Interpreting hybrid…' : 'Hybrid'}
      </button>
    </div>
    {!enabled && interpretation && <p className="interpretation-detail interpretation-complete">This result is attached to the refreshed proposal. Financial application still requires a reviewer.</p>}
    {interpretation && <div className={`interpretation-result interpretation-${interpretation.status}`} role="status" aria-live="polite">
      <div className="interpretation-result-top"><span className={`status-pill status-${interpretation.status}`}>{interpretation.status.replace('_', ' ')}</span><span className="interpretation-source">{interpretationSource(interpretation.source)}</span></div>
      <strong>{interpretationSummary(interpretation)}</strong>
      {interpretation.candidate_id && <span className="interpretation-detail">Candidate <span className="mono">{interpretation.candidate_id}</span></span>}
      <span className="interpretation-detail">Reason: {message || readableCode(interpretation.reason_code ?? interpretation.failure_code) || 'No reason supplied.'}</span>
      {interpretation.failure_code && !message && <span className="interpretation-detail">Failure code: <span className="mono">{interpretation.failure_code}</span></span>}
    </div>}
  </section>
}

function toCents(value: unknown): number | string {
  if (typeof value === 'number') return value
  const raw = String(value ?? '').trim()
  if (/^\d+$/.test(raw)) return Number(raw)
  if (/^\d+\.\d{1,2}$/.test(raw)) { const [whole, fraction = ''] = raw.split('.'); return Number(whole) * 100 + Number(fraction.padEnd(2, '0')) }
  return raw
}
function toCashPayload(line: CashLine) { return { invoice_id: text(line as Record<string, unknown>, 'invoice_id', 'invoiceId') ?? '', amount: toCents(line.amount_cents ?? line.amountCents ?? line.amount) } }
function toCreditPayload(line: CreditLine) { return { credit_note_id: text(line as Record<string, unknown>, 'credit_note_id', 'creditNoteId') ?? '', invoice_id: text(line as Record<string, unknown>, 'invoice_id', 'invoiceId') ?? '', amount: toCents(line.amount_cents ?? line.amountCents ?? line.amount) } }

function LineEditor({ line, kind, onChange, onRemove }: { line: CashLine | CreditLine; kind: 'cash' | 'credit'; onChange: (line: any) => void; onRemove: () => void }) {
  const record = line as Record<string, unknown>
  const idKey = kind === 'cash' ? 'invoice_id' : 'credit_note_id'
  const idLabel = kind === 'cash' ? 'Invoice ID' : 'Credit note ID'
  return <div className="line-editor"><label>{idLabel}<input value={text(record, idKey, kind === 'credit' ? 'creditNoteId' : 'invoiceId') ?? ''} onChange={(e) => onChange({ ...line, [idKey]: e.target.value })} /></label>{kind === 'credit' && <label>Invoice ID<input value={text(record, 'invoice_id', 'invoiceId') ?? ''} onChange={(e) => onChange({ ...line, invoice_id: e.target.value })} /></label>}<label>Amount (MXN)<input inputMode="decimal" value={amountInput(line)} onChange={(e) => onChange({ ...line, amount_cents: e.target.value })} /></label><button type="button" className="remove-button" onClick={onRemove} aria-label={`Remove ${kind} line`}>Remove</button></div>
}
function amountInput(line: CashLine | CreditLine) { const cents = line.amount_cents ?? line.amountCents ?? line.amount; const n = numberValue(cents); return n === undefined ? String(cents ?? '') : (n / 100).toFixed(2) }

function AllocationLines({ title, lines, kind }: { title: string; lines: (CashLine | CreditLine)[]; kind: string }) { return <section className="panel lines-card"><div className="panel-heading"><div><h2>{title}</h2><p className="muted">{kind === 'cash' ? 'Cash is separate from credit.' : 'Credit remains explicitly linked to an invoice.'}</p></div><span className="line-total">{lines.length} line{lines.length === 1 ? '' : 's'}</span></div>{lines.length === 0 ? <p className="empty-inline">No {kind} lines returned.</p> : <div className="line-list">{lines.map((line, index) => <div className="allocation-line" key={index}><div><strong>{text(line as Record<string, unknown>, 'invoice_id', 'invoiceId', 'credit_note_id', 'creditNoteId') ?? 'Unidentified'}</strong><span>{text(line as Record<string, unknown>, 'customer_name', 'customerName') ?? (kind === 'credit' ? 'Credit note' : 'Invoice')}</span></div><strong>{money(centsOf(line as Record<string, unknown>, 'amount_cents', 'amountCents', 'amount'))}</strong></div>)}</div>}</section> }
function EvidenceSection({ evidence }: { evidence: Evidence[] }) { return <section className="panel evidence-card"><div className="panel-heading"><div><h2>Evidence</h2><p className="muted">Citations point to immutable source records.</p></div><span className="line-total">{evidence.length}</span></div>{evidence.length === 0 ? <p className="empty-inline">No evidence spans returned.</p> : <ul className="evidence-list">{evidence.map((item, index) => { const source = text(item as Record<string, unknown>, 'source_id', 'sourceId'); return <li key={index}><div><span className="evidence-kind">{item.kind ?? 'source'}</span><q>{item.excerpt ?? item.text ?? item.quote ?? 'Span returned without excerpt.'}</q><span className="evidence-position">{item.record ? `Record ${item.record}` : `${item.start ?? item.start_offset ?? '—'}–${item.end ?? item.end_offset ?? '—'}`}</span></div>{source ? <a href={sourceUrl(source)} target="_blank" rel="noreferrer">Open source <span aria-hidden="true">↗</span></a> : <span className="muted">Source unavailable</span>}</li> })}</ul>}</section> }
function AlternativesSection({ alternatives }: { alternatives: (Record<string, unknown> | unknown[])[] }) { return <section className="panel alternatives-card"><div className="panel-heading"><div><h2>Alternatives</h2><p className="muted">Plausible alternatives remain visible for review.</p></div></div>{alternatives.length === 0 ? <p className="empty-inline">No alternatives returned.</p> : <ul className="alternative-list">{alternatives.map((alternative, index) => { const label = Array.isArray(alternative) ? alternative.join(' → ') : text(alternative, 'label', 'description', 'status'); return <li key={index}><strong>{label ?? 'Alternative allocation'}</strong><span>{Array.isArray(alternative) ? 'Returned as an equally feasible combination.' : text(alternative, 'reason', 'message') ?? 'No explanation returned.'}</span></li> })}</ul>}</section> }
function ExportCard() { return <section className="panel export-card"><p className="eyebrow">History</p><h2>Export applications</h2><p className="muted">Download active and historical applications as RFC 4180 CSV.</p><a className="button button-secondary full-width" href={exportUrl()} download>Download CSV</a></section> }
function PageHeading({ eyebrow, title, description }: { eyebrow: string; title: string; description: string }) { return <div className="page-heading"><p className="eyebrow">{eyebrow}</p><h1>{title}</h1><p>{description}</p></div> }
function Data({ label, value, mono }: { label: string; value: string; mono?: boolean }) { return <div><dt>{label}</dt><dd className={mono ? 'mono' : ''}>{value}</dd></div> }
function Balance({ label, value }: { label: string; value?: number }) { return <div className="balance"><span>{label}</span><strong>{money(value)}</strong></div> }
function EmptyState({ title, body, action }: { title: string; body: string; action?: React.ReactNode }) { return <div className="empty-state"><span className="empty-symbol" aria-hidden="true">○</span><h2>{title}</h2><p>{body}</p>{action}</div> }

export default App
