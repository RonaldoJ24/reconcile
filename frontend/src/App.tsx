import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  ApiError,
  applyCaseVariant,
  applyProposal,
  compareProposal,
  commitImport,
  correctProposal,
  createSession,
  exportUrl,
  getEvaluation,
  getProposal,
  getSource,
  streamInterpretProposal,
  listCases,
  listImports,
  listProposals,
  openCase as openCaseRequest,
  reverseApplication,
  runReliabilityCheck,
  runJobOnce,
  sampleUrl,
  validateImport,
} from './api'
import CaseVariantPanel from './CaseVariantPanel'
import ComparisonPanel from './ComparisonPanel'
import EvaluationView from './EvaluationView'
import ReliabilityPanel from './ReliabilityPanel'
import type {
  CashLine,
  Capabilities,
  CaseOpen,
  CaseRegistry,
  CaseVariant,
  Comparison,
  CreditLine,
  DecisionTrace,
  Evidence,
  InvoiceBalance,
  ImportCommit,
  ImportSummary,
  ImportValidation,
  EvaluationResponse,
  Interpretation,
  InterpretationMode,
  InterpretationProgress,
  JobState,
  Mode,
  ProposalDetail,
  ProposalSummary,
  RowIssue,
  ReliabilityExperiment,
  ReliabilityResult,
  Session,
  SourceRecord,
} from './types'
import { centsToMxn, mxnToCents } from './money'

type Screen = 'cases' | 'imports' | 'queue' | 'detail' | 'evaluation'
export type AppRoute =
  | { screen: 'cases' }
  | { screen: 'imports' }
  | { screen: 'queue' }
  | { screen: 'detail'; id: string }
  | { screen: 'evaluation' }

export function parseAppRoute(hash: string): AppRoute {
  const value = hash.replace(/^#/, '')
  if (value === 'imports') return { screen: 'imports' }
  if (value === 'queue') return { screen: 'queue' }
  if (value === 'evaluation') return { screen: 'evaluation' }
  if (value.startsWith('proposal/')) {
    try {
      const id = decodeURIComponent(value.slice('proposal/'.length))
      if (id) return { screen: 'detail', id }
    } catch {
      return { screen: 'cases' }
    }
  }
  return { screen: 'cases' }
}

function routeHash(route: AppRoute) {
  if (route.screen === 'detail') return `#proposal/${encodeURIComponent(route.id)}`
  return `#${route.screen}`
}

export type CashDraft = { invoice_id: string; amount_mxn: string }
export type CreditDraft = { credit_note_id: string; invoice_id: string; amount_mxn: string }

export function cashLineToDraft(line: CashLine): CashDraft {
  return {
    invoice_id: line.invoice_id,
    amount_mxn: centsToMxn(line.amount),
  }
}

export function creditLineToDraft(line: CreditLine): CreditDraft {
  return {
    credit_note_id: line.credit_note_id,
    invoice_id: line.invoice_id,
    amount_mxn: centsToMxn(line.amount),
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
  balances: Array<InvoiceBalance & { invoice_id?: string }>,
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
    const invoiceId = balance.invoice_id
    if (!invoiceId || !deductions.has(invoiceId)) return balance
    return { ...balance, projected_remaining_amount: balance.remaining_amount - deductions.get(invoiceId)! }
  })
}

export function applyAttemptFingerprint(proposalId: string, revision: number | undefined, versionToken: string | undefined, reviewer: string) {
  return JSON.stringify({ proposalId, revision: revision ?? null, versionToken: versionToken ?? null, reviewer: reviewer.trim() })
}

export function isUncertainApplyError(error: unknown) {
  if (!(error instanceof ApiError)) return true
  return error.status === 408 || error.status === 425 || error.status === 429 || error.status >= 500
}

export function comparisonMatchesDetail(
  detail: Pick<ProposalDetail, 'revision' | 'decision_trace' | 'model_trace' | 'trace'>,
  comparison: Comparison,
) {
  if (comparison.revision !== detail.revision) return false
  const traceFingerprints = [
    detail.decision_trace?.input_fingerprint,
    typeof detail.model_trace?.input_fingerprint === 'string' ? detail.model_trace.input_fingerprint : undefined,
    typeof detail.trace?.input_fingerprint === 'string' ? detail.trace.input_fingerprint : undefined,
  ]
  const expected = traceFingerprints.find((value): value is string => typeof value === 'string')
  if (expected !== undefined) return comparison.input_fingerprint === expected
  return comparison.input_fingerprint === null && comparison.methods.every((method) => method.status === 'unavailable')
}

const money = (cents: number | undefined) => {
  if (cents === undefined || !Number.isFinite(cents)) return '—'
  return new Intl.NumberFormat('en-MX', { style: 'currency', currency: 'MXN' }).format(cents / 100)
}

export const formatDateTime = (value: string | undefined) => {
  if (!value) return '—'
  if (/^\d{4}-\d{2}-\d{2}$/.test(value)) return value
  const parsed = new Date(String(value))
  return Number.isNaN(parsed.getTime()) ? String(value) : parsed.toLocaleString()
}

function errorText(error: unknown) {
  if (error instanceof ApiError) return `${error.status}: ${error.message}`
  return error instanceof Error ? error.message : 'Unexpected request failure'
}

function readableCode(value: string | undefined) {
  return value ? value.replace(/[_-]+/g, ' ') : undefined
}

function interpretationReason(value: string | undefined) {
  switch (value) {
    case 'evidence_supported': return 'The cited evidence supports the selected allocation.'
    case 'ambiguous': return 'The evidence fits more than one candidate, so no allocation was selected.'
    case 'contradictory': return 'The sources conflict, so no allocation was selected.'
    case 'insufficient_evidence': return 'The available evidence is too weak to select an allocation.'
    default: return readableCode(value)
  }
}

function interpretationSource(source: Interpretation['source']) {
  if (source === 'live') return 'Live DeepSeek'
  if (source === 'cache') return 'Validated cache'
  return 'Unavailable'
}

function interpretationSummary(result: Interpretation) {
  if (result.status === 'selected') return 'An existing allocation was selected for reviewer review.'
  if (result.status === 'needs_review') return 'No existing allocation was selected; the proposal remains unresolved.'
  return 'Interpretation is unavailable; the current proposal is unchanged.'
}

function normalizedInterpretation(detail: ProposalDetail): Interpretation | undefined {
  const saved = detail.interpretation
  if (!saved) return undefined
  const savedStatus = saved.status
  if (savedStatus !== 'selected' && savedStatus !== 'needs_review' && savedStatus !== 'unavailable') return undefined
  const source: Interpretation['source'] = saved.source === 'live' || saved.source === 'cache' ? saved.source : 'none'
  const mode: InterpretationMode = saved.mode === 'hybrid' ? 'hybrid' : 'direct'
  return {
    ...saved,
    status: savedStatus,
    source,
    mode,
  }
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
    const state = result.status.toUpperCase()
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
  const [route, setRoute] = useState<AppRoute>(() => parseAppRoute(typeof window === 'undefined' ? '' : window.location.hash))
  const [mode, setMode] = useState<Mode>('rules-v1')
  const [session, setSession] = useState<Session>()
  const [caseRegistry, setCaseRegistry] = useState<CaseRegistry>()
  const [proposals, setProposals] = useState<ProposalSummary[]>([])
  const [imports, setImports] = useState<ImportSummary[]>([])
  const [caseBusy, setCaseBusy] = useState('')
  const [caseProgress, setCaseProgress] = useState('')
  const [caseError, setCaseError] = useState('')
  const [startup, setStartup] = useState(true)
  const [error, setError] = useState('')
  const [evaluation, setEvaluation] = useState<EvaluationResponse>()
  const [evaluationLoading, setEvaluationLoading] = useState(false)
  const [evaluationError, setEvaluationError] = useState('')
  const [evaluationAttempt, setEvaluationAttempt] = useState(0)

  const refresh = useCallback(async () => {
    const [importResult, proposalResult] = await Promise.all([listImports(), listProposals()])
    setImports(importResult)
    setProposals(proposalResult)
  }, [])

  useEffect(() => {
    const onHashChange = () => setRoute(parseAppRoute(window.location.hash))
    window.addEventListener('hashchange', onHashChange)
    return () => window.removeEventListener('hashchange', onHashChange)
  }, [])

  useEffect(() => {
    let cancelled = false
    void (async () => {
      try {
        const session = await createSession()
        if (!cancelled) {
          setSession(session)
          setMode(session.mode)
        }
        const [registry] = await Promise.all([
          listCases(),
          refresh(),
        ])
        if (!cancelled) setCaseRegistry(registry)
      } catch (cause) {
        if (!cancelled) setCaseError(errorText(cause))
      } finally {
        if (!cancelled) setStartup(false)
      }
    })()
    return () => { cancelled = true }
  }, [refresh])

  useEffect(() => {
    if (startup || route.screen !== 'evaluation') return
    let cancelled = false
    setEvaluationLoading(true)
    setEvaluationError('')
    void getEvaluation()
      .then((result) => {
        if (!cancelled) setEvaluation(result)
      })
      .catch((cause) => {
        if (!cancelled) setEvaluationError(errorText(cause))
      })
      .finally(() => {
        if (!cancelled) setEvaluationLoading(false)
      })
    return () => { cancelled = true }
  }, [evaluationAttempt, route.screen, startup])

  const retryEvaluation = () => {
    setEvaluation(undefined)
    setEvaluationError('')
    setEvaluationAttempt((attempt) => attempt + 1)
  }

  const navigate = (next: AppRoute) => {
    if (typeof window === 'undefined') {
      setRoute(next)
      return
    }
    const hash = routeHash(next)
    if (window.location.hash === hash) setRoute(next)
    else window.location.hash = hash.slice(1)
    setError('')
  }

  const openDetail = (id: string) => navigate({ screen: 'detail', id })

  const openCase = async (caseId: string) => {
    if (caseBusy) return
    setCaseBusy(caseId)
    setCaseProgress('Preparing the payment evidence…')
    setCaseError('')
    setError('')
    try {
      const opened: CaseOpen = await openCaseRequest(caseId)
      if (opened.jobs.length > 0) {
        await runJobsUntilSettled(
          runJobOnce,
          (job) => {
            const state = job.status.toUpperCase()
            setCaseProgress(
              state === 'RUNNING'
                ? 'Matching the payment to the available invoices…'
                : state === 'PENDING'
                  ? 'Matching work is queued…'
                  : state === 'SUCCEEDED'
                    ? 'Matching complete; loading the review result…'
                    : state === 'FAILED'
                      ? 'Matching could not complete; checking the saved review state…'
                      : 'Checking the payment result…',
            )
          },
          async () => {},
        )
      }
      setCaseProgress('Loading the review result…')
      const nextProposals = await listProposals()
      setProposals(nextProposals)
      const proposal = nextProposals.find((item) => item.payment_id === opened.payment_id)
      const id = proposal?.proposal_id ?? opened.proposal_id
      navigate(id ? { screen: 'detail', id } : { screen: 'queue' })
    } catch (cause) {
      setCaseError(errorText(cause))
    } finally {
      setCaseBusy('')
      setCaseProgress('')
    }
  }

  if (startup) return <div className="loading-page" role="status">Starting secure workspace…</div>

  const screen = route.screen
  const selectedId = route.screen === 'detail' ? route.id : ''
  const capabilitySummary = session?.capabilities

  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content" onClick={(event) => { event.preventDefault(); document.getElementById('main-content')?.focus() }}>Skip to content</a>
      <header className="topbar">
        <div className="brand-lockup">
          <span className="brand-mark" aria-hidden="true">R</span>
          <div><span className="brand">Reconcile</span><span className="brand-subtitle">Payment-to-invoice review</span></div>
        </div>
        <div className="session-meta" aria-label="Runtime mode">
          <span className="mode-dot" aria-hidden="true" />
          <span>{session?.active_engine ?? 'Engine unavailable'} · {mode}</span>
          <span className="mode-note">runtime</span>
        </div>
      </header>

      <div className="workspace">
        <aside className="sidebar" aria-label="Primary navigation">
          <p className="eyebrow">Workspace</p>
          <nav>
            <button className={screen === 'cases' ? 'nav-item active' : 'nav-item'} onClick={() => navigate({ screen: 'cases' })}>
              <span aria-hidden="true">◈</span> Cases
            </button>
            <button className={screen === 'imports' ? 'nav-item active' : 'nav-item'} onClick={() => navigate({ screen: 'imports' })}>
              <span aria-hidden="true">↥</span> Imports
            </button>
            <button className={screen === 'queue' || screen === 'detail' ? 'nav-item active' : 'nav-item'} onClick={() => navigate({ screen: 'queue' })}>
              <span aria-hidden="true">☷</span> Review queue
              {proposals.length > 0 && <span className="nav-count" aria-label={`${proposals.length} proposals`}>{proposals.length}</span>}
            </button>
            <button className={screen === 'evaluation' ? 'nav-item active' : 'nav-item'} onClick={() => navigate({ screen: 'evaluation' })}>
              <span aria-hidden="true">▥</span> Evaluation
            </button>
          </nav>
          <div className="sidebar-foot">
            <p className="eyebrow">What it does</p>
            <p>Reconcile proposes which invoices an incoming payment belongs to and shows the evidence behind the match.</p>
            <p className="muted">A reviewer must approve every allocation before balances change.</p>
          </div>
        </aside>

        <main id="main-content" className="main-content" tabIndex={-1}>
          <div className="content-wrap">
            {(error || caseError) && <ErrorBanner message={error || caseError} onDismiss={() => { setError(''); setCaseError('') }} />}
            {screen === 'cases' && <CasesView registry={caseRegistry} busy={caseBusy} progress={caseProgress} onOpen={openCase} />}
            {screen === 'imports' && <ImportsView mode={mode} imports={imports} onError={setError} onRefresh={refresh} />}
            {screen === 'queue' && <QueueView proposals={proposals} onOpen={openDetail} onRefresh={refresh} onError={setError} />}
            {screen === 'evaluation' && <><PageHeading eyebrow="Evaluation" title="Evaluation" description="What the preserved report measured." /><EvaluationView evaluation={evaluation} loading={evaluationLoading} error={evaluationError} onRetry={retryEvaluation} /></>}
            {screen === 'detail' && (
              <DetailView
                id={selectedId}
                sessionCapabilities={capabilitySummary}
                onBack={() => navigate({ screen: 'queue' })}
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

function DecisionSummary({ detail, cash, credits, status, canEdit, onEdit }: { detail: ProposalDetail; cash: CashDraft[]; credits: CreditDraft[]; status: string; canEdit: boolean; onEdit: () => void }) {
  const reason = typeof detail.reason === 'string' ? detail.reason : undefined
  return <section className={`panel decision-summary decision-summary-${status.toLowerCase()}`} aria-labelledby="decision-summary-heading"><div className="decision-summary-top"><div><p className="eyebrow">Decision</p><div className="decision-heading-line"><h2 id="decision-summary-heading">{decisionStateLabel(status)}</h2><span className={`status-pill status-${status.toLowerCase()}`}>{status}</span></div><p className="muted">Server state: <span className="mono">{status}</span>. Review the evidence and balances before taking a financial action.</p>{reason && <p className="decision-reason">Reason: {reason}</p>}</div>{canEdit && <button className="button button-secondary" type="button" onClick={onEdit}>Edit allocation</button>}</div><div className="decision-summary-lines"><div><span>Cash</span><strong>{cash.length} line{cash.length === 1 ? '' : 's'}</strong></div><div><span>Credit</span><strong>{credits.length} line{credits.length === 1 ? '' : 's'}</strong></div><div><span>Case</span><strong>{detail.case?.id ?? 'Manual import'}</strong></div></div></section>
}

function decisionStateLabel(status: string) {
  switch (status) {
    case 'NEEDS_REVIEW': return 'Needs review'
    case 'STALE': return 'Stale revision'
    case 'APPLIED': return 'Applied allocation'
    case 'REVERSED': return 'Reversed allocation'
    case 'PROPOSED': return 'Ready for reviewer approval'
    default: return status || 'Unknown decision state'
  }
}

function CanonicalAllocationLines({ title, lines, kind }: { title: string; lines: (CashLine | CreditLine)[]; kind: 'cash' | 'credit' }) {
  return <section className="panel lines-card"><div className="panel-heading"><div><h2>{title}</h2><p className="muted">{kind === 'cash' ? 'Cash is separate from credit.' : 'Credit remains explicitly linked to an invoice.'}</p></div><span className="line-total">{lines.length} line{lines.length === 1 ? '' : 's'}</span></div>{lines.length === 0 ? <p className="empty-inline">No {kind} lines returned.</p> : <div className="line-list">{lines.map((line, index) => { const cash = line as CashLine; const credit = line as CreditLine; return <div className="allocation-line" key={index}><div><strong>{kind === 'cash' ? `Invoice ${cash.invoice_id}` : `Credit note ${credit.credit_note_id}`}</strong><span>{kind === 'cash' ? 'Invoice allocation' : `Linked invoice ${credit.invoice_id}`}</span></div><strong>{money(line.amount)}</strong></div> })}</div>}</section>
}

function CanonicalEvidenceSection({ evidence, onSource }: { evidence: Evidence[]; onSource: (sourceId: string) => void }) {
  const uniqueEvidence = evidence.filter((item, index, all) => all.findIndex((candidate) => candidate.source_id === item.source_id && candidate.start === item.start && candidate.end === item.end && candidate.quote === item.quote) === index)
  return <section className="panel evidence-card"><div className="panel-heading"><div><h2>Evidence</h2><p className="muted">Citations point to immutable source records.</p></div><span className="line-total">{uniqueEvidence.length}</span></div>{uniqueEvidence.length === 0 ? <p className="empty-inline">No evidence spans returned.</p> : <ul className="evidence-list">{uniqueEvidence.map((item, index) => <li key={`${item.source_id}-${item.start}-${item.end}-${index}`}><div><span className="evidence-kind">Evidence</span><q>{item.quote}</q><span className="evidence-position">{item.start}–{item.end}</span><span className="evidence-source mono">Source {item.source_id}</span></div><button className="button button-quiet" type="button" onClick={() => onSource(item.source_id)}>Open source</button></li>)}</ul>}</section>
}

function traceProvenance(source: DecisionTrace['source'] | undefined) {
  if (source === 'live') return 'Live'
  if (source === 'cache') return 'Validated cache'
  if (source === 'recorded') return 'Recorded validated run'
  if (source === 'rules') return 'Deterministic rules'
  if (source === 'local') return 'Local model execution'
  if (source === 'unavailable') return 'Unavailable'
  return 'Not returned'
}

export function DecisionTracePanel({ trace, modelTrace, onSource }: { trace?: DecisionTrace | null; modelTrace?: Record<string, unknown> | null; onSource?: (sourceId: string) => void }) {
  return <section className="panel trace-panel" aria-labelledby="decision-trace-heading"><div className="panel-heading"><div><p className="eyebrow">Decision trace</p><h2 id="decision-trace-heading">How this decision was produced</h2></div>{trace?.source && <span className="status-pill">{traceProvenance(trace.source)}</span>}</div>{trace ? <><p className="trace-meta">Source provenance: <strong>{traceProvenance(trace.source)}</strong>{trace.schema_version ? ` · schema ${trace.schema_version}` : ''}</p>{trace.stages.length === 0 ? <p className="empty-inline">No executed stages were returned.</p> : <ol className="trace-timeline" aria-label="Decision stages">{trace.stages.map((stage) => <li key={stage.id}><div className="trace-stage-top"><strong>{stage.name}</strong><span className={`status-pill status-${stage.status.toLowerCase()}`}>{stage.status}</span></div><p>{stage.summary}</p><span className="trace-duration">{stage.duration_ms === null ? 'Not measured' : `${stage.duration_ms} ms`}</span>{(stage.details !== undefined || (stage.evidence && stage.evidence.length > 0)) && <details className="trace-stage-details"><summary>Stage details</summary>{stage.evidence && stage.evidence.length > 0 && <div className="trace-stage-evidence"><span>Evidence sources</span>{stage.evidence.map((sourceId) => onSource ? <button className="trace-source-button" key={sourceId} type="button" onClick={() => onSource(sourceId)}>Open source <span className="mono">{sourceId}</span></button> : <span className="mono" key={sourceId}>{sourceId}</span>)}</div>}{stage.details !== undefined && <pre>{JSON.stringify(stage.details, null, 2)}</pre>}</details>}</li>)}</ol>}</> : <p className="empty-inline">Decision trace unavailable from the server.</p>}{modelTrace && <details className="trace-raw"><summary>Show original model trace</summary><pre>{JSON.stringify(modelTrace, null, 2)}</pre></details>}</section>
}

export function SourceViewer({ sourceId, source, busy, error, onClose }: { sourceId?: string; source?: SourceRecord; busy: boolean; error: string; onClose: () => void }) {
  const dialogRef = useRef<HTMLDialogElement>(null)
  useEffect(() => {
    const dialog = dialogRef.current
    if (dialog && !dialog.open) dialog.showModal()
    return () => { if (dialog?.open) dialog.close() }
  }, [])
  if (!sourceId) return null
  return <dialog className="source-dialog" ref={dialogRef} aria-labelledby="source-viewer-heading" onCancel={(event) => { event.preventDefault(); onClose() }}><div className="source-dialog-heading"><div><p className="eyebrow">Source record</p><h2 id="source-viewer-heading">{source?.kind ?? 'Source'} content</h2><p className="source-id">Source <span className="mono">{sourceId}</span></p></div><button className="icon-button" type="button" aria-label="Close source" onClick={onClose}>×</button></div>{busy && <p role="status">Loading authenticated source…</p>}{error && <p className="draft-status draft-error" role="alert">{error}</p>}{source && <><div className="source-content-block"><p className="eyebrow">Source content</p><pre className="source-content">{source.raw_text ?? source.text ?? JSON.stringify(source.rows, null, 2)}</pre></div><details className="source-provenance"><summary>Source provenance</summary><dl className="source-meta"><Data label="Source ID" value={source.source_id} mono /><Data label="Version" value={source.version === undefined ? 'Not returned' : String(source.version)} /><Data label="SHA-256" value={source.sha256} mono /><Data label="Bytes" value={String(source.bytes)} /></dl><details className="trace-raw"><summary>Exact source metadata</summary><pre>{JSON.stringify({ version: source.version ?? null, metadata: source.metadata, row_locators: source.row_locators, issues: source.issues }, null, 2)}</pre></details></details></>}<div className="confirmation-actions"><button className="button button-secondary" type="button" onClick={onClose}>Close source</button></div></dialog>
}

export function CasesView({ registry, busy, progress, onOpen }: { registry?: CaseRegistry; busy: string; progress?: string; onOpen: (caseId: string) => Promise<void> }) {
  const cases = registry?.cases ?? []
  const bundled = cases.find((item) => item.id === 'bundle')
  return <>
    <PageHeading eyebrow="Case study workspace" title="Start with a bundled payment case" description="Walk through one payment, several plausible invoices, the evidence behind each match, and the reviewer decision." />
    <section className="case-hero panel" aria-labelledby="case-hero-heading">
      <div>
        <p className="eyebrow">Recommended first step</p>
        <h2 id="case-hero-heading">See one payment become a reviewable decision</h2>
        <p className="muted">Open the bundled payment to compare several plausible invoices, inspect its evidence, and decide what a reviewer should do. Nothing is applied automatically.</p>
      </div>
      <div>{progress && <p className="case-progress" role="status" aria-live="polite">{progress}</p>}<button className="button button-primary" type="button" disabled={!bundled || Boolean(busy)} onClick={() => bundled && void onOpen(bundled.id)}>{busy === 'bundle' ? 'Opening bundled case…' : 'Open bundled payment case'}</button></div>
    </section>
    <section aria-labelledby="case-library-heading">
      <div className="section-heading"><div><p className="eyebrow">Case library</p><h2 id="case-library-heading">Choose a scenario</h2></div><span className="muted">{registry?.version ?? '—'}</span></div>
      {cases.length === 0 ? <div className="empty-state"><h2>Cases are unavailable</h2><p>The server did not return any case choices. Manual imports remain available.</p></div> : <div className="case-grid">{cases.map((item) => <button className="case-card" key={item.id} type="button" disabled={Boolean(busy)} onClick={() => void onOpen(item.id)}><span className="case-card-top"><span className="case-id mono">{item.id}</span><span className="case-amount">{money(item.amount)}</span></span><strong>{item.title}</strong><span>{item.description}</span><span className="case-card-action">{busy === item.id ? 'Opening…' : 'Open case →'}</span></button>)}</div>}
    </section>
  </>
}

type ImportResult = ImportValidation | (ImportCommit & { committed: true })

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
  const [validation, setValidation] = useState<ImportResult>()
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
    const batchId = validation?.batch_id
    if (!batchId) return
    const generation = inputGeneration.current
    setBusy('commit')
    onError('')
    try {
      const result = await commitImport(batchId)
      if (inputGeneration.current === generation) setValidation({ ...result, committed: true })
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
        {imports.length === 0 ? <EmptyState title="No imports yet" body="Validate a bank and invoice CSV to start a review." /> : <div className="table-wrap"><table><caption className="sr-only">Import history</caption><thead><tr><th>Batch</th><th>Status</th><th>Created</th><th>Accepted</th></tr></thead><tbody>{imports.map((item) => <ImportRow key={item.batch_id} item={item} />)}</tbody></table></div>}
      </section>
    </>
  )
}

function FileField({ id, label, required, file, onChange, hint }: { id: string; label: string; required?: boolean; file?: File; onChange: (event: React.ChangeEvent<HTMLInputElement>) => void; hint: string }) {
  return <label className="file-field" htmlFor={id}><span className="file-label">{label}{required && <span className="required"> *</span>}</span><span className={file ? 'file-picker has-file' : 'file-picker'}><span>{file?.name ?? 'Choose file'}</span><span className="file-action">Browse</span></span><input id={id} name={id} type="file" accept={id === 'message-file' ? '.txt,text/plain' : '.csv,text/csv'} required={required} onChange={onChange} /><span className="field-help">{hint}</span></label>
}

function ValidationResult({ result, onCommit, busy, job }: { result: ImportResult; onCommit: () => Promise<void>; busy: string; job?: JobState }) {
  const isValidation = 'sources' in result
  const issues = isValidation ? result.sources.flatMap((source) => source.issues) : []
  const committed = 'committed' in result && result.committed
  const batchId = result.batch_id
  const jobState = job?.status.toUpperCase()
  const matchingText = jobState === 'SUCCEEDED'
    ? 'Matching finished. The result is shown below.'
    : jobState === 'FAILED'
      ? 'Matching did not complete. Review the saved review state below.'
      : jobState === 'RUNNING'
        ? 'Matching is in progress; the saved review state will appear when it finishes.'
        : jobState === 'PENDING'
          ? 'Matching is queued; the saved review state will appear when it finishes.'
          : 'Matching status is not available yet; refresh to check the saved review state.'
  return <section className="panel validation-panel" aria-live="polite"><div className="panel-heading"><div><p className="eyebrow">Validation result</p><h2>{batchId ? `Batch ${batchId}` : 'Preview complete'}</h2></div><span className={`status-pill ${committed ? 'status-success' : issues.length ? 'status-review' : 'status-success'}`}>{committed ? 'Committed' : issues.length ? `${issues.length} issue${issues.length === 1 ? '' : 's'}` : 'Ready to commit'}</span></div>{isValidation && <div className="count-grid"><Count label="Accepted rows" value={result.accepted} tone="good" /><Count label="Rejected rows" value={result.rejected} tone={result.rejected ? 'warn' : 'neutral'} /></div>}{issues.length > 0 && <IssueTable issues={issues} />}{batchId && <div className="validation-actions">{committed ? <span className="muted">{matchingText}</span> : <><button className="button button-primary" onClick={() => void onCommit()} disabled={Boolean(busy)}>{busy === 'commit' ? 'Committing…' : 'Commit accepted rows'}</button>{issues.length > 0 && <span className="muted">Rejected rows stay out of the commit; accepted rows can still be committed.</span>}</>}</div>}{job && <div className="job-status"><span className="status-pill">Job {job.status}</span><span className="muted">The matching worker response is shown as returned; no timing is inferred.</span></div>}</section>
}

function Count({ label, value, tone }: { label: string; value?: number; tone: string }) { return <div className={`count-card ${tone}`}><span>{label}</span><strong>{value ?? '—'}</strong></div> }
function IssueTable({ issues }: { issues: RowIssue[] }) { return <div className="issue-table table-wrap"><table><caption>Validation issues</caption><thead><tr><th>Row</th><th>Field</th><th>Issue</th></tr></thead><tbody>{issues.map((issue, index) => <tr key={`${issue.row ?? issue.record ?? index}-${issue.field ?? ''}-${index}`}><td>{issue.row ?? issue.record ?? '—'}</td><td>{issue.field ?? '—'}</td><td>{issue.message}{issue.code && <span className="muted"> ({issue.code})</span>}</td></tr>)}</tbody></table></div> }
function ImportRow({ item }: { item: ImportSummary }) { return <tr><td className="mono">{item.batch_id}</td><td><span className="status-pill">{item.status}</span></td><td>{formatDateTime(item.created_at)}</td><td>—</td></tr> }

function QueueView({ proposals, onOpen, onRefresh, onError }: { proposals: ProposalSummary[]; onOpen: (id: string) => void; onRefresh: () => Promise<void>; onError: (message: string) => void }) {
  return <><PageHeading eyebrow="Work queue" title="Review queue" description="Every payment proposal is a reviewable suggestion. Select one to inspect its invoices, evidence, and balances." /><div className="toolbar"><span className="muted">{proposals.length} proposal{proposals.length === 1 ? '' : 's'}</span><button className="button button-quiet" onClick={() => void onRefresh().catch((cause) => onError(errorText(cause)))}>Refresh queue</button></div>{proposals.length === 0 ? <EmptyState title="Queue is clear" body="Committed payments will appear here after the matching job runs." /> : <section className="queue-grid" aria-label="Proposals">{proposals.map((proposal, index) => <button className="proposal-card" key={proposal.proposal_id || index} onClick={() => onOpen(proposal.proposal_id)}><div className="proposal-top"><span className={`status-pill status-${proposal.status.toLowerCase()}`}>{proposal.status}</span><span className="mono">#{proposal.proposal_id.slice(0, 8)}</span></div><strong>{proposal.payer_name}</strong><span className="proposal-amount">{money(proposal.amount)}</span><span className="proposal-meta">MXN · Revision {proposal.revision}</span><span className="view-link">Open allocation <span aria-hidden="true">→</span></span></button>)}</section>}</>
}

function DetailView({ id, sessionCapabilities, onBack, onError, onRefresh }: { id: string; sessionCapabilities?: Capabilities; onBack: () => void; onError: (message: string) => void; onRefresh: () => Promise<void> }) {
  const [detail, setDetail] = useState<ProposalDetail>()
  const [loading, setLoading] = useState(true)
  const [editing, setEditing] = useState(false)
  const [cashDraft, setCashDraft] = useState<CashDraft[]>([])
  const [creditDraft, setCreditDraft] = useState<CreditDraft[]>([])
  const [persistedCashDraft, setPersistedCashDraft] = useState<CashDraft[]>([])
  const [persistedCreditDraft, setPersistedCreditDraft] = useState<CreditDraft[]>([])
  const [reviewer, setReviewer] = useState('')
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState('')
  const [appliedId, setAppliedId] = useState('')
  const [interpretation, setInterpretation] = useState<Interpretation>()
  const [interpretationProposal, setInterpretationProposal] = useState<{ status: string; revision: number }>()
  const [interpretationRefreshPending, setInterpretationRefreshPending] = useState(false)
  const [interpretationRefreshFailed, setInterpretationRefreshFailed] = useState(false)
  const [interpretationMessage, setInterpretationMessage] = useState('')
  const [interpretationProgress, setInterpretationProgress] = useState<InterpretationProgress[]>([])
  const [interpretationStartedAt, setInterpretationStartedAt] = useState<number>()
  const [interpretationElapsedMs, setInterpretationElapsedMs] = useState(0)
  const [confirmationOpen, setConfirmationOpen] = useState(false)
  const [sourceRequest, setSourceRequest] = useState<string>()
  const [sourceRecord, setSourceRecord] = useState<SourceRecord>()
  const [sourceBusy, setSourceBusy] = useState(false)
  const [sourceError, setSourceError] = useState('')
  const [comparisonBusy, setComparisonBusy] = useState(false)
  const [comparisonError, setComparisonError] = useState('')
  const [variantSelection, setVariantSelection] = useState<CaseVariant | ''>('')
  const [variantBusy, setVariantBusy] = useState(false)
  const [variantError, setVariantError] = useState('')
  const [reliabilityExperiment, setReliabilityExperiment] = useState<ReliabilityExperiment>('invalid_allocation')
  const [reliabilityResult, setReliabilityResult] = useState<ReliabilityResult>()
  const [reliabilityBusy, setReliabilityBusy] = useState(false)
  const [reliabilityError, setReliabilityError] = useState('')
  const applyAttemptRef = useRef<{ fingerprint: string; key: string } | undefined>(undefined)
  const applyInFlightRef = useRef(false)
  const sourceRequestVersion = useRef(0)
  const comparisonGeneration = useRef(0)
  const reliabilityGeneration = useRef(0)
  const detailGeneration = useRef(0)
  const interpretationGeneration = useRef(0)
  const interpretationAbort = useRef<AbortController | undefined>(undefined)

  const load = useCallback(async (preserveInterpretation = false) => {
    const generation = ++detailGeneration.current
    comparisonGeneration.current += 1
    reliabilityGeneration.current += 1
    setComparisonError('')
    setComparisonBusy(false)
    setVariantError('')
    setVariantBusy(false)
    setReliabilityError('')
    setReliabilityResult(undefined)
    setReliabilityBusy(false)
    if (!id) { setLoading(false); return undefined }
    // Interpretation refreshes replace the saved proposal in place. Keep the
    // current detail visible while that request runs so the completed stream
    // does not flash a blank loading card.
    if (!preserveInterpretation) setLoading(true)
    try {
      const result = await getProposal(id)
      if (detailGeneration.current !== generation) return result
      setDetail(result)
      setAppliedId(result.application_id ?? '')
      const nextCashDraft = result.cash.map(cashLineToDraft)
      const nextCreditDraft = result.credits.map(creditLineToDraft)
      setCashDraft(nextCashDraft)
      setCreditDraft(nextCreditDraft)
      setPersistedCashDraft(nextCashDraft)
      setPersistedCreditDraft(nextCreditDraft)
      setVariantSelection(result.case?.variant ?? '')
      applyAttemptRef.current = undefined
      setEditing(false)
      if (!preserveInterpretation) setInterpretation(normalizedInterpretation(result))
      return result
    } catch (cause) {
      if (detailGeneration.current === generation) onError(errorText(cause))
      return undefined
    } finally {
      if (detailGeneration.current === generation) setLoading(false)
    }
  }, [id, onError])

  useEffect(() => {
    interpretationAbort.current?.abort()
    interpretationGeneration.current += 1
    detailGeneration.current += 1
    setBusy('')
    setInterpretation(undefined)
    setInterpretationProposal(undefined)
    setInterpretationRefreshPending(false)
    setInterpretationRefreshFailed(false)
    setInterpretationMessage('')
    setInterpretationProgress([])
    setInterpretationStartedAt(undefined)
    setInterpretationElapsedMs(0)
    void load()
    return () => {
      detailGeneration.current += 1
      interpretationGeneration.current += 1
      interpretationAbort.current?.abort()
      interpretationAbort.current = undefined
      setBusy('')
      setInterpretation(undefined)
      setInterpretationProposal(undefined)
      setInterpretationRefreshPending(false)
      setInterpretationRefreshFailed(false)
      setInterpretationMessage('')
      setInterpretationProgress([])
      setInterpretationStartedAt(undefined)
    }
  }, [id, load])

  useEffect(() => {
    if (interpretationStartedAt === undefined) return
    const update = () => setInterpretationElapsedMs(Math.max(0, performance.now() - interpretationStartedAt))
    update()
    const timer = window.setInterval(update, 250)
    return () => window.clearInterval(timer)
  }, [interpretationStartedAt])

  if (loading) return <div className="loading-card" role="status">Loading allocation detail…</div>
  if (!detail) return <EmptyState title="Allocation unavailable" body="The server did not return this proposal." action={<button className="button button-secondary" onClick={onBack}>Back to queue</button>} />

  const payment = detail.payment
  const cashLines = detail.cash
  const creditLines = detail.credits
  const balances = Object.entries(detail.balances).map(([invoice_id, balance]) => ({ invoice_id, ...balance }))
  const applicationId = appliedId || detail.application_id || undefined
  const status = detail.status.toUpperCase()
  const draftError = reviewDraftError(cashDraft, creditDraft)
  const hasUnsavedChanges = !reviewDraftsEqual(cashDraft, creditDraft, persistedCashDraft, persistedCreditDraft)
  const capabilities = detail.capabilities
  const versionToken = detail.version_token
  const immutable = ['APPLIED', 'REVERSED'].includes(status)
  const canCorrect = !immutable && (capabilities?.correct ?? sessionCapabilities?.correct ?? true)
  const financialActionBusy = Boolean(busy) || variantBusy || reliabilityBusy
  const canApply = status === 'PROPOSED' && !immutable && (capabilities?.apply ?? sessionCapabilities?.apply ?? status === 'PROPOSED') && typeof versionToken === 'string' && versionToken.length === 64 && !hasUnsavedChanges && !draftError && !variantBusy && !reliabilityBusy
  const canReverse = status === 'APPLIED' && (capabilities?.reverse ?? sessionCapabilities?.reverse ?? true)
  const interpretationEnabled = capabilities?.interpret ?? sessionCapabilities?.interpret ?? false
  const canInterpret = status === 'NEEDS_REVIEW' && !hasUnsavedChanges && interpretationEnabled && !variantBusy && !reliabilityBusy
  const provenance = typeof detail.trace?.mode === 'string' ? detail.trace.mode : detail.decision_trace?.source
  const interpretationDisabledReason = status === 'APPLIED'
    ? 'DeepSeek is unavailable because this allocation is already applied. Review details remain available for reversal.'
    : status === 'REVERSED'
      ? 'DeepSeek is unavailable because this allocation has been reversed. The financial history remains immutable.'
      : status === 'PROPOSED' && provenance === 'rules-v2-conservative'
        ? 'DeepSeek was not needed because the deterministic rules resolved this proposal. A reviewer must still apply it.'
        : status === 'PROPOSED' && provenance === 'human-correction'
          ? 'DeepSeek is unavailable for this revision because a reviewer supplied the allocation.'
          : status === 'PROPOSED' && typeof provenance === 'string' && provenance.startsWith('llm-')
            ? 'DeepSeek is unavailable because this revision already contains an interpretation result.'
            : status !== 'NEEDS_REVIEW'
              ? 'DeepSeek is available only for proposals that still need review.'
              : capabilities?.interpret === false || (capabilities?.interpret === undefined && sessionCapabilities?.interpret === false)
                ? 'Live interpretation is disabled for this session.'
                : hasUnsavedChanges ? 'Save or discard unsaved changes before requesting interpretation.'
                  : variantBusy || reliabilityBusy ? 'Wait for the current case lab request to finish before requesting interpretation.'
                    : undefined
  const comparisonStale = Boolean(detail.comparison && !comparisonMatchesDetail(detail, detail.comparison))
  const comparison = hasUnsavedChanges || comparisonStale ? undefined : detail.comparison
  const comparisonDisabledReason = hasUnsavedChanges
    ? 'Save or discard unsaved correction changes before comparing this revision.'
    : comparisonStale
      ? 'This comparison belongs to an older revision. Compare again to refresh it.'
      : undefined
  const clearComparison = () => {
    comparisonGeneration.current += 1
    reliabilityGeneration.current += 1
    setComparisonBusy(false)
    setComparisonError('')
    setReliabilityBusy(false)
    setReliabilityError('')
    setReliabilityResult(undefined)
    setDetail((current) => current?.comparison ? { ...current, comparison: undefined } : current)
  }
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
      setAppliedId(applied.application_id)
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

  const compare = async () => {
    if (comparisonBusy || Boolean(busy) || variantBusy || reliabilityBusy || loading) return
    if (hasUnsavedChanges) {
      setComparisonError('Save or discard unsaved correction changes before comparing this revision.')
      return
    }
    setComparisonBusy(true)
    setComparisonError('')
    const generation = comparisonGeneration.current
    const requestedDetail = detail
    try {
      const result = await compareProposal(id, detail.revision)
      if (comparisonGeneration.current !== generation) return
      if (!comparisonMatchesDetail(requestedDetail, result)) {
        setComparisonError('The comparison response does not match the current proposal revision or input snapshot.')
        return
      }
      setDetail((current) => current && current.proposal_id === requestedDetail.proposal_id && current.revision === requestedDetail.revision
        ? { ...current, comparison: result }
        : current)
    } catch (cause) {
      if (comparisonGeneration.current === generation) setComparisonError(errorText(cause))
    } finally {
      if (comparisonGeneration.current === generation) setComparisonBusy(false)
    }
  }

  const variantReason = detail.case?.variant === undefined
    ? 'The server did not return the current case variant.'
    : immutable
      ? 'Case variants cannot change an applied or reversed revision.'
      : hasUnsavedChanges
        ? 'Save or discard unsaved correction changes before changing the case variant.'
        : Boolean(busy) || comparisonBusy || reliabilityBusy || variantBusy
          ? 'Wait for the current request to finish before changing the case variant.'
          : undefined
  const runVariant = async () => {
    const caseId = detail.case?.id
    if (!caseId || !variantSelection || variantReason) return
    clearComparison()
    const generation = reliabilityGeneration.current
    setVariantBusy(true)
    setVariantError('')
    try {
      const opened = await applyCaseVariant(caseId, detail.revision, variantSelection)
      if (reliabilityGeneration.current !== generation) return
      setVariantSelection(opened.variant ?? variantSelection)
      if (opened.jobs.length > 0) await runJobsUntilSettled(runJobOnce, () => {}, async () => {})
      if (reliabilityGeneration.current !== generation) return
      await load()
      await onRefresh()
    } catch (cause) {
      if (reliabilityGeneration.current === generation) setVariantError(errorText(cause))
    } finally {
      if (reliabilityGeneration.current === generation) setVariantBusy(false)
    }
  }

  const reliabilityReason = hasUnsavedChanges
    ? 'Save or discard unsaved correction changes before running a synthetic check.'
    : Boolean(busy) || comparisonBusy || variantBusy || reliabilityBusy
      ? 'Wait for the current request to finish before running a synthetic check.'
      : reliabilityExperiment === 'stale_apply' && status !== 'PROPOSED'
        ? 'Stale apply requires a PROPOSED revision so a discordant version token can be checked safely.'
        : reliabilityExperiment === 'duplicate_apply' && status !== 'APPLIED'
          ? 'Duplicate apply is available only after an explicit reviewer application.'
          : ['invalid_allocation', 'invalid_citation'].includes(reliabilityExperiment) && balances.length === 0
            ? 'This synthetic check requires an invoice balance in the current proposal.'
            : undefined
  const runReliability = async () => {
    if (reliabilityReason) return
    const generation = reliabilityGeneration.current
    setReliabilityBusy(true)
    setReliabilityError('')
    try {
      const result = await runReliabilityCheck(id, detail.revision, reliabilityExperiment)
      if (reliabilityGeneration.current === generation) setReliabilityResult(result)
    } catch (cause) {
      if (reliabilityGeneration.current === generation) setReliabilityError(errorText(cause))
    } finally {
      if (reliabilityGeneration.current === generation) setReliabilityBusy(false)
    }
  }
  const changeReliabilityExperiment = (experiment: ReliabilityExperiment) => {
    reliabilityGeneration.current += 1
    setReliabilityExperiment(experiment)
    setReliabilityResult(undefined)
    setReliabilityError('')
  }

  const inspectSource = async (sourceId: string) => {
    const requestVersion = ++sourceRequestVersion.current
    setSourceRequest(sourceId)
    setSourceRecord(undefined)
    setSourceError('')
    setSourceBusy(true)
    try {
      const result = await getSource(sourceId)
      if (sourceRequestVersion.current === requestVersion) setSourceRecord(result)
    } catch (cause) {
      if (sourceRequestVersion.current === requestVersion) setSourceError(errorText(cause))
    } finally {
      if (sourceRequestVersion.current === requestVersion) setSourceBusy(false)
    }
  }

  const closeSource = () => {
    sourceRequestVersion.current += 1
    setSourceRequest(undefined)
    setSourceRecord(undefined)
    setSourceError('')
    setSourceBusy(false)
  }

  const interpret = async (requestedMode: InterpretationMode) => {
    interpretationAbort.current?.abort()
    const generation = ++interpretationGeneration.current
    const controller = new AbortController()
    interpretationAbort.current = controller
    setBusy(`interpret-${requestedMode}`)
    setInterpretation(undefined)
    setInterpretationProposal(undefined)
    setInterpretationRefreshPending(false)
    setInterpretationRefreshFailed(false)
    setInterpretationMessage('')
    setInterpretationProgress([])
    setInterpretationStartedAt(performance.now())
    setInterpretationElapsedMs(0)
    onError('')
    try {
      const result = await streamInterpretProposal(
        id,
        requestedMode,
        (progress) => {
          if (interpretationGeneration.current !== generation) return
          setInterpretationProgress((current) => [...current.filter((item) => item.stage !== progress.stage), progress])
        },
        controller.signal,
      )
      if (interpretationGeneration.current !== generation) return
      setInterpretation(result.interpretation)
      setInterpretationProposal({ status: result.status, revision: result.revision })
      setInterpretationRefreshPending(true)
      try {
        const refreshed = await load(true)
        if (interpretationGeneration.current !== generation) return
        setInterpretationRefreshPending(false)
        setInterpretationRefreshFailed(!refreshed)
        await onRefresh()
        if (interpretationGeneration.current !== generation) return
      } catch (refreshCause) {
        if (interpretationGeneration.current !== generation) return
        onError(errorText(refreshCause))
      }
    } catch (cause) {
      if (interpretationGeneration.current !== generation) return
      const message = cause instanceof ApiError
        ? cause.message
        : cause instanceof DOMException && cause.name === 'AbortError'
          ? 'Interpretation stopped before the result was received. Refresh the proposal to confirm its saved state.'
        : cause instanceof Error ? cause.message : 'Interpretation request failed.'
      setInterpretation({
        status: 'unavailable',
        source: 'none',
        mode: requestedMode,
        failure_code: cause instanceof ApiError ? cause.code ?? 'request_failed' : 'request_failed',
      })
      setInterpretationProposal(undefined)
      setInterpretationMessage(message)
      setInterpretationRefreshPending(true)
      try {
        const refreshed = await load(true)
        if (interpretationGeneration.current !== generation) return
        setInterpretationRefreshPending(false)
        setInterpretationRefreshFailed(!refreshed)
        await onRefresh()
        if (interpretationGeneration.current !== generation) return
      } catch (refreshCause) {
        if (interpretationGeneration.current !== generation) return
        onError(errorText(refreshCause))
      }
    } finally {
      if (interpretationGeneration.current === generation) {
        setBusy('')
        setInterpretationStartedAt(undefined)
        if (interpretationAbort.current === controller) interpretationAbort.current = undefined
      }
    }
  }

  return <>
    <button className="back-link" onClick={onBack}>← Back to review queue</button>
    <PageHeading eyebrow="Allocation detail" title="Allocation detail" description={<span className="detail-page-meta">{payment.payer_name} · proposal <span className="mono">{id}</span> · revision {detail.revision} · {status}</span>} />
    <section className="detail-grid">
      <div className="detail-main">
        <DecisionSummary detail={detail} cash={persistedCashDraft} credits={persistedCreditDraft} status={status} onEdit={() => setEditing(true)} canEdit={canCorrect && !financialActionBusy} />
        <div className="allocation-overview">
          <section className="panel payment-card"><div className="panel-heading"><div><p className="eyebrow">Incoming payment</p><h2>{money(payment.amount)}</h2></div><span className={`status-pill status-${status.toLowerCase()}`}>{status}</span></div><dl className="data-list"><Data label="Currency" value="MXN" /><Data label="Booked" value={formatDateTime(payment.booking_date)} /><Data label="Source account" value={payment.source_account_id} mono /><Data label="Transaction" value={payment.transaction_id} mono /></dl></section>
          <div className="proposed-lines">
            <CanonicalAllocationLines title="Cash applications" lines={cashLines} kind="cash" />
            <CanonicalAllocationLines title="Credit applications" lines={creditLines} kind="credit" />
          </div>
          <CanonicalEvidenceSection evidence={detail.evidence} onSource={inspectSource} />
        </div>
        <section className="panel balances-card current-balances-card"><div className="panel-heading"><div><p className="eyebrow">Current state</p><h2>Balances and cash</h2><p className="muted">Authoritative amounts returned by the server.</p></div></div><div className="balance-grid"><Balance label="Unapplied cash" value={detail.unapplied_cash} /><Balance label="Payment" value={payment.amount} /></div>{balances.length > 0 && <div className="table-wrap"><table><caption>Remaining balances</caption><thead><tr><th>Invoice</th><th>Opening</th><th>Cash used</th><th>Credit used</th><th>Remaining</th></tr></thead><tbody>{balances.map((balance) => <tr key={balance.invoice_id}><td className="mono">{balance.invoice_id}</td><td>{money(balance.opening_amount)}</td><td>{money(balance.cash_applied)}</td><td>{money(balance.credit_applied)}</td><td>{money(balance.remaining_amount)}</td></tr>)}</tbody></table></div>}</section>
        <AlternativesSection alternatives={detail.alternatives} />
        <aside className="detail-side">
          {(status === 'NEEDS_REVIEW' || interpretation || interpretationDisabledReason) && <InterpretationAction
            enabled={canInterpret}
            disabledReason={interpretationDisabledReason}
            interpretation={interpretation}
            message={interpretationMessage}
            busy={busy}
            progress={interpretationProgress}
            elapsedMs={interpretationElapsedMs}
            proposalStatus={status}
            proposalRevision={interpretationProposal?.revision ?? detail.revision}
            hasSavedInterpretation={Boolean(detail.interpretation)}
            actualProposalStatus={interpretationProposal?.status ?? status}
            interpretationRefreshPending={interpretationRefreshPending}
            interpretationRefreshFailed={interpretationRefreshFailed}
            cash={detail.cash}
            credits={detail.credits}
            evidence={detail.evidence}
            proposalReason={detail.reason}
            onSource={inspectSource}
            onInterpret={interpret}
          />}
          <section className="panel action-panel">
            <div className="panel-heading"><div><p className="eyebrow">Review action</p><h2>Confirm or correct</h2></div></div>
            <label>Reviewer name<input value={reviewer} onChange={(e) => setReviewer(e.target.value)} required placeholder="Your name" disabled={financialActionBusy || (!canCorrect && !canReverse && !canApply)} /></label>
            {editing && canCorrect ? <form onSubmit={correct}>
              <div className="correction-section">
                <div className="subheading"><h3>Cash lines</h3><button className="button button-quiet" type="button" disabled={!canCorrect || financialActionBusy} onClick={() => { clearComparison(); setCashDraft([...cashDraft, { invoice_id: '', amount_mxn: '' }]) }}>Add line</button></div>
                <p id="amount-format-help" className="field-help">Enter MXN as a decimal amount, such as 100 or 100.00. Values are saved as integer centavos.</p>
                {cashDraft.map((line, index) => <LineEditor key={`cash-${index}`} line={line} kind="cash" disabled={!canCorrect || financialActionBusy} onChange={(next) => { clearComparison(); setCashDraft(cashDraft.map((item, itemIndex) => itemIndex === index ? next as CashDraft : item)) }} onRemove={() => { clearComparison(); setCashDraft(cashDraft.filter((_, itemIndex) => itemIndex !== index)) }} />)}
              </div>
              <div className="correction-section">
                <div className="subheading"><h3>Credit lines</h3><button className="button button-quiet" type="button" disabled={!canCorrect || financialActionBusy} onClick={() => { clearComparison(); setCreditDraft([...creditDraft, { credit_note_id: '', invoice_id: '', amount_mxn: '' }]) }}>Add line</button></div>
                {creditDraft.map((line, index) => <LineEditor key={`credit-${index}`} line={line} kind="credit" disabled={!canCorrect || financialActionBusy} onChange={(next) => { clearComparison(); setCreditDraft(creditDraft.map((item, itemIndex) => itemIndex === index ? next as CreditDraft : item)) }} onRemove={() => { clearComparison(); setCreditDraft(creditDraft.filter((_, itemIndex) => itemIndex !== index)) }} />)}
              </div>
              {hasUnsavedChanges && <div className="draft-status" role="status"><span>Unsaved changes — save or discard before applying.</span>{canCorrect && <button className="button button-quiet" type="button" disabled={financialActionBusy} onClick={() => { clearComparison(); setCashDraft(persistedCashDraft); setCreditDraft(persistedCreditDraft) }}>Discard changes</button>}</div>}
              {draftError && <p className="draft-status draft-error" role="alert">{draftError}</p>}
              <div className="edit-actions"><button className="button button-quiet" type="button" disabled={financialActionBusy} onClick={() => { clearComparison(); setCashDraft(persistedCashDraft); setCreditDraft(persistedCreditDraft); setEditing(false) }}>Cancel edit</button><button className="button button-secondary" type="submit" disabled={financialActionBusy || !canCorrect}>{busy === 'correct' ? 'Saving correction…' : 'Save correction'}</button></div>
            </form> : canCorrect ? <div className="read-only-action"><p className="muted">The persisted allocation is shown above. Enter edit mode to change invoice, credit, or amount lines.</p><button className="button button-secondary full-width" type="button" onClick={() => setEditing(true)} disabled={financialActionBusy}>Edit allocation</button></div> : <p className="muted">This revision is locked. Reviewer details remain available for reversal when the server permits it.</p>}
            <div className="action-divider" />
            <button className="button button-primary full-width" onClick={() => void apply()} disabled={financialActionBusy || !canApply}>{busy === 'apply' ? 'Applying…' : status === 'APPLIED' ? 'Applied' : 'Apply allocation'}</button>
            {status === 'APPLIED' && <><label className="reversal-reason">Reversal reason<input value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Why is this being reversed?" disabled={financialActionBusy || !canReverse} /></label><button className="button button-danger full-width" onClick={() => void reverse()} disabled={financialActionBusy || !applicationId || !canReverse}>{busy === 'reverse' ? 'Reversing…' : 'Reverse application'}</button></>}
          </section>
          <ExportCard />
        </aside>
        <DecisionTracePanel trace={detail.decision_trace} modelTrace={detail.model_trace} onSource={inspectSource} />
        <ComparisonPanel
          comparison={comparison}
          loading={comparisonBusy}
          error={comparisonError}
          onCompare={() => void compare()}
          canCompare={!hasUnsavedChanges && !Boolean(busy) && !variantBusy && !reliabilityBusy && !loading}
          disabledReason={comparisonDisabledReason}
        />
        {detail.case && <CaseVariantPanel
          caseId={detail.case.id}
          scenarioVersion={detail.case.version}
          currentVariant={detail.case.variant}
          selectedVariant={variantSelection}
          loading={variantBusy}
          disabled={Boolean(variantReason)}
          disabledReason={variantReason}
          error={variantError}
          onVariantChange={setVariantSelection}
          onApply={() => void runVariant()}
        />}
        <ReliabilityPanel
          status={status}
          revision={detail.revision}
          selectedExperiment={reliabilityExperiment}
          result={reliabilityResult}
          loading={reliabilityBusy}
          disabled={Boolean(reliabilityReason)}
          selectionDisabled={Boolean(hasUnsavedChanges) || Boolean(busy) || comparisonBusy || variantBusy || reliabilityBusy}
          disabledReason={reliabilityReason}
          error={reliabilityError}
          onExperimentChange={changeReliabilityExperiment}
          onRun={() => void runReliability()}
        />
      </div>
    </section>
    {confirmationOpen && <ApplyConfirmation detail={detail} cash={persistedCashDraft} credits={persistedCreditDraft} balances={balances} busy={busy} onCancel={() => setConfirmationOpen(false)} onConfirm={() => void confirmApply()} />}
    {sourceRequest && <SourceViewer sourceId={sourceRequest} source={sourceRecord} busy={sourceBusy} error={sourceError} onClose={closeSource} />}
  </>
}

export function InterpretationAction({
  enabled,
  disabledReason,
  interpretation,
  message,
  busy,
  progress = [],
  elapsedMs = 0,
  proposalStatus,
  proposalRevision,
  hasSavedInterpretation = false,
  actualProposalStatus,
  interpretationRefreshPending = false,
  interpretationRefreshFailed = false,
  cash = [],
  credits = [],
  evidence = [],
  proposalReason,
  onSource,
  onInterpret,
}: {
  enabled: boolean
  disabledReason?: string
  interpretation?: Interpretation
  message: string
  busy: string
  progress?: InterpretationProgress[]
  elapsedMs?: number
  proposalStatus?: string
  proposalRevision?: number
  hasSavedInterpretation?: boolean
  actualProposalStatus?: string
  interpretationRefreshPending?: boolean
  interpretationRefreshFailed?: boolean
  cash?: CashLine[]
  credits?: CreditLine[]
  evidence?: Evidence[]
  proposalReason?: string | null
  onSource?: (sourceId: string) => void
  onInterpret: (mode: InterpretationMode) => Promise<void>
}) {
  const activeMode = busy.startsWith('interpret-') ? busy.slice('interpret-'.length) : ''
  const elapsed = `${(elapsedMs / 1000).toFixed(1)}s`
  const observedProgress = progress.filter((item) => item.stage !== 'workflow')
  const currentProgress = [...observedProgress].reverse().find((item) => item.status === 'running') ?? observedProgress[observedProgress.length - 1]
  const savedStatus = actualProposalStatus ?? proposalStatus
  const allocation = interpretationRefreshFailed ? [] : [
    ...cash.map((line) => `Invoice ${line.invoice_id} (${money(line.amount)})`),
    ...credits.map((line) => `Credit note ${line.credit_note_id} → invoice ${line.invoice_id} (${money(line.amount)})`),
  ]
  const reason = interpretationReason(interpretation?.reason_code)
    ?? (interpretation?.status === 'unavailable' ? message : undefined)
    ?? readableCode(interpretation?.failure_code)
  const savedReason = proposalReason ? interpretationReason(proposalReason.replace(/^bounded interpretation:\s*/i, '')) : undefined
  const citations = interpretation?.citations ?? []
  const nextAction = interpretationRefreshFailed
    ? 'Refresh the proposal to confirm the saved allocation before applying.'
    : interpretation?.status === 'selected'
    ? 'Review the selected allocation and evidence, then apply it if correct or edit it before applying.'
    : interpretation?.status === 'needs_review'
      ? 'Resolve the allocation manually or provide more evidence before applying.'
      : interpretation?.status === 'unavailable'
        ? 'Review the saved allocation manually; retry interpretation only if it is still available.'
        : savedStatus === 'PROPOSED'
          ? 'Review the saved allocation and evidence, then apply it if correct or edit it before applying.'
          : savedStatus === 'NEEDS_REVIEW'
            ? 'Resolve the allocation manually or provide more evidence before applying.'
            : undefined
  return <section className="panel interpretation-panel" aria-labelledby="interpretation-heading">
    <div className="panel-heading">
      <div><p className="eyebrow">Optional interpretation</p><h2 id="interpretation-heading">Review with DeepSeek</h2></div>
    </div>
    <p className="interpretation-help" id="interpretation-help">Direct reviews the saved payment evidence and candidates. Hybrid also supplies the existing rank order as context. DeepSeek never applies money; review and application remain separate.</p>
    <div className="interpretation-actions" role="group" aria-label="Interpretation mode">
      <button className="button button-secondary" type="button" onClick={() => void onInterpret('direct')} disabled={!enabled || Boolean(busy)} aria-describedby="interpretation-help">
        {busy === 'interpret-direct' ? 'Interpreting direct…' : 'Direct'}
      </button>
      <button className="button button-secondary" type="button" onClick={() => void onInterpret('hybrid')} disabled={!enabled || Boolean(busy)} aria-describedby="interpretation-help">
        {busy === 'interpret-hybrid' ? 'Interpreting hybrid…' : 'Hybrid'}
      </button>
    </div>
    {activeMode && <div className="interpretation-progress" role="status" aria-live="polite">
      <strong>{activeMode === 'hybrid' ? 'Hybrid interpretation in progress' : 'Direct interpretation in progress'}</strong>
      {interpretationRefreshPending
        ? <><span className="interpretation-current-stage">Updating saved proposal</span><span>Refreshing the saved proposal result before showing the outcome.</span></>
        : currentProgress ? <><span className="interpretation-current-stage">{progressLabel(currentProgress.stage)}</span><span>{currentProgress.summary || progressSummary(currentProgress)}</span></> : <span>Starting the interpretation workflow.</span>}
      <span>Elapsed {elapsed}</span>
      {observedProgress.length > 0 && <details><summary>Observed workflow steps</summary><ol>{observedProgress.map((item) => <li key={item.stage} className={`progress-${item.status}`}><span>{progressLabel(item.stage)}</span><span>{item.summary || progressSummary(item)}</span></li>)}</ol></details>}
    </div>}
    {!enabled && disabledReason && <p className="interpretation-detail interpretation-disabled" role="status">{disabledReason}</p>}
    {!enabled && interpretation && !interpretationRefreshPending && !interpretationRefreshFailed && <p className="interpretation-detail interpretation-complete">This result is attached to the refreshed proposal. Financial application still requires a reviewer.</p>}
    {interpretationRefreshFailed && <p className="interpretation-detail interpretation-refresh-warning" role="status">The interpretation response arrived, but the saved proposal could not be refreshed. Refresh the proposal to confirm its persisted status and allocation before applying.</p>}
    {interpretation && !interpretationRefreshPending && <div className={`interpretation-result interpretation-${interpretation.status}`} role="status" aria-live="polite">
      <div className="interpretation-result-top"><span className={`status-pill status-${interpretation.status}`}>{interpretation.status.replace('_', ' ')}</span><span className="interpretation-source">{interpretationSource(interpretation.source)}</span></div>
      <strong>{interpretationSummary(interpretation)}</strong>
      {savedStatus && proposalRevision !== undefined && <span className="interpretation-detail"><strong>Proposal update:</strong> {savedStatus} at revision {proposalRevision}.</span>}
      {interpretation.status === 'selected' && <span className="interpretation-detail"><strong>Chosen allocation:</strong> {interpretationRefreshFailed ? 'Allocation details could not be refreshed; refresh the proposal before applying.' : allocation.length ? allocation.join('; ') : 'The saved proposal contains no cash or credit lines.'}</span>}
      {interpretation.status === 'needs_review' && <span className="interpretation-detail"><strong>Allocation:</strong> unresolved; no existing candidate was selected.</span>}
      {interpretation.candidate_id && <span className="interpretation-detail">Candidate <span className="mono">{interpretation.candidate_id}</span></span>}
      {reason && <span className="interpretation-detail">Reason: {reason}</span>}
      {citations.length > 0 && <div className="interpretation-citations"><strong>Relevant evidence</strong><ul>{citations.map((citation, index) => citation.source_id ? <li key={`${citation.source_id}-${citation.start ?? index}-${citation.end ?? index}`}><span><span className="mono">Source {citation.source_id}</span>{citation.quote ? `: “${citation.quote}”` : ''}</span>{onSource && <button className="button button-quiet" type="button" onClick={() => onSource(citation.source_id!)}>Open source</button>}</li> : null)}</ul></div>}
      {nextAction && <span className="interpretation-next"><strong>Next reviewer action:</strong> {nextAction}</span>}
      {interpretation.failure_code && !message && <span className="interpretation-detail">Failure code: <span className="mono">{interpretation.failure_code}</span></span>}
    </div>}
    {!interpretation && !interpretationRefreshPending && hasSavedInterpretation && <div className="interpretation-result interpretation-saved" role="status" aria-live="polite">
      <div className="interpretation-result-top"><span className={`status-pill status-${(savedStatus ?? 'review').toLowerCase()}`}>{savedStatus ?? 'Saved proposal'}</span><span className="interpretation-source">Saved proposal state</span></div>
      <strong>Saved interpretation is attached to this proposal.</strong>
      {savedStatus && proposalRevision !== undefined && <span className="interpretation-detail"><strong>Proposal update:</strong> {savedStatus} at revision {proposalRevision}.</span>}
      {savedStatus === 'PROPOSED' && <span className="interpretation-detail"><strong>Saved allocation:</strong> {interpretationRefreshFailed ? 'Allocation details could not be refreshed; refresh the proposal before applying.' : allocation.length ? allocation.join('; ') : 'No cash or credit lines are saved.'}</span>}
      {savedStatus === 'NEEDS_REVIEW' && <span className="interpretation-detail"><strong>Saved allocation:</strong> unresolved; no allocation was saved.</span>}
      {savedReason && <span className="interpretation-detail"><strong>Saved proposal reason:</strong> {savedReason}</span>}
      {evidence.length > 0 && <div className="interpretation-citations"><strong>Saved proposal evidence</strong><ul>{evidence.map((item, index) => <li key={`${item.source_id}-${item.start}-${item.end}-${index}`}><span><span className="mono">Source {item.source_id}</span>: “{item.quote}”</span>{onSource && <button className="button button-quiet" type="button" onClick={() => onSource(item.source_id)}>Open source</button>}</li>)}</ul></div>}
      {nextAction && <span className="interpretation-next"><strong>Next reviewer action:</strong> {nextAction}</span>}
    </div>}
  </section>
}

function progressLabel(stage: string) {
  switch (stage) {
    case 'load_observations': return 'Preparing payment evidence'
    case 'enumerate_candidates': return 'Preparing possible matches'
    case 'rank_if_hybrid': return 'Checking optional rank context'
    case 'compile_and_validate': return 'Checking the request'
    case 'read_cache': return 'Checking for a saved result'
    case 'reserve_and_call': return 'Waiting for DeepSeek'
    case 'validate_and_cache': return 'Checking the proposed match'
    case 'record_proposal_revision': return 'Preparing the review result'
    case 'workflow': return 'Workflow'
    default: return 'Working'
  }
}

function progressSummary(progress: InterpretationProgress) {
  if (progress.status === 'failed') return 'Needs attention before a result can be shown.'
  if (progress.status === 'skipped') return 'Not needed for this request.'
  if (progress.status === 'running') {
    if (progress.stage === 'reserve_and_call') return 'Waiting for the provider response.'
    if (progress.stage === 'record_proposal_revision') return 'Preparing the review result.'
    return 'In progress.'
  }
  if (progress.stage === 'record_proposal_revision') return 'Ready for your review.'
  return 'Done.'
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

export function ApplyConfirmation({ detail, cash, credits, balances, busy, onCancel, onConfirm }: { detail: Pick<ProposalDetail, 'revision'>; cash: CashDraft[]; credits: CreditDraft[]; balances: Array<InvoiceBalance & { invoice_id: string }>; busy: string; onCancel: () => void; onConfirm: () => void }) {
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
  return <dialog className="confirmation-dialog" role="dialog" aria-modal="true" aria-labelledby="apply-confirmation-heading" aria-describedby="apply-confirmation-description" ref={dialogRef} onCancel={(event) => { event.preventDefault(); if (!busy) onCancel() }}><p className="eyebrow">Final confirmation</p><h2 id="apply-confirmation-heading">Apply persisted allocation?</h2><p id="apply-confirmation-description">Review revision <strong>{detail.revision}</strong> will be recorded with the following values.</p><div className="confirmation-section"><h3>Cash</h3>{cash.length === 0 ? <p className="muted">No cash lines.</p> : <ul>{cash.map((line, index) => <li key={`cash-${index}`}><span>Invoice <span className="mono">{line.invoice_id}</span></span><strong>{line.amount_mxn} MXN</strong></li>)}</ul>}</div><div className="confirmation-section"><h3>Credit</h3>{credits.length === 0 ? <p className="muted">No credit lines.</p> : <ul>{credits.map((line, index) => <li key={`credit-${index}`}><span>Credit note <span className="mono">{line.credit_note_id}</span> → invoice <span className="mono">{line.invoice_id}</span></span><strong>{line.amount_mxn} MXN</strong></li>)}</ul>}</div><div className="confirmation-section"><h3>Projected invoice balances</h3>{projected.length === 0 ? <p className="muted">No projected balances returned.</p> : <ul>{projected.map((balance, index) => <li key={index}><span className="mono">Invoice {balance.invoice_id}</span><strong>{money('projected_remaining_amount' in balance ? balance.projected_remaining_amount : balance.remaining_amount)}</strong></li>)}</ul>}</div><p className="confirmation-note">Reconcile records an allocation for audit purposes; it does not move money in a bank account.</p><div className="confirmation-actions"><button className="button button-secondary" type="button" onClick={onCancel} disabled={Boolean(busy)}>Cancel</button><button className="button button-primary" type="button" onClick={onConfirm} disabled={Boolean(busy)}>{busy === 'apply' ? 'Applying…' : 'Confirm and apply'}</button></div></dialog>
}
function AlternativesSection({ alternatives }: { alternatives: string[][] }) { return <section className="panel alternatives-card"><div className="panel-heading"><div><h2>Alternatives</h2><p className="muted">Plausible alternatives remain visible for review.</p></div></div>{alternatives.length === 0 ? <p className="empty-inline">No alternatives returned.</p> : <ul className="alternative-list">{alternatives.map((alternative, index) => <li key={index}><strong>{alternative.join(' → ')}</strong><span>Returned as an equally feasible combination.</span></li>)}</ul>}</section> }
function ExportCard() { return <section className="panel export-card"><p className="eyebrow">History</p><h2>Export applications</h2><p className="muted">Download active and historical applications as RFC 4180 CSV.</p><a className="button button-secondary full-width" href={exportUrl()} download>Download CSV</a></section> }
function PageHeading({ eyebrow, title, description }: { eyebrow: string; title: string; description: React.ReactNode }) { return <div className="page-heading"><p className="eyebrow">{eyebrow}</p><h1>{title}</h1><p>{description}</p></div> }
function Data({ label, value, mono }: { label: string; value: string; mono?: boolean }) { return <div><dt>{label}</dt><dd className={mono ? 'mono' : ''}>{value}</dd></div> }
function Balance({ label, value }: { label: string; value?: number }) { return <div className="balance"><span>{label}</span><strong>{money(value)}</strong></div> }
function EmptyState({ title, body, action }: { title: string; body: string; action?: React.ReactNode }) { return <div className="empty-state"><span className="empty-symbol" aria-hidden="true">○</span><h2>{title}</h2><p>{body}</p>{action}</div> }

export default App
