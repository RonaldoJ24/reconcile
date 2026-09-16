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
    <th scope="row">{row.method}</th>
    <td>{row.split}</td>
    <td>{row.groups}</td>
    <td>{row.proposals}</td>
    <td>{row.correct_proposals_per_v1_labels}</td>
    <td>{row.incorrect_proposals_per_v1_labels}</td>
    <td>{formatEvaluationPercent(row.precision)}</td>
    <td>{formatEvaluationPercent(row.coverage)}</td>
    <td>{row.underdetermined_groups}</td>
    <td>{row.abstained_underdetermined}</td>
  </tr>
}

export function EvaluationView({ evaluation, loading = false, error = null, onRetry }: { evaluation?: EvaluationResponse | null; loading?: boolean; error?: string | null; onRetry?: () => void }) {
  return <section className="panel evaluation-panel" aria-labelledby="evaluation-heading">
    <div className="panel-heading">
      <div>
        <p className="eyebrow">Historical evaluation</p>
        <h2 id="evaluation-heading">What the preserved report measured</h2>
      </div>
      {evaluation && <span className="status-pill">{evaluation.schema_version}</span>}
    </div>
    {loading ? <p className="empty-inline" role="status">Loading evaluation summary…</p> : error ? <div className="evaluation-error"><p className="error-banner" role="alert">Evaluation unavailable: {error}</p>{onRetry && <button className="button button-secondary" type="button" onClick={onRetry}>Retry evaluation</button>}</div> : !evaluation ? <p className="empty-inline">Evaluation summary is unavailable from the server.</p> : <>
      <p className="evaluation-active">Active engine: <strong>{evaluation.active_engine}</strong></p>
      <div className="evaluation-v2" aria-label="Version two evaluation status">
        <strong>V2 evaluation: {evaluationStatusLabel(evaluation.v2.status)}</strong>
        <span>Independent domain review: {evaluationStatusLabel(evaluation.v2.independent_domain_review)}</span>
        <span>Provider calls in this continuation: {evaluation.v2.provider_calls_this_continuation}</span>
        <span>Final access in this continuation: {evaluation.v2.final_access_this_continuation ? 'Yes' : 'No'}</span>
      </div>
      <div className="table-wrap evaluation-table-wrap">
        <table>
          <caption>Historical method and split results</caption>
          <thead><tr><th scope="col">Method</th><th scope="col">Split</th><th scope="col">Groups</th><th scope="col">Proposals</th><th scope="col">Correct v1 labels</th><th scope="col">Incorrect v1 labels</th><th scope="col">Precision</th><th scope="col">Coverage</th><th scope="col">Underdetermined</th><th scope="col">Abstained on underdetermined cases</th></tr></thead>
          <tbody>{evaluation.historical.length === 0 ? <tr><td colSpan={10}>No historical rows were returned.</td></tr> : evaluation.historical.map((row) => <HistoricalRow key={`${row.method}-${row.split}`} row={row} />)}</tbody>
        </table>
      </div>
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
}

export default EvaluationView
