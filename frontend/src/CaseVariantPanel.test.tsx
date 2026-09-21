import { renderToString } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import CaseVariantPanel, { caseVariantLabel } from './CaseVariantPanel'

describe('CaseVariantPanel', () => {
  it('shows the server variant and preserves the historical evidence warning', () => {
    const markup = renderToString(<CaseVariantPanel
      caseId="bundle"
      scenarioVersion="v1"
      currentVariant="original"
      selectedVariant="ambiguous"
      onVariantChange={() => {}}
      onApply={() => {}}
    />)

    expect(markup).toContain('Original message')
    expect(markup).toContain('Ambiguous message')
    expect(markup).toContain('Earlier source records remain in history')
    expect(markup).toContain('balances are not restored')
  })

  it('explains why a variant change is disabled', () => {
    const markup = renderToString(<CaseVariantPanel
      caseId="bundle"
      scenarioVersion="v1"
      selectedVariant="original"
      disabled
      disabledReason="Save or discard unsaved correction changes before changing the case variant."
      onVariantChange={() => {}}
      onApply={() => {}}
    />)

    expect(markup).toContain('Save or discard unsaved correction changes')
    expect(markup).toMatch(/select[^>]+disabled/)
    expect(markup).toMatch(/button[^>]+disabled/)
    expect(caseVariantLabel('prompt_like')).toBe('Prompt-like message')
  })
})
