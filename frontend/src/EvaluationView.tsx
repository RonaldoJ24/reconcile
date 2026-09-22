import type { EvaluationHistoricalRow, EvaluationResponse } from './types'

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
          <strong>V2 evaluation: {evaluationStatusLabel(evaluation.v2.status)}</strong>
          <span>Independent domain review: {evaluationStatusLabel(evaluation.v2.independent_domain_review)}</span>
          <span>Provider calls in this continuation: {evaluation.v2.provider_calls_this_continuation}</span>
          <span>Final access in this continuation: {evaluation.v2.final_access_this_continuation ? 'Yes' : 'No'}</span>
        </div>
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
