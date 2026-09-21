import { renderToString } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import ReliabilityPanel from './ReliabilityPanel'
import type { ReliabilityResult } from './types'

const result: ReliabilityResult = {
  experiment: 'invalid_citation',
  synthetic: true,
  validator: 'citation-validator-v1',
  expected: { passed: false, source_id: 'source-1' },
  observed: { passed: false, issue: 'span_out_of_bounds' },
  passed: true,
  application_id: null,
  effects_before: { application_groups: 1, cash_applications: 1, credit_applications: 0, cash_centavos: 10000, credit_centavos: 0 },
  effects_after: { application_groups: 1, cash_applications: 1, credit_applications: 0, cash_centavos: 10000, credit_centavos: 0 },
}

describe('ReliabilityPanel', () => {
  it('explains the synthetic check before rendering server results', () => {
    const markup = renderToString(<ReliabilityPanel
      status="PROPOSED"
      revision={4}
      selectedExperiment="invalid_allocation"
      onExperimentChange={() => {}}
      onRun={() => {}}
    />)

    expect(markup).toContain('Synthetic check')
    expect(markup).toContain('does not represent provider output')
    expect(markup).not.toContain('Server result')
  })

  it('renders opaque expected and observed payloads plus exact financial effects', () => {
    const markup = renderToString(<ReliabilityPanel
      status="APPLIED"
      revision={4}
      selectedExperiment="invalid_citation"
      result={result}
      onExperimentChange={() => {}}
      onRun={() => {}}
    />)

    expect(markup).toContain('Passed')
    expect(markup).toContain('citation-validator-v1')
    expect(markup).toContain('span_out_of_bounds')
    expect(markup).toContain('Application groups')
    expect(markup).toContain('Cash centavos')
    expect(markup).toContain('No application ID returned')
  })

  it('keeps the picker usable when only the selected experiment is inapplicable', () => {
    const markup = renderToString(<ReliabilityPanel
      status="NEEDS_REVIEW"
      revision={4}
      selectedExperiment="stale_apply"
      disabled
      disabledReason="Stale apply requires a PROPOSED revision so a discordant version token can be checked safely."
      onExperimentChange={() => {}}
      onRun={() => {}}
    />)

    expect(markup).toContain('Stale apply requires a PROPOSED revision')
    expect(markup).not.toMatch(/select[^>]+disabled/)
    expect(markup).toMatch(/button[^>]+disabled/)
  })

  it('can lock the picker independently while a revision request is active', () => {
    const markup = renderToString(<ReliabilityPanel
      status="PROPOSED"
      revision={4}
      selectedExperiment="invalid_allocation"
      selectionDisabled
      disabled
      disabledReason="Wait for the current request to finish before running a synthetic check."
      onExperimentChange={() => {}}
      onRun={() => {}}
    />)

    expect(markup).toMatch(/select[^>]+disabled/)
  })
})
