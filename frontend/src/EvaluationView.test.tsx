import { renderToString } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import EvaluationView, { evaluationStatusLabel, formatEvaluationPercent } from './EvaluationView'
import type { EvaluationResponse } from './types'

const evaluation: EvaluationResponse = {
  schema_version: 'evaluation-summary-v1',
  active_engine: 'rules-v2-conservative',
  provenance: {
    kind: 'historical_aggregate',
    report_path: 'reports/release-v1/evaluation.json',
    report_sha256: 'a'.repeat(64),
    evaluated_at: '2026-09-14T23:12:08.754954+00:00',
    release_commit: 'b'.repeat(40),
  },
  historical: [{
    method: 'rules-v1',
    split: 'final',
    groups: 500,
    proposals: 300,
    correct_proposals_per_v1_labels: 250,
    incorrect_proposals_per_v1_labels: 50,
    precision: 0.8333333333,
    coverage: 0.6,
    underdetermined_groups: 250,
    abstained_underdetermined: 200,
  }],
  v2: {
    status: 'not_evaluated',
    provider_calls_this_continuation: 0,
    final_access_this_continuation: false,
    independent_domain_review: 'pending',
  },
  limitations: ['Historical timing excluded feature construction and inference.'],
}

describe('EvaluationView', () => {
  it('renders server packaged values and explicit v2 unknown state', () => {
    const markup = renderToString(<EvaluationView evaluation={evaluation} />)
    const plainMarkup = markup.replace(/<!-- -->/g, '')

    expect(markup).toContain('Active engine: <strong>rules-v2-conservative</strong>')
    expect(plainMarkup).toContain('Not evaluated')
    expect(plainMarkup).toContain('Independent accountant review: Pending')
    expect(plainMarkup).toContain('Live DeepSeek calls counted in v2: 0')
    expect(plainMarkup).toContain('Held-out final set opened: No')
    expect(plainMarkup).not.toContain('continuation')
    expect(plainMarkup).toContain('The rules proposed an allocation in 300 of 500 cases')
    expect(plainMarkup).toContain('500')
    expect(plainMarkup).toContain('300')
    expect(plainMarkup).toContain('83.3%')
    expect(plainMarkup).toContain('60.0%')
    expect(plainMarkup).toContain('Historical timing excluded feature construction and inference.')
    expect(plainMarkup).toContain('a'.repeat(64))
    expect(plainMarkup).toContain('Abstained on underdetermined cases')
  })

  it('does not fabricate a summary when the endpoint is unavailable', () => {
    expect(renderToString(<EvaluationView />)).toContain('Evaluation summary is unavailable from the server.')
    expect(renderToString(<EvaluationView loading />)).toContain('Loading evaluation summary…')
    expect(renderToString(<EvaluationView error="503: unavailable" onRetry={() => {}} />).replace(/<!-- -->/g, '')).toContain('Evaluation unavailable: 503: unavailable')
    expect(renderToString(<EvaluationView error="503: unavailable" onRetry={() => {}} />)).toContain('Retry evaluation')
  })

  it('formats server ratios and preserves unknown labels', () => {
    expect(formatEvaluationPercent(0.5)).toBe('50.0%')
    expect(evaluationStatusLabel('not_evaluated')).toBe('Not evaluated')
    expect(evaluationStatusLabel('future_state')).toBe('future state')
  })
})
