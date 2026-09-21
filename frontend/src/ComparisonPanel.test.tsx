import { renderToString } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import ComparisonPanel, { comparisonSourceLabel, formatComparisonCost, formatComparisonDuration, formatComparisonUsage } from './ComparisonPanel'
import type { Comparison } from './types'

const comparison: Comparison = {
  revision: 4,
  input_fingerprint: 'f'.repeat(64),
  methods: [
    {
      method: 'rules-v2-conservative',
      status: 'proposed',
      source: 'rules',
      candidate: {
        cash: [{ invoice_id: '101', amount: 10000 }],
        credits: [{ credit_note_id: '103', invoice_id: '102', amount: 5000 }],
      },
      actionable: false,
      raw_score: 0.75,
      duration_ms: 12,
      usage: { input_tokens: null, output_tokens: null, provider_cache_tokens: null, reasoning_tokens: null },
      cost_usd: null,
      reason: null,
    },
    {
      method: 'direct',
      status: 'unavailable',
      source: 'unavailable',
      candidate: null,
      actionable: false,
      raw_score: null,
      duration_ms: null,
      usage: null,
      cost_usd: null,
      reason: 'No authenticated exact-input observation is available.',
    },
  ],
}

describe('ComparisonPanel', () => {
  it('renders persisted methods, complete allocation IDs, raw scores, and unknown telemetry', () => {
    const markup = renderToString(<ComparisonPanel comparison={comparison} />)
    const plainMarkup = markup.replace(/<!-- -->/g, '')

    expect(plainMarkup).toContain('Revision 4')
    expect(markup).toContain('Invoice <span class="mono">101</span>')
    expect(markup).toContain('Credit note <span class="mono">103</span>')
    expect(markup).toContain('MXN 100.00')
    expect(markup).toContain('MXN 50.00')
    expect(markup).toContain('Raw score (not confidence)')
    expect(markup).toContain('Not measured')
    expect(markup).toContain('Unknown')
    expect(markup).toContain('cannot apply money')
    expect(markup).not.toContain('winner')
    expect(markup).not.toContain('<button')
  })

  it('keeps an empty comparison explicit', () => {
    expect(renderToString(<ComparisonPanel />)).toContain('No comparison was recorded for this revision.')
  })

  it('labels transport failures as unavailable instead of a deferred result', () => {
    const markup = renderToString(<ComparisonPanel error="Revision changed" />).replace(/<!-- -->/g, '')
    expect(markup).toContain('Comparison unavailable: Revision changed')
    expect(markup).not.toContain('Deferred observation')
  })

  it('does not turn absent telemetry into measured values', () => {
    expect(formatComparisonDuration(null)).toBe('Not measured')
    expect(formatComparisonUsage(null)).toBe('Unknown')
    expect(formatComparisonCost(null)).toBe('Unknown')
  })

  it('explains local observations and an unavailable input fingerprint', () => {
    expect(comparisonSourceLabel('local')).toBe('Local model execution')
    const markup = renderToString(<ComparisonPanel comparison={{ ...comparison, input_fingerprint: null }} />)
    expect(markup).toContain('Input snapshot fingerprint unavailable; same-input identity could not be verified.')
  })
})
