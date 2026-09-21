import type { ReliabilityEffects, ReliabilityExperiment, ReliabilityResult } from './types'

export const RELIABILITY_EXPERIMENTS: ReliabilityExperiment[] = ['invalid_allocation', 'invalid_citation', 'stale_apply', 'duplicate_apply']

export function reliabilityExperimentLabel(experiment: ReliabilityExperiment) {
  if (experiment === 'invalid_allocation') return 'Invalid allocation'
  if (experiment === 'invalid_citation') return 'Invalid citation'
  if (experiment === 'stale_apply') return 'Stale apply'
  return 'Duplicate apply'
}

function experimentDescription(experiment: ReliabilityExperiment) {
  if (experiment === 'invalid_allocation') return 'Checks the shared allocation validator with a synthetic invalid amount or ID.'
  if (experiment === 'invalid_citation') return 'Checks the shared citation validator with a synthetic invalid source span.'
  if (experiment === 'stale_apply') return 'Uses an intentionally discordant version token and verifies that financial effects stay unchanged.'
  return 'Reuses an already approved operation with its same key and payload; it never creates the first application.'
}

function json(value: Record<string, unknown>) {
  return JSON.stringify(value, null, 2)
}

function EffectsTable({ before, after }: { before: ReliabilityEffects; after: ReliabilityEffects }) {
  return <div className="table-wrap reliability-effects-wrap"><table className="reliability-effects"><caption>Persisted financial effects</caption><thead><tr><th scope="col">Effect</th><th scope="col">Before</th><th scope="col">After</th></tr></thead><tbody>
    <tr><th scope="row">Application groups</th><td>{before.application_groups}</td><td>{after.application_groups}</td></tr>
    <tr><th scope="row">Cash applications</th><td>{before.cash_applications}</td><td>{after.cash_applications}</td></tr>
    <tr><th scope="row">Credit applications</th><td>{before.credit_applications}</td><td>{after.credit_applications}</td></tr>
    <tr><th scope="row">Cash centavos</th><td>{before.cash_centavos}</td><td>{after.cash_centavos}</td></tr>
    <tr><th scope="row">Credit centavos</th><td>{before.credit_centavos}</td><td>{after.credit_centavos}</td></tr>
  </tbody></table></div>
}

export function ReliabilityPanel({
  status,
  revision,
  selectedExperiment,
  result,
  loading = false,
  disabled = false,
  selectionDisabled = false,
  disabledReason,
  error,
  onExperimentChange,
  onRun,
}: {
  status: string
  revision: number
  selectedExperiment: ReliabilityExperiment
  result?: ReliabilityResult
  loading?: boolean
  /** Disable the experiment picker while the current revision is changing. */
  selectionDisabled?: boolean
  disabled?: boolean
  disabledReason?: string
  error?: string
  onExperimentChange: (experiment: ReliabilityExperiment) => void
  onRun: () => void
}) {
  const duplicate = selectedExperiment === 'duplicate_apply'
  return <section className="panel reliability-panel" aria-labelledby="reliability-heading">
    <div className="panel-heading"><div><p className="eyebrow">Reliability lab</p><h2 id="reliability-heading">Synthetic check</h2></div><span className="status-pill">Revision {revision}</span></div>
    <p className="reliability-warning"><strong>Synthetic check.</strong> This sends a named experiment to the server validator. It does not represent provider output or create an autonomous financial action.</p>
    <label>Experiment
      <select value={selectedExperiment} onChange={(event) => onExperimentChange(event.target.value as ReliabilityExperiment)} disabled={selectionDisabled || loading}>
        {RELIABILITY_EXPERIMENTS.map((experiment) => <option key={experiment} value={experiment}>{reliabilityExperimentLabel(experiment)}</option>)}
      </select>
    </label>
    <p className="field-help">{experimentDescription(selectedExperiment)}</p>
    {duplicate && <p className="reliability-note">Duplicate apply is available only after an explicit reviewer application. The check reuses that approved operation's key and payload and must return the same application ID without new effects.</p>}
    {disabledReason && <p className="draft-status" role="status">{disabledReason}</p>}
    {error && <p className="draft-status draft-error" role="alert">{error}</p>}
    <button className="button button-secondary" type="button" onClick={onRun} disabled={disabled || loading}>{loading ? 'Running synthetic check…' : 'Run synthetic check'}</button>
    {result && <div className="reliability-result" role="status" aria-live="polite">
      <div className="reliability-result-heading"><h3>Server result</h3><span className={result.passed ? 'status-pill status-succeeded' : 'status-pill status-failed'}>{result.passed ? 'Passed' : 'Failed'}</span></div>
      <dl className="reliability-facts"><div><dt>Experiment</dt><dd>{reliabilityExperimentLabel(result.experiment)}</dd></div><div><dt>Validator</dt><dd className="mono">{result.validator}</dd></div><div><dt>Application ID</dt><dd className="mono">{result.application_id ?? 'No application ID returned'}</dd></div></dl>
      <details><summary>Expected server behavior</summary><pre>{json(result.expected)}</pre></details>
      <details><summary>Observed server behavior</summary><pre>{json(result.observed)}</pre></details>
      <EffectsTable before={result.effects_before} after={result.effects_after} />
    </div>}
  </section>
}

export default ReliabilityPanel
