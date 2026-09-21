import type { Comparison, ComparisonAllocation, ComparisonMethod, ComparisonUsage } from './types'
import { centsToMxn } from './money'

function amount(value: number) {
  try {
    return `MXN ${centsToMxn(value)}`
  } catch {
    return 'Amount unavailable'
  }
}

export function comparisonMethodLabel(method: string) {
  return method.replace(/[-_]+/g, ' ')
}

export function comparisonStatusLabel(status: string) {
  if (status === 'proposed') return 'Proposed observation'
  if (status === 'deferred') return 'Deferred observation'
  if (status === 'unavailable') return 'Unavailable'
  if (status === 'failed') return 'Failed'
  return status.replace(/[-_]+/g, ' ')
}

export function comparisonSourceLabel(source: string) {
  if (source === 'rules') return 'Deterministic rules'
  if (source === 'local') return 'Local model execution'
  if (source === 'live') return 'Live output'
  if (source === 'cache') return 'Validated cache'
  if (source === 'recorded') return 'Recorded observation'
  if (source === 'unavailable') return 'Unavailable'
  return `Unknown (${source})`
}

export function formatComparisonDuration(durationMs: number | null) {
  return durationMs === null ? 'Not measured' : `${durationMs} ms`
}

export function formatComparisonUsage(usage: ComparisonUsage | null) {
  if (!usage) return 'Unknown'
  const input = usage.input_tokens === null ? 'input unknown' : `input ${usage.input_tokens}`
  const output = usage.output_tokens === null ? 'output unknown' : `output ${usage.output_tokens}`
  return `${input} · ${output} tokens`
}

export function formatComparisonCost(costUsd: number | null) {
  return costUsd === null ? 'Unknown' : `USD ${costUsd.toFixed(6)}`
}

function AllocationLines({ candidate }: { candidate: ComparisonAllocation | null }) {
  if (!candidate) return <p className="empty-inline">No allocation candidate returned.</p>

  return <div className="comparison-allocation">
    <div>
      <h4>Cash lines</h4>
      {candidate.cash.length === 0 ? <p className="empty-inline">No cash lines.</p> : <ul>
        {candidate.cash.map((line, index) => <li key={`cash-${line.invoice_id}-${index}`}><span>Invoice <span className="mono">{line.invoice_id}</span></span><strong>{amount(line.amount)}</strong></li>)}
      </ul>}
    </div>
    <div>
      <h4>Credit lines</h4>
      {candidate.credits.length === 0 ? <p className="empty-inline">No credit lines.</p> : <ul>
        {candidate.credits.map((line, index) => <li key={`credit-${line.credit_note_id}-${index}`}><span>Credit note <span className="mono">{line.credit_note_id}</span> → invoice <span className="mono">{line.invoice_id}</span></span><strong>{amount(line.amount)}</strong></li>)}
      </ul>}
    </div>
  </div>
}

function MethodObservation({ method }: { method: ComparisonMethod }) {
  return <article className="comparison-method" aria-label={`${comparisonMethodLabel(method.method)} comparison observation`}>
    <div className="comparison-method-heading">
      <div>
        <h3>{comparisonMethodLabel(method.method)}</h3>
        <span className={`status-pill status-${method.status}`}>{comparisonStatusLabel(method.status)}</span>
      </div>
      <span className="comparison-source">{comparisonSourceLabel(method.source)}</span>
    </div>
    {method.reason && <p className="comparison-reason">{method.reason}</p>}
    <dl className="comparison-facts">
      <div><dt>Timing</dt><dd>{formatComparisonDuration(method.duration_ms)}</dd></div>
      <div><dt>Usage</dt><dd>{formatComparisonUsage(method.usage)}</dd></div>
      <div><dt>Cost</dt><dd>{formatComparisonCost(method.cost_usd)}</dd></div>
      <div><dt>Reviewer action</dt><dd>No action from this observation</dd></div>
    </dl>
    {method.raw_score !== undefined && method.raw_score !== null && <p className="comparison-score"><strong>Raw score (not confidence)</strong> {method.raw_score}</p>}
    <div className="comparison-candidate">
      <h4>Observed allocation candidate</h4>
      <AllocationLines candidate={method.candidate} />
    </div>
  </article>
}

export function ComparisonPanel({ comparison, loading = false, error = null, onCompare, canCompare = true, disabledReason }: { comparison?: Comparison | null; loading?: boolean; error?: string | null; onCompare?: () => void; canCompare?: boolean; disabledReason?: string }) {
  return <section className="panel comparison-panel" aria-labelledby="comparison-heading">
    <div className="panel-heading">
      <div>
        <p className="eyebrow">Method comparison</p>
        <h2 id="comparison-heading">Review-only observations</h2>
      </div>
      <div className="comparison-controls">
        {comparison && <span className="status-pill">Revision {comparison.revision}</span>}
        {onCompare && <button className="button button-secondary" type="button" onClick={onCompare} disabled={!canCompare || loading}>{loading ? 'Comparing…' : 'Compare methods'}</button>}
      </div>
    </div>
    {disabledReason && <p className="comparison-note" role="status">{disabledReason}</p>}
    {loading ? <p className="empty-inline">Loading method observations…</p> : error ? <p className="error-banner" role="alert">Comparison unavailable: {error}</p> : comparison ? <>
      <p className="comparison-meta">{comparison.input_fingerprint ? <>Same input snapshot · fingerprint <span className="mono">{comparison.input_fingerprint}</span></> : 'Input snapshot fingerprint unavailable; same-input identity could not be verified.'}</p>
      <p className="comparison-note">These methods are shown side by side for review. Every observation is review-only and cannot apply money.</p>
      {comparison.methods.length === 0 ? <p className="empty-inline">No method observations were recorded.</p> : <div className="comparison-methods">{comparison.methods.map((method) => <MethodObservation key={method.method} method={method} />)}</div>}
    </> : <p className="empty-inline">No comparison was recorded for this revision.</p>}
  </section>
}

export default ComparisonPanel
