import type { EvaluationHistoricalRow, EvaluationResponse, EvaluationV2, EvaluationV2Result } from './types'

export function formatEvaluationPercent(value: number) {
  return Number.isFinite(value) ? `${(value * 100).toFixed(1)}%` : 'Unknown'
}

export function evaluationStatusLabel(status: string) {
  if (status === 'not_evaluated') return 'Not evaluated'
  if (status === 'pending') return 'Pending'
  if (status === 'complete') return 'Complete'
  return status.replace(/[-_]+/g, ' ')
}

function HistoricalRow({ row }: { row: EvaluationHistoricalRow }) {
  return <tr>
    <th scope="row" className="evaluation-method">{row.method}</th>
    <td data-label="Groups">{row.groups}</td>
    <td data-label="Proposals">{row.proposals}</td>
    <td data-label="Correct v1 labels">{row.correct_proposals_per_v1_labels}</td>
    <td data-label="Incorrect v1 labels">{row.incorrect_proposals_per_v1_labels}</td>
    <td data-label="Precision">{formatEvaluationPercent(row.precision)}</td>
    <td data-label="Coverage">{formatEvaluationPercent(row.coverage)}</td>
    <td data-label="Underdetermined">{row.underdetermined_groups}</td>
    <td data-label="Abstained on underdetermined cases">{row.abstained_underdetermined}</td>
  </tr>
}

function HistoricalSplit({ split, rows }: { split: string; rows: EvaluationHistoricalRow[] }) {
  return <section className="evaluation-split" aria-labelledby={`evaluation-split-${split}`}>
    <div className="evaluation-split-heading">
      <h3 id={`evaluation-split-${split}`}>{split}</h3>
      <span className="muted">{rows.length} method{rows.length === 1 ? '' : 's'}</span>
    </div>
    <div className="table-wrap evaluation-table-wrap">
      <table>
        <caption className="sr-only">Historical results for the {split} split</caption>
        <thead><tr><th scope="col">Method</th><th scope="col">Groups</th><th scope="col">Proposals</th><th scope="col">Correct v1 labels</th><th scope="col">Incorrect v1 labels</th><th scope="col">Precision</th><th scope="col">Coverage</th><th scope="col">Underdetermined</th><th scope="col">Abstained on underdetermined cases</th></tr></thead>
        <tbody>{rows.map((row) => <HistoricalRow key={`${row.method}-${row.split}`} row={row} />)}</tbody>
      </table>
    </div>
  </section>
}

const V2_METHOD_LABELS: Record<string, string> = {
  rules: 'Rules alone',
  ranker: 'Shadow ranker (threshold chosen on validation)',
  rules_then_direct: 'Rules, then the model (production path)',
}

const V2_SCOPE_LABELS: Record<string, string> = {
  'reserved-final': 'Held-out final split',
  'all-cases': 'All cases',
}

const formatMxn = (centavos: number) =>
  new Intl.NumberFormat('en-MX', { style: 'currency', currency: 'MXN' }).format(centavos / 100)

function V2ResultRow({ row }: { row: EvaluationV2Result }) {
  return <tr>
    <th scope="row" className="evaluation-method">{row.label ?? V2_METHOD_LABELS[row.method] ?? row.method}{row.note && <span className="evaluation-row-note">{row.note}</span>}</th>
    <td data-label="Proposed">{row.proposals}</td>
    <td data-label="Right">{row.correct}</td>
    <td data-label="Wrong">{row.unsupported}</td>
    <td data-label="Deferred correctly">{row.correct_deferrals}</td>
    <td data-label="Deferred but answerable">{row.unnecessary_deferrals}</td>
    <td data-label="Unavailable">{row.unavailable_or_error}</td>
    <td data-label="Answerable resolved">{`${row.correct} of ${row.answerable}`}</td>
    <td data-label="Value of wrong proposals">{formatMxn(row.misallocated_centavos)}</td>
  </tr>
}

// The v2 run's counts, packaged from its published report; nothing here is recomputed.
export function V2Results({ v2 }: { v2: EvaluationV2 }) {
  const rows = v2.results ?? []
  if (rows.length === 0) return null
  const scopes = Array.from(new Set(rows.map((row) => row.scope)))
  return <section className="evaluation-v2-results" aria-labelledby="evaluation-v2-results-heading">
    <h3 id="evaluation-v2-results-heading">New evaluation (v2): what each method did</h3>
    {v2.cases && <p className="muted">{`${v2.cases.evaluated} synthetic cases written by AI agents from a domain brief, run once against the frozen system. ${v2.cases.answerable} could be allocated by a careful analyst; ${v2.cases.unreachable} of those need an allocation the system cannot represent.`}</p>}
    {scopes.map((scope) => <div className="table-wrap evaluation-table-wrap" key={scope}>
      <table className="evaluation-v2-table">
        <caption>{`${V2_SCOPE_LABELS[scope] ?? scope}: ${rows.find((row) => row.scope === scope)?.cases ?? 0} cases`}</caption>
        <thead><tr><th scope="col">Method</th><th scope="col">Proposed</th><th scope="col">Right</th><th scope="col">Wrong</th><th scope="col">Deferred correctly</th><th scope="col">Deferred but answerable</th><th scope="col">Unavailable</th><th scope="col">Answerable resolved</th><th scope="col">Value of wrong proposals</th></tr></thead>
        <tbody>{rows.filter((row) => row.scope === scope).map((row) => <V2ResultRow key={`${scope}-${row.method}-${row.label ?? ''}`} row={row} />)}</tbody>
      </table>
    </div>)}
    {v2.findings && v2.findings.length > 0 && <ul className="evaluation-v2-findings">{v2.findings.map((finding) => <li key={finding}>{finding}</li>)}</ul>}
    {v2.provider && <p className="muted">{`${v2.provider.label ?? 'Model'}: ${v2.provider.attempts} calls, about US$${v2.provider.estimated_cost_usd.toFixed(2)} at list price${v2.provider.latency_ms_p50 !== null ? `, median ${(v2.provider.latency_ms_p50 / 1000).toFixed(1)} s per call` : ''}.`}</p>}
    {v2.report_path && <p className="muted">{`Full report, cases, labels and every recorded model call: ${v2.report_path}`}</p>}
  </section>
}

// Summarizes the preserved final-split rows in plain language; numbers come from the report.
export function HistoricalTakeaway({ rows }: { rows: EvaluationHistoricalRow[] }) {
  const final = rows.filter((row) => row.split === 'final')
  const rules = final.find((row) => row.method.startsWith('rules'))
  const ranker = final.find((row) => row.method.startsWith('ranker'))
  if (!rules && !ranker) return null
  const line = (label: string, row: EvaluationHistoricalRow) => `${label} proposed an allocation in ${row.proposals} of ${row.groups} cases; ${row.correct_proposals_per_v1_labels} were right (${formatEvaluationPercent(row.precision)}).`
  return <div className="evaluation-takeaway">
    {rules && <p><strong>{line('The rules', rules)}</strong></p>}
    {ranker && <p>{line('The trained ranker', ranker)} It had no way to abstain when the evidence was insufficient, so it guessed on every case. It stays in shadow mode and never decides.</p>}
  </div>
}

export function EvaluationView({ evaluation, loading = false, error = null, onRetry }: { evaluation?: EvaluationResponse | null; loading?: boolean; error?: string | null; onRetry?: () => void }) {
  const splits = evaluation ? Array.from(new Set(evaluation.historical.map((row) => row.split))) : []
  return <div className="evaluation-view">
    <section className="panel evaluation-current" aria-labelledby="evaluation-current-heading">
      <div className="panel-heading">
        <div>
          <p className="eyebrow">Current engine</p>
          <h2 id="evaluation-current-heading">What runs for new proposals</h2>
        </div>
        {evaluation && <span className="status-pill">{evaluation.active_engine}</span>}
      </div>
      {loading ? <p className="empty-inline" role="status">Loading current engine status…</p> : evaluation ? <>
        <p className="evaluation-active">Active engine: <strong>{evaluation.active_engine}</strong></p>
        <div className="evaluation-v2" aria-label="Version two evaluation status">
          <strong>New evaluation (v2): {evaluationStatusLabel(evaluation.v2.status)}</strong>
          <span>Independent accountant review: {evaluationStatusLabel(evaluation.v2.independent_domain_review)}</span>
          <span>Live model calls counted in v2: {evaluation.v2.provider_calls_this_continuation}</span>
          <span>Held-out final set opened: {evaluation.v2.final_access_this_continuation ? 'Yes' : 'No'}</span>
        </div>
        <V2Results v2={evaluation.v2} />
      </> : <p className="empty-inline">Current engine status is unavailable from the server.</p>}
    </section>
    <section className="panel evaluation-panel" aria-labelledby="evaluation-heading">
      <div className="panel-heading">
        <div>
          <p className="eyebrow">Historical evaluation</p>
          <h2 id="evaluation-heading">What the preserved report measured</h2>
        </div>
        {evaluation && <span className="status-pill">{evaluation.schema_version}</span>}
      </div>
      {loading ? <p className="empty-inline" role="status">Loading evaluation summary…</p> : error ? <div className="evaluation-error"><p className="error-banner" role="alert">Evaluation unavailable: {error}</p>{onRetry && <button className="button button-secondary" type="button" onClick={onRetry}>Retry evaluation</button>}</div> : !evaluation ? <p className="empty-inline">Evaluation summary is unavailable from the server.</p> : <>
        <HistoricalTakeaway rows={evaluation.historical} />
        {evaluation.historical.length === 0 ? <p className="empty-inline">No historical rows were returned.</p> : <div className="evaluation-splits">{splits.map((split) => <HistoricalSplit key={split} split={split} rows={evaluation.historical.filter((row) => row.split === split)} />)}</div>}
        <p className="evaluation-disclaimer">Correctness uses the preserved v1 label definition; it does not establish independent semantic support.</p>
        <details className="evaluation-provenance">
          <summary>Report provenance</summary>
          <dl className="evaluation-facts">
            <div><dt>Report</dt><dd>{evaluation.provenance.report_path}</dd></div>
            <div><dt>Report SHA-256</dt><dd className="mono">{evaluation.provenance.report_sha256}</dd></div>
            <div><dt>Evaluated at</dt><dd>{evaluation.provenance.evaluated_at}</dd></div>
            <div><dt>Release commit</dt><dd className="mono">{evaluation.provenance.release_commit}</dd></div>
          </dl>
        </details>
        {evaluation.limitations.length > 0 && <details className="evaluation-limitations">
          <summary>Limitations ({evaluation.limitations.length})</summary>
          <ul>{evaluation.limitations.map((limitation, index) => <li key={`${index}-${limitation}`}>{limitation}</li>)}</ul>
        </details>}
      </>}
    </section>
  </div>
}

export default EvaluationView
