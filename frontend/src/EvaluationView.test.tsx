import { renderToString } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import EvaluationView, { V2Results, evaluationStatusLabel, formatEvaluationPercent } from './EvaluationView'
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
    expect(plainMarkup).toContain('Live model calls counted in v2: 0')
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

describe('v2 results', () => {
  it('renders each method with counts per scope and stays empty without results', () => {
    const row = (scope: string, method: string, correct: number) => ({
      scope, method, cases: 36, answerable: 20, proposals: correct + 1, correct, unsupported: 1,
      correct_deferrals: 10, unnecessary_deferrals: 20 - correct, unavailable_or_error: 0, misallocated_centavos: 1000,
    })
    const markup = renderToString(<V2Results v2={{
      status: 'complete', provider_calls_this_continuation: 140, final_access_this_continuation: true, independent_domain_review: 'pending',
      cases: { authored: 180, evaluated: 178, excluded: 2, answerable: 110, unreachable: 12 },
      results: [
        row('reserved-final', 'rules', 5),
        row('reserved-final', 'rules_then_direct', 14),
        row('all-cases', 'rules', 30),
        { ...row('all-cases', 'ranker', 0), note: 'Excludes the validation cases' },
      ],
      findings: ['Every wrong proposal should have been a deferral.'],
      provider: { label: 'GPT-6 Luna', attempts: 140, estimated_cost_usd: 0.09, latency_ms_p50: 1100, latency_ms_p95: 2400 },
      report_path: 'reports/eval-v2/README.md',
    }} />).replace(/<!-- -->/g, '')

    expect(markup).toContain('Held-out final split: 36 cases')
    expect(markup).toContain('Rules, then the model (production path)')
    expect(markup).toContain('14 of 20')
    expect(markup).toContain('178 synthetic cases written by AI agents')
    expect(markup).toContain('GPT-6 Luna: 140 calls, about US$0.09 at list price, median 1.1 s per call.')
    expect(markup).toContain('Excludes the validation cases')
    expect(markup).toContain('MX$10.00')
    expect(markup).toContain('<li>Every wrong proposal should have been a deferral.</li>')
    expect(markup).toContain('every recorded model call: reports/eval-v2/README.md')
    expect(renderToString(<V2Results v2={{ status: 'not_evaluated', provider_calls_this_continuation: 0, final_access_this_continuation: false, independent_domain_review: 'pending' }} />)).toBe('')
  })
})
