import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
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
import { centsToMxn, mxnToCents } from './money'

type Screen = 'imports' | 'queue' | 'detail'

export type CashDraft = { invoice_id: string; amount_mxn: string }
export type CreditDraft = { credit_note_id: string; invoice_id: string; amount_mxn: string }

function amountCentsFromLine(line: CashLine | CreditLine): unknown {
  return line.amount_cents ?? line.amountCents ?? line.amount
}

export function cashLineToDraft(line: CashLine): CashDraft {
  const amount = amountCentsFromLine(line)
  return {
    invoice_id: text(line as Record<string, unknown>, 'invoice_id', 'invoiceId') ?? '',
    amount_mxn: amount === undefined ? '' : centsToMxn(amount),
  }
}

export function creditLineToDraft(line: CreditLine): CreditDraft {
  const amount = amountCentsFromLine(line)
  return {
    credit_note_id: text(line as Record<string, unknown>, 'credit_note_id', 'creditNoteId') ?? '',
    invoice_id: text(line as Record<string, unknown>, 'invoice_id', 'invoiceId') ?? '',
    amount_mxn: amount === undefined ? '' : centsToMxn(amount),
  }
}

export function reviewDraftError(cash: CashDraft[], credits: CreditDraft[]): string | undefined {
  if (cash.length > 3) return 'A correction can contain at most three cash lines.'
  if (credits.length > 1) return 'A correction can contain at most one credit line.'
  for (const [index, line] of cash.entries()) {
    if (!line.invoice_id.trim()) return `Enter an invoice ID for cash line ${index + 1}.`
    try {
      if (mxnToCents(line.amount_mxn) <= 0) return `Cash line ${index + 1} must be greater than MXN 0.00.`
    } catch (error) {
      return `Cash line ${index + 1}: ${error instanceof Error ? error.message : 'enter a valid MXN amount.'}`
    }
  }
  for (const [index, line] of credits.entries()) {
    if (!line.credit_note_id.trim()) return `Enter a credit note ID for credit line ${index + 1}.`
    if (!line.invoice_id.trim()) return `Enter an invoice ID for credit line ${index + 1}.`
    try {
      if (mxnToCents(line.amount_mxn) <= 0) return `Credit line ${index + 1} must be greater than MXN 0.00.`
    } catch (error) {
      return `Credit line ${index + 1}: ${error instanceof Error ? error.message : 'enter a valid MXN amount.'}`
    }
  }
  return undefined
}

export function reviewDraftsEqual(
  leftCash: CashDraft[],
  leftCredits: CreditDraft[],
  rightCash: CashDraft[],
  rightCredits: CreditDraft[],
) {
  return JSON.stringify({ cash: leftCash, credits: leftCredits }) === JSON.stringify({ cash: rightCash, credits: rightCredits })
}

export function projectedBalanceRows(
  balances: Record<string, unknown>[],
  cash: CashDraft[],
  credits: CreditDraft[],
) {
  const deductions = new Map<string, number>()
  for (const line of cash) {
    const amount = mxnToCents(line.amount_mxn)
    deductions.set(line.invoice_id, (deductions.get(line.invoice_id) ?? 0) + amount)
  }
  for (const line of credits) {
    const amount = mxnToCents(line.amount_mxn)
    deductions.set(line.invoice_id, (deductions.get(line.invoice_id) ?? 0) + amount)
  }
  return balances.map((balance) => {
    const invoiceId = text(balance, 'invoice_id', 'invoiceId')
    const remaining = centsOf(balance, 'remaining_amount_cents', 'remainingAmountCents', 'remaining_amount', 'available_amount_cents')
    if (!invoiceId || remaining === undefined || !deductions.has(invoiceId)) return balance
    return { ...balance, projected_remaining_amount: remaining - deductions.get(invoiceId)! }
  })
}

export function applyAttemptFingerprint(proposalId: string, revision: number | undefined, versionToken: string | undefined, reviewer: string) {
  return JSON.stringify({ proposalId, revision: revision ?? null, versionToken: versionToken ?? null, reviewer: reviewer.trim() })
}

export function isUncertainApplyError(error: unknown) {
  if (!(error instanceof ApiError)) return true
  return error.status === 408 || error.status === 425 || error.status === 429 || error.status >= 500
}

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

export async function runJobsUntilSettled(
  run: () => Promise<JobState>,
  onJob: (job: JobState) => void,
  onRefresh: () => Promise<void>,
  delayMs = 500,
  maxAttempts = 60,
) {
  for (let attempt = 0; attempt < maxAttempts; attempt += 1) {
    const result = await run()
    onJob(result)
    const state = String(result.state ?? result.status ?? '').toUpperCase()
    if (!['PENDING', 'RUNNING'].includes(state)) {
      await onRefresh()
      return
    }
    if (attempt === maxAttempts - 1) {
      throw new Error('Job processing timed out while the worker was still running.')
    }
    await new Promise((resolve) => setTimeout(resolve, delayMs))
  }
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
          <div><span className="brand">Reconcile</span><span className="brand-subtitle">Payment-to-invoice review</span></div>
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
            <p className="eyebrow">What it does</p>
            <p>Reconcile proposes which invoices an incoming payment belongs to and shows the evidence behind the match.</p>
            <p className="muted">A reviewer must approve every allocation before balances change.</p>
          </div>
        </aside>

        <main id="main-content" className="main-content">
          <div className="content-wrap">
            {error && <ErrorBanner message={error} onDismiss={() => setError('')} />}
            {screen === 'imports' && <ImportsView mode={mode} imports={imports} onError={setError} onRefresh={refresh} />}
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
  mode,
  imports,
  onError,
  onRefresh,
}: {
  mode: Mode
  imports: ImportSummary[]
  onError: (message: string) => void
  onRefresh: () => Promise<void>
}) {
  const [bank, setBank] = useState<File>()
  const [invoice, setInvoice] = useState<File>()
  const [credit, setCredit] = useState<File>()
  const [message, setMessage] = useState<File>()
  const isPreview = mode === 'preview'
  const [messageTime, setMessageTime] = useState(isPreview ? '2026-01-15T12:00:00+00:00' : '')
  const [paymentAccount, setPaymentAccount] = useState(isPreview ? 'acct-1' : '')
  const [paymentTransaction, setPaymentTransaction] = useState(isPreview ? 'pay-54k' : '')
  const [validation, setValidation] = useState<ImportValidation>()
  const [busy, setBusy] = useState('')
  const [job, setJob] = useState<JobState>()
  const inputGeneration = useRef(0)
  const validationRequest = useRef(0)

  const invalidateInputs = () => {
    inputGeneration.current += 1
    setValidation(undefined)
    setJob(undefined)
    setBusy('')
    onError('')
  }

  const select = (setter: (file: File | undefined) => void) => (event: React.ChangeEvent<HTMLInputElement>) => {
    invalidateInputs()
    setter(event.target.files?.[0])
  }

  const updateContext = (setter: (value: string) => void, value: string) => {
    invalidateInputs()
    setter(value)
  }

  const validate = async (event: React.FormEvent) => {
    event.preventDefault()
    if (!bank || !invoice) {
      onError('Select both the bank CSV and invoice CSV before validating.')
      return
    }
    if (isPreview && (!credit || !message)) {
      onError('Public demo requires all four unmodified files from the downloaded packet: bank.csv, invoices.csv, credits.csv, and message.txt.')
      return
    }
    setBusy('validate')
    onError('')
    const generation = inputGeneration.current
    const requestId = validationRequest.current + 1
    validationRequest.current = requestId
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
      if (inputGeneration.current === generation && validationRequest.current === requestId) setValidation(result)
    } catch (cause) {
      if (inputGeneration.current === generation && validationRequest.current === requestId) onError(errorText(cause))
    } finally {
      if (validationRequest.current === requestId) setBusy('')
    }
  }

  const processJobs = async (generation: number) => {
    if (inputGeneration.current !== generation) return
    setBusy('jobs')
    onError('')
    const stale = () => inputGeneration.current !== generation
    try {
      await runJobsUntilSettled(
        async () => {
          if (stale()) throw new Error('stale import operation')
          return runJobOnce()
        },
        (nextJob) => { if (!stale()) setJob(nextJob) },
        async () => { if (!stale()) await onRefresh() },
      )
    } catch (cause) {
      if (!stale()) onError(errorText(cause))
    } finally {
      if (!stale()) setBusy('')
    }
  }

  const commit = async () => {
    const batchId = validation && (validation.batch_id ?? validation.batchId)
    if (!batchId) return
    const generation = inputGeneration.current
    setBusy('commit')
    onError('')
    try {
      const result = await commitImport(batchId)
      if (inputGeneration.current === generation) setValidation({ ...result, status: 'COMMITTED' })
      await processJobs(generation)
    } catch (cause) {
      if (inputGeneration.current === generation) {
        onError(errorText(cause))
        setBusy('')
      }
    }
  }

  return (
    <>
      <PageHeading eyebrow="Payment allocation workspace" title="Match incoming payments to the right invoices" description="Reconcile compares a bank payment with open invoices, credits, and the customer's payment message. It proposes an evidence-backed allocation for a reviewer to correct or approve." />
      <ol className="workflow-steps" aria-label="Reconciliation workflow">
        <li><span>1</span><strong>Upload evidence</strong><small>Payment, invoices, credit, and message</small></li>
        <li><span>2</span><strong>Review the match</strong><small>Inspect amounts and source evidence</small></li>
        <li><span>3</span><strong>Approve allocation</strong><small>Nothing is applied automatically</small></li>
        <li><span>4</span><strong>Export or reverse</strong><small>Keep an auditable record</small></li>
      </ol>
      <section className="notice-card" aria-label="Import guidance">
        <div className="notice-icon" aria-hidden="true">i</div>
        {isPreview
          ? <div><strong>Public demo: use the included sample</strong><p>Download and unzip the packet, then upload all four unmodified files. The sample context is filled in below. Private business files are not accepted here.</p></div>
          : <div><strong>Evidence first</strong><p>Files are validated and retained as source evidence. Validation, committing rows, and applying an allocation are separate actions.</p></div>}
        <a href={sampleUrl()} className="button button-secondary" download>{isPreview ? 'Download demo packet' : 'Download sample packet'}</a>
      </section>

      <section className="panel import-panel">
        <div className="panel-heading"><div><h2>Upload payment evidence</h2><p className="muted">{isPreview ? 'Start with all four files from the demo packet.' : 'Choose a bank payment file and open-invoice file; credit and message evidence are optional.'} Validation checks them without changing balances.</p></div><span className="step-label">STEP 1</span></div>
        <form onSubmit={validate} className="import-form">
          <FileField id="bank-file" label="Bank CSV" required file={bank} onChange={select(setBank)} hint="source_account_id · transaction_id · amount" />
          <FileField id="invoice-file" label="Invoice CSV" required file={invoice} onChange={select(setInvoice)} hint="customer_id · invoice_id · outstanding_amount" />
          <FileField id="credit-file" label="Credit CSV" file={credit} onChange={select(setCredit)} hint="Optional · explicit invoice links supported" />
          <FileField id="message-file" label="Payment message TXT" file={message} onChange={select(setMessage)} hint="Optional · UTF-8 text with supplied context" />
          <fieldset className="context-fields">
            <legend>Message context <span className="muted">(required when a message is included)</span></legend>
            <label>Message time<input type="text" value={messageTime} onChange={(e) => updateContext(setMessageTime, e.target.value)} placeholder="2026-09-14T10:00:00-06:00" aria-describedby="context-help" /></label>
            <label>Payment source account ID<input value={paymentAccount} onChange={(e) => updateContext(setPaymentAccount, e.target.value)} /></label>
            <label>Payment transaction ID<input value={paymentTransaction} onChange={(e) => updateContext(setPaymentTransaction, e.target.value)} /></label>
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
  const committed = result.committed === true || String(result.status ?? '').toUpperCase() === 'COMMITTED'
  return <section className="panel validation-panel" aria-live="polite"><div className="panel-heading"><div><p className="eyebrow">Validation result</p><h2>{batchId ? `Batch ${batchId}` : 'Preview complete'}</h2></div><span className={`status-pill ${committed ? 'status-success' : issues.length ? 'status-review' : 'status-success'}`}>{committed ? 'Committed' : issues.length ? `${issues.length} issue${issues.length === 1 ? '' : 's'}` : 'Ready to commit'}</span></div><div className="count-grid"><Count label="Accepted rows" value={result.accepted ?? sum(accepted)} tone="good" /><Count label="Rejected rows" value={result.rejected ?? sum(rejected)} tone={sum(rejected) ? 'warn' : 'neutral'} /></div>{(accepted || rejected) && <div className="count-breakdown"><span>Accepted {counts(accepted)}</span><span>Rejected {counts(rejected)}</span></div>}{issues.length > 0 && <IssueTable issues={issues} />}{batchId && <div className="validation-actions">{committed ? <span className="muted">Accepted rows are committed. Matching jobs are being processed below.</span> : <><button className="button button-primary" onClick={() => void onCommit()} disabled={Boolean(busy)}>{busy === 'commit' ? 'Committing…' : 'Commit accepted rows'}</button>{issues.length > 0 && <span className="muted">Rejected rows stay out of the commit; accepted rows can still be committed.</span>}</>}</div>}{job && <div className="job-status"><span className="status-pill">Job {String(job.state ?? job.status ?? 'returned')}</span><span className="muted">The matching worker response is shown as returned; no timing is inferred.</span></div>}</section>
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
  const [cashDraft, setCashDraft] = useState<CashDraft[]>([])
  const [creditDraft, setCreditDraft] = useState<CreditDraft[]>([])
  const [persistedCashDraft, setPersistedCashDraft] = useState<CashDraft[]>([])
  const [persistedCreditDraft, setPersistedCreditDraft] = useState<CreditDraft[]>([])
  const [reviewer, setReviewer] = useState('')
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState('')
  const [appliedId, setAppliedId] = useState('')
  const [interpretation, setInterpretation] = useState<Interpretation>()
  const [interpretationMessage, setInterpretationMessage] = useState('')
  const [confirmationOpen, setConfirmationOpen] = useState(false)
  const applyAttemptRef = useRef<{ fingerprint: string; key: string } | undefined>(undefined)
  const applyInFlightRef = useRef(false)

  const load = useCallback(async (preserveInterpretation = false) => {
    if (!id) { setLoading(false); return }
    setLoading(true)
    try {
      const result = await getProposal(id)
      setDetail(result)
      const returnedApplicationId = result.application_id ?? result.applicationId
      setAppliedId(returnedApplicationId ?? '')
      const nextCashDraft = (result.cash ?? result.cash_lines ?? result.cashLines ?? []).map(cashLineToDraft)
      const nextCreditDraft = (result.credits ?? result.credit_lines ?? result.creditLines ?? []).map(creditLineToDraft)
      setCashDraft(nextCashDraft)
      setCreditDraft(nextCreditDraft)
      setPersistedCashDraft(nextCashDraft)
      setPersistedCreditDraft(nextCreditDraft)
      applyAttemptRef.current = undefined
      if (!preserveInterpretation) setInterpretation(result.interpretation)
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
  const draftError = reviewDraftError(cashDraft, creditDraft)
  const hasUnsavedChanges = !reviewDraftsEqual(cashDraft, creditDraft, persistedCashDraft, persistedCreditDraft)
  const capabilities = detail.capabilities
  const versionToken = detail.version_token ?? detail.versionToken
  const immutable = ['APPLIED', 'REVERSED'].includes(status)
  const canCorrect = !immutable && (capabilities?.correct ?? true)
  const canApply = !immutable && (capabilities?.apply ?? status === 'PROPOSED') && typeof versionToken === 'string' && versionToken.length === 64 && !hasUnsavedChanges && !draftError
  const canReverse = status === 'APPLIED' && (capabilities?.reverse ?? true)
  const apply = async () => {
    if (!canApply) {
      onError(draftError ? `Cannot apply: ${draftError}` : hasUnsavedChanges ? 'Save or discard unsaved correction changes before applying.' : typeof versionToken !== 'string' || versionToken.length !== 64 ? 'This proposal is missing a valid version token.' : 'This proposal is not available for application.')
      return
    }
    if (!reviewer.trim()) { onError('Reviewer name is required to apply an allocation.'); return }
    setConfirmationOpen(true)
  }
  const confirmApply = async () => {
    if (applyInFlightRef.current) return
    if (!canApply || !reviewer.trim()) {
      setConfirmationOpen(false)
      if (!reviewer.trim()) onError('Reviewer name is required to apply an allocation.')
      return
    }
    applyInFlightRef.current = true
    setBusy('apply'); onError('')
    const fingerprint = applyAttemptFingerprint(id, detail.revision, versionToken, reviewer)
    const previousAttempt = applyAttemptRef.current
    const idempotencyKey = previousAttempt?.fingerprint === fingerprint ? previousAttempt.key : crypto.randomUUID()
    const nextAttempt = { fingerprint, key: idempotencyKey }
    applyAttemptRef.current = nextAttempt
    try {
      const applied = await applyProposal(id, { expected_revision: detail.revision, version_token: versionToken, reviewer: reviewer.trim(), idempotency_key: idempotencyKey })
      setAppliedId(applied.application_id ?? applied.applicationId ?? '')
      applyAttemptRef.current = undefined
      setConfirmationOpen(false)
      await load(); await onRefresh()
    } catch (cause) {
      onError(errorText(cause))
      if (!isUncertainApplyError(cause)) {
        applyAttemptRef.current = undefined
      }
    } finally {
      applyInFlightRef.current = false
      setBusy('')
    }
  }
  const correct = async (event: React.FormEvent) => {
    event.preventDefault()
    if (!canCorrect) return
    if (!reviewer.trim()) { onError('Reviewer name is required to save a correction.'); return }
    if (draftError) { onError(draftError); return }
    setBusy('correct'); onError('')
    try {
      await correctProposal(id, { expected_revision: detail.revision, cash: cashDraft.map(cashDraftToPayload), credits: creditDraft.map(creditDraftToPayload), reviewer: reviewer.trim() })
      await load(); await onRefresh()
    } catch (cause) { onError(errorText(cause)) } finally { setBusy('') }
  }
  const reverse = async () => {
    if (!applicationId || !canReverse) return
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
      <aside className="detail-side">
        {(status === 'NEEDS_REVIEW' || interpretation) && <InterpretationAction enabled={status === 'NEEDS_REVIEW' && !hasUnsavedChanges} interpretation={interpretation} message={interpretationMessage} busy={busy} onInterpret={interpret} />}
        <section className="panel action-panel">
          <div className="panel-heading"><div><p className="eyebrow">Review action</p><h2>Confirm or correct</h2></div></div>
          <form onSubmit={correct}>
            <label>Reviewer name<input value={reviewer} onChange={(e) => setReviewer(e.target.value)} required placeholder="Your name" disabled={Boolean(busy) || (!canCorrect && !canReverse && !canApply)} /></label>
            <div className="correction-section">
              <div className="subheading"><h3>Cash lines</h3><button className="button button-quiet" type="button" disabled={!canCorrect || Boolean(busy)} onClick={() => setCashDraft([...cashDraft, { invoice_id: '', amount_mxn: '' }])}>Add line</button></div>
              <p id="amount-format-help" className="field-help">Enter MXN as a decimal amount, such as 100 or 100.00. Values are saved as integer centavos.</p>
              {cashDraft.map((line, index) => <LineEditor key={`cash-${index}`} line={line} kind="cash" disabled={!canCorrect || Boolean(busy)} onChange={(next) => setCashDraft(cashDraft.map((item, itemIndex) => itemIndex === index ? next as CashDraft : item))} onRemove={() => setCashDraft(cashDraft.filter((_, itemIndex) => itemIndex !== index))} />)}
            </div>
            <div className="correction-section">
              <div className="subheading"><h3>Credit lines</h3><button className="button button-quiet" type="button" disabled={!canCorrect || Boolean(busy)} onClick={() => setCreditDraft([...creditDraft, { credit_note_id: '', invoice_id: '', amount_mxn: '' }])}>Add line</button></div>
              {creditDraft.map((line, index) => <LineEditor key={`credit-${index}`} line={line} kind="credit" disabled={!canCorrect || Boolean(busy)} onChange={(next) => setCreditDraft(creditDraft.map((item, itemIndex) => itemIndex === index ? next as CreditDraft : item))} onRemove={() => setCreditDraft(creditDraft.filter((_, itemIndex) => itemIndex !== index))} />)}
            </div>
            {hasUnsavedChanges && <div className="draft-status" role="status"><span>Unsaved changes — save or discard before applying.</span>{canCorrect && <button className="button button-quiet" type="button" disabled={Boolean(busy)} onClick={() => { setCashDraft(persistedCashDraft); setCreditDraft(persistedCreditDraft) }}>Discard changes</button>}</div>}
            {draftError && <p className="draft-status draft-error" role="alert">{draftError}</p>}
            <button className="button button-secondary full-width" type="submit" disabled={Boolean(busy) || !canCorrect}>{busy === 'correct' ? 'Saving correction…' : 'Save correction'}</button>
          </form>
          <div className="action-divider" />
          <button className="button button-primary full-width" onClick={() => void apply()} disabled={Boolean(busy) || !canApply}>{busy === 'apply' ? 'Applying…' : status === 'APPLIED' ? 'Applied' : 'Apply allocation'}</button>
          {status === 'APPLIED' && <><label className="reversal-reason">Reversal reason<input value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Why is this being reversed?" disabled={Boolean(busy) || !canReverse} /></label><button className="button button-danger full-width" onClick={() => void reverse()} disabled={Boolean(busy) || !applicationId || !canReverse}>{busy === 'reverse' ? 'Reversing…' : 'Reverse application'}</button></>}
        </section>
        <ExportCard />
      </aside>
    </section>
    {confirmationOpen && <ApplyConfirmation detail={detail} cash={persistedCashDraft} credits={persistedCreditDraft} balances={balances} busy={busy} onCancel={() => setConfirmationOpen(false)} onConfirm={() => void confirmApply()} />}
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

export function toCents(value: string): number {
  return mxnToCents(value)
}
export function cashDraftToPayload(line: CashDraft) {
  return { invoice_id: line.invoice_id.trim(), amount: mxnToCents(line.amount_mxn) }
}
export function creditDraftToPayload(line: CreditDraft) {
  return { credit_note_id: line.credit_note_id.trim(), invoice_id: line.invoice_id.trim(), amount: mxnToCents(line.amount_mxn) }
}

function LineEditor({ line, kind, disabled, onChange, onRemove }: { line: CashDraft | CreditDraft; kind: 'cash' | 'credit'; disabled?: boolean; onChange: (line: CashDraft | CreditDraft) => void; onRemove: () => void }) {
  const idLabel = kind === 'cash' ? 'Invoice ID' : 'Credit note ID'
  return <div className="line-editor"><label>{idLabel}<input value={kind === 'cash' ? (line as CashDraft).invoice_id : (line as CreditDraft).credit_note_id} disabled={disabled} onChange={(e) => onChange(kind === 'cash' ? { ...line, invoice_id: e.target.value } as CashDraft : { ...line, credit_note_id: e.target.value } as CreditDraft)} /></label>{kind === 'credit' && <label>Invoice ID<input value={(line as CreditDraft).invoice_id} disabled={disabled} onChange={(e) => onChange({ ...line, invoice_id: e.target.value })} /></label>}<label>Amount (MXN)<input inputMode="decimal" value={line.amount_mxn} disabled={disabled} placeholder="100.00" aria-describedby="amount-format-help" onChange={(e) => onChange({ ...line, amount_mxn: e.target.value })} /></label><button type="button" className="remove-button" disabled={disabled} onClick={onRemove} aria-label={`Remove ${kind} line`}>Remove</button></div>
}

export function ApplyConfirmation({ detail, cash, credits, balances, busy, onCancel, onConfirm }: { detail: ProposalDetail; cash: CashDraft[]; credits: CreditDraft[]; balances: Record<string, unknown>[]; busy: string; onCancel: () => void; onConfirm: () => void }) {
  const projected = projectedBalanceRows(balances, cash, credits)
  const dialogRef = useRef<HTMLDialogElement>(null)
  useEffect(() => {
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : undefined
    const dialog = dialogRef.current
    if (dialog && !dialog.open) dialog.showModal()
    dialog?.focus()
    return () => {
      if (dialog?.open) dialog.close()
      previous?.focus()
    }
  }, [])
  return <dialog className="confirmation-dialog" role="dialog" aria-modal="true" aria-labelledby="apply-confirmation-heading" aria-describedby="apply-confirmation-description" ref={dialogRef} onCancel={(event) => { event.preventDefault(); if (!busy) onCancel() }}><p className="eyebrow">Final confirmation</p><h2 id="apply-confirmation-heading">Apply persisted allocation?</h2><p id="apply-confirmation-description">Review revision <strong>{detail.revision ?? '—'}</strong> will be recorded with the following values.</p><div className="confirmation-section"><h3>Cash</h3>{cash.length === 0 ? <p className="muted">No cash lines.</p> : <ul>{cash.map((line, index) => <li key={`cash-${index}`}><span>Invoice <span className="mono">{line.invoice_id}</span></span><strong>{line.amount_mxn} MXN</strong></li>)}</ul>}</div><div className="confirmation-section"><h3>Credit</h3>{credits.length === 0 ? <p className="muted">No credit lines.</p> : <ul>{credits.map((line, index) => <li key={`credit-${index}`}><span>Credit note <span className="mono">{line.credit_note_id}</span> → invoice <span className="mono">{line.invoice_id}</span></span><strong>{line.amount_mxn} MXN</strong></li>)}</ul>}</div><div className="confirmation-section"><h3>Projected invoice balances</h3>{projected.length === 0 ? <p className="muted">No projected balances returned.</p> : <ul>{projected.map((balance, index) => <li key={index}><span className="mono">{text(balance, 'invoice_id', 'invoiceId') ?? 'Invoice'}</span><strong>{money(centsOf(balance, 'projected_remaining_amount', 'remaining_amount_cents', 'remainingAmountCents', 'remaining_amount', 'available_amount_cents'))}</strong></li>)}</ul>}</div><p className="confirmation-note">Reconcile records an allocation for audit purposes; it does not move money in a bank account.</p><div className="confirmation-actions"><button className="button button-secondary" type="button" onClick={onCancel} disabled={Boolean(busy)}>Cancel</button><button className="button button-primary" type="button" onClick={onConfirm} disabled={Boolean(busy)}>{busy === 'apply' ? 'Applying…' : 'Confirm and apply'}</button></div></dialog>
}

function AllocationLines({ title, lines, kind }: { title: string; lines: (CashLine | CreditLine)[]; kind: string }) { return <section className="panel lines-card"><div className="panel-heading"><div><h2>{title}</h2><p className="muted">{kind === 'cash' ? 'Cash is separate from credit.' : 'Credit remains explicitly linked to an invoice.'}</p></div><span className="line-total">{lines.length} line{lines.length === 1 ? '' : 's'}</span></div>{lines.length === 0 ? <p className="empty-inline">No {kind} lines returned.</p> : <div className="line-list">{lines.map((line, index) => <div className="allocation-line" key={index}><div><strong>{text(line as Record<string, unknown>, 'invoice_id', 'invoiceId', 'credit_note_id', 'creditNoteId') ?? 'Unidentified'}</strong><span>{text(line as Record<string, unknown>, 'customer_name', 'customerName') ?? (kind === 'credit' ? 'Credit note' : 'Invoice')}</span></div><strong>{money(centsOf(line as Record<string, unknown>, 'amount_cents', 'amountCents', 'amount'))}</strong></div>)}</div>}</section> }
function EvidenceSection({ evidence }: { evidence: Evidence[] }) { return <section className="panel evidence-card"><div className="panel-heading"><div><h2>Evidence</h2><p className="muted">Citations point to immutable source records.</p></div><span className="line-total">{evidence.length}</span></div>{evidence.length === 0 ? <p className="empty-inline">No evidence spans returned.</p> : <ul className="evidence-list">{evidence.map((item, index) => { const source = text(item as Record<string, unknown>, 'source_id', 'sourceId'); return <li key={index}><div><span className="evidence-kind">{item.kind ?? 'source'}</span><q>{item.excerpt ?? item.text ?? item.quote ?? 'Span returned without excerpt.'}</q><span className="evidence-position">{item.record ? `Record ${item.record}` : `${item.start ?? item.start_offset ?? '—'}–${item.end ?? item.end_offset ?? '—'}`}</span></div>{source ? <a href={sourceUrl(source)} target="_blank" rel="noreferrer">Open source <span aria-hidden="true">↗</span></a> : <span className="muted">Source unavailable</span>}</li> })}</ul>}</section> }
function AlternativesSection({ alternatives }: { alternatives: (Record<string, unknown> | unknown[])[] }) { return <section className="panel alternatives-card"><div className="panel-heading"><div><h2>Alternatives</h2><p className="muted">Plausible alternatives remain visible for review.</p></div></div>{alternatives.length === 0 ? <p className="empty-inline">No alternatives returned.</p> : <ul className="alternative-list">{alternatives.map((alternative, index) => { const label = Array.isArray(alternative) ? alternative.join(' → ') : text(alternative, 'label', 'description', 'status'); return <li key={index}><strong>{label ?? 'Alternative allocation'}</strong><span>{Array.isArray(alternative) ? 'Returned as an equally feasible combination.' : text(alternative, 'reason', 'message') ?? 'No explanation returned.'}</span></li> })}</ul>}</section> }
function ExportCard() { return <section className="panel export-card"><p className="eyebrow">History</p><h2>Export applications</h2><p className="muted">Download active and historical applications as RFC 4180 CSV.</p><a className="button button-secondary full-width" href={exportUrl()} download>Download CSV</a></section> }
function PageHeading({ eyebrow, title, description }: { eyebrow: string; title: string; description: string }) { return <div className="page-heading"><p className="eyebrow">{eyebrow}</p><h1>{title}</h1><p>{description}</p></div> }
function Data({ label, value, mono }: { label: string; value: string; mono?: boolean }) { return <div><dt>{label}</dt><dd className={mono ? 'mono' : ''}>{value}</dd></div> }
function Balance({ label, value }: { label: string; value?: number }) { return <div className="balance"><span>{label}</span><strong>{money(value)}</strong></div> }
function EmptyState({ title, body, action }: { title: string; body: string; action?: React.ReactNode }) { return <div className="empty-state"><span className="empty-symbol" aria-hidden="true">○</span><h2>{title}</h2><p>{body}</p>{action}</div> }

export default App
