import type { CaseVariant } from './types'

export const CASE_VARIANTS: CaseVariant[] = ['original', 'ambiguous', 'prompt_like']

export function caseVariantLabel(variant: CaseVariant | undefined) {
  if (variant === 'prompt_like') return 'Prompt-like message'
  if (variant === 'ambiguous') return 'Ambiguous message'
  if (variant === 'original') return 'Original message'
  return 'Not returned'
}

function variantDescription(variant: CaseVariant | '') {
  if (variant === 'ambiguous') return 'Uses the registered message without an unambiguous instruction.'
  if (variant === 'prompt_like') return 'Uses the registered prompt-like wording.'
  if (variant === 'original') return 'Restores the registered original message.'
  return 'Choose one registered message variant.'
}

export function CaseVariantPanel({
  caseId,
  scenarioVersion,
  currentVariant,
  selectedVariant,
  loading = false,
  disabled = false,
  disabledReason,
  error,
  onVariantChange,
  onApply,
}: {
  caseId: string
  scenarioVersion: string
  currentVariant?: CaseVariant
  selectedVariant: CaseVariant | ''
  loading?: boolean
  disabled?: boolean
  disabledReason?: string
  error?: string
  onVariantChange: (variant: CaseVariant) => void
  onApply: () => void
}) {
  return <section className="panel case-variant-panel" aria-labelledby="case-variant-heading">
    <div className="panel-heading">
      <div><p className="eyebrow">Registered case input</p><h2 id="case-variant-heading">Change message variant</h2></div>
      <span className="status-pill">Case <span className="mono">{caseId}</span></span>
    </div>
    <p className="case-variant-meta">Current: <strong>{caseVariantLabel(currentVariant)}</strong> · Scenario <span className="mono">{scenarioVersion}</span></p>
    <p className="case-variant-note">The server replaces only the active registered message. Earlier source records remain in history and balances are not restored.</p>
    <label>Variant
      <select value={selectedVariant} onChange={(event) => onVariantChange(event.target.value as CaseVariant)} disabled={disabled || loading}>
        <option value="" disabled>Select a variant</option>
        {CASE_VARIANTS.map((variant) => <option key={variant} value={variant}>{caseVariantLabel(variant)}</option>)}
      </select>
    </label>
    <p className="field-help">{variantDescription(selectedVariant)}</p>
    {disabledReason && <p className="draft-status" role="status">{disabledReason}</p>}
    {error && <p className="draft-status draft-error" role="alert">{error}</p>}
    <button className="button button-secondary" type="button" onClick={onApply} disabled={disabled || loading || !selectedVariant}>{loading ? 'Changing variant…' : 'Change case variant'}</button>
  </section>
}

export default CaseVariantPanel
