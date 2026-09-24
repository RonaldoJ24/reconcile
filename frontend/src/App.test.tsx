import { renderToString } from 'react-dom/server'
import { afterEach, describe, expect, it, vi } from 'vitest'
import App, { ApplyConfirmation, CasesView, DecisionSummary, DecisionTracePanel, InterpretationAction, SourceViewer, applyAttemptFingerprint, cashDraftToPayload, comparisonMatchesDetail, formatDateTime, parseAppRoute, projectedBalanceRows, reviewDraftError, reviewDraftsEqual, runJobsUntilSettled, shouldShowInterpretation, toCents } from './App'
import { centsToMxn, mxnToCents } from './money'
import { interpretProposal } from './api'
import type { Comparison, DecisionTrace, ProposalDetail, SourceRecord } from './types'

afterEach(() => vi.restoreAllMocks())

describe('Phase 4 interpretation UI', () => {
  it('does not request interpretation while the app renders', () => {
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)

    renderToString(<App />)

    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('leads with one AI reading action and keeps hybrid as a secondary option', () => {
    const markup = renderToString(<InterpretationAction enabled busy="" message="" onInterpret={async () => {}} />)

    expect(markup).toContain('>Read with AI<')
    expect(markup).toContain('>With ranking context<')
    expect(markup).toContain('never applies money')
    expect(markup).toContain('must quote the note')
  })

  it('keeps a selected result visible after the proposal refreshes', () => {
    const markup = renderToString(<InterpretationAction
      enabled={false}
      busy=""
      message=""
      interpretation={{ status: 'selected', source: 'cache', mode: 'direct', candidate_id: 'candidate-1', reason_code: 'evidence_supported' }}
      onInterpret={async () => {}}
    />)

    expect(markup).toContain('Validated cache')
    expect(markup).toContain('candidate-1')
    expect(markup).toContain('What the AI read')
    expect(markup).toContain('Next reviewer action:')
    expect(markup).not.toContain('>Read with AI<')
    expect(markup).not.toContain('>With ranking context<')
  })

  it('keeps the completed allocation outcome and reviewer action readable', () => {
    const markup = renderToString(<InterpretationAction
      enabled={false}
      busy=""
      message=""
      proposalStatus="PROPOSED"
      proposalRevision={4}
      cash={[{ invoice_id: '101', amount: 10000 }]}
      credits={[]}
      evidence={[{ source_id: 'source-1', start: 0, end: 12, quote: 'Invoice 101' }]}
      interpretation={{ status: 'selected', source: 'cache', mode: 'direct', reason_code: 'evidence_supported', citations: [{ source_id: 'source-1', start: 0, end: 12, quote: 'Invoice 101' }] }}
      onSource={() => {}}
      onInterpret={async () => {}}
    />)

    expect(markup).toContain('Saved decision:</strong>')
    expect(markup).toContain('Suggested allocation ready for review')
    expect(markup).toContain('Chosen allocation:</strong>')
    expect(markup).toContain('Invoice 101')
    expect(markup).toContain('source-1')
    expect(markup).toContain('Next reviewer action:</strong>')
    expect(markup).toContain('Review the selected allocation')
  })

  it('labels hard-reload data as saved proposal state when model fields are absent', () => {
    const markup = renderToString(<InterpretationAction
      enabled={false}
      busy=""
      message=""
      hasSavedInterpretation
      proposalStatus="PROPOSED"
      proposalRevision={5}
      cash={[{ invoice_id: '101', amount: 10000 }]}
      evidence={[{ source_id: 'source-1', start: 0, end: 12, quote: 'Invoice 101' }]}
      proposalReason="bounded interpretation: evidence_supported"
      onInterpret={async () => {}}
    />)

    expect(markup).toContain('Saved proposal state')
    expect(markup).toContain('Saved allocation:')
    expect(markup).toContain('Saved proposal evidence')
    expect(markup).not.toContain('Relevant evidence')
    expect(markup).toContain('Saved proposal reason:')
    expect(markup).toContain('The cited evidence supports the selected allocation.')
  })

  it('shows the latest observed workflow stage while interpretation is running', () => {
    const markup = renderToString(<InterpretationAction
      enabled
      busy="interpret-hybrid"
      message=""
      progress={[
        { stage: 'load_observations', status: 'succeeded', summary: 'Loaded saved payment evidence.' },
        { stage: 'reserve_and_call', status: 'running', summary: 'Waiting for the provider response.' },
      ]}
      onInterpret={async () => {}}
    />)

    expect(markup).toContain('Waiting for the provider response.')
    expect(markup).toContain('Observed workflow steps')
  })

  it('keeps observed review steps available after the result arrives', () => {
    const markup = renderToString(<InterpretationAction
      enabled={false}
      busy=""
      message=""
      interpretation={{ status: 'selected', source: 'live', mode: 'direct' }}
      proposalStatus="PROPOSED"
      proposalRevision={3}
      progress={[{ stage: 'reserve_and_call', status: 'succeeded', summary: 'Provider response received.' }]}
      onInterpret={async () => {}}
    />)

    expect(markup).toContain('What happened during this review')
    expect(markup).toContain('Provider response received.')
    expect(markup).toContain('What the AI read')
  })

  it('holds the completed outcome until the saved proposal refresh finishes', () => {
    const markup = renderToString(<InterpretationAction
      enabled={false}
      busy="interpret-direct"
      message=""
      interpretationRefreshPending
      interpretation={{ status: 'selected', source: 'live', mode: 'direct', candidate_id: 'candidate-1' }}
      proposalStatus="NEEDS_REVIEW"
      proposalRevision={2}
      cash={[{ invoice_id: '101', amount: 10000 }]}
      onInterpret={async () => {}}
    />)

    expect(markup).toContain('Updating saved proposal')
    expect(markup).toContain('Refreshing the saved proposal result')
    expect(markup).not.toContain('Chosen allocation')
    expect(markup).not.toContain('candidate-1')
  })

  it('states why optional interpretation is disabled', () => {
    const markup = renderToString(<InterpretationAction
      enabled={false}
      disabledReason="Live interpretation is disabled for this session."
      busy=""
      message=""
      onInterpret={async () => {}}
    />)

    expect(markup).toContain('AI reading · GPT-6 Luna')
    expect(markup).toContain('Live interpretation is disabled for this session.')
  })

  it('sends an explicit direct interpretation request', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      proposal_id: 'proposal-1',
      revision: 2,
      status: 'NEEDS_REVIEW',
      interpretation: { status: 'selected', source: 'live', mode: 'direct', candidate_id: 'candidate-1' },
    }), { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)

    await expect(interpretProposal('proposal-1', 'direct')).resolves.toMatchObject({
      interpretation: { status: 'selected', source: 'live', mode: 'direct' },
    })

    expect(fetchMock).toHaveBeenCalledWith('/api/v1/proposals/proposal-1/interpret', expect.objectContaining({
      method: 'POST',
      body: JSON.stringify({ mode: 'direct' }),
    }))
  })

  it('keeps checking after a lifecycle consumer reports a running job', async () => {
    const run = vi.fn()
      .mockResolvedValueOnce({ job_id: 'job-1', status: 'RUNNING' })
      .mockResolvedValueOnce({ job_id: 'job-1', status: 'SUCCEEDED' })
    const onJob = vi.fn()
    const onRefresh = vi.fn().mockResolvedValue(undefined)

    await runJobsUntilSettled(run, onJob, onRefresh, 0)

    expect(run).toHaveBeenCalledTimes(2)
    expect(onJob).toHaveBeenLastCalledWith({ job_id: 'job-1', status: 'SUCCEEDED' })
    expect(onRefresh).toHaveBeenCalledOnce()
  })

  it('fails explicitly when a job stays transient through the polling limit', async () => {
    const run = vi.fn().mockResolvedValue({ job_id: 'job-1', status: 'RUNNING' })
    const onJob = vi.fn()
    const onRefresh = vi.fn().mockResolvedValue(undefined)

    await expect(runJobsUntilSettled(run, onJob, onRefresh, 0, 3)).rejects.toThrow(
      'Job processing timed out',
    )

    expect(run).toHaveBeenCalledTimes(3)
    expect(onRefresh).not.toHaveBeenCalled()
  })
})

describe('case study surfaces', () => {
  it('maps browser hashes to stable case, queue, and detail routes', () => {
    expect(parseAppRoute('#cases')).toEqual({ screen: 'cases' })
    expect(parseAppRoute('#queue')).toEqual({ screen: 'queue' })
    expect(parseAppRoute('#evaluation')).toEqual({ screen: 'evaluation' })
    expect(parseAppRoute('#proposal/proposal%201')).toEqual({ screen: 'detail', id: 'proposal 1' })
    expect(parseAppRoute('#proposal/%E0%A4%A')).toEqual({ screen: 'cases' })
  })

  it('keeps API calendar dates on the same calendar day', () => {
    expect(formatDateTime('2026-01-15')).toBe('2026-01-15')
  })

  it('leads with a library case, shows its reference, and keeps regression fixtures folded', () => {
    const markup = renderToString(<CasesView
      registry={{ version: 'v1', cases: [
        { id: 'spei-shorthand', title: 'Abbreviated bank reference', description: 'The reference abbreviates two invoices.', amount: 5400000, group: 'library', reference: 'PAGO FACT 1432 Y 33 MENOS NC-88' },
        { id: 'partial-installment', title: 'Partial payment', description: 'A first installment.', amount: 1600000, group: 'library', reference: 'ABONO 1 DE 3 FACT 2207' },
        { id: 'clean-reference', title: 'Clean reference', description: 'One exact invoice number.', amount: 845000, group: 'library', reference: 'FACTURA F-4410' },
        { id: 'straightforward', title: 'Straightforward', description: 'One match', amount: 10000, group: 'regression', reference: '—' },
        { id: 'bundle', title: 'Bundled', description: 'Several matches', amount: 20000, group: 'regression', reference: '—' },
      ] }}
      busy=""
      onOpen={async () => {}}
    />)

    expect(markup).toContain('Start here')
    expect(markup).toContain('Abbreviated bank reference')
    expect(markup).toContain('PAGO FACT 1432 Y 33 MENOS NC-88')
    expect(markup).toContain('Open this payment')
    expect(markup).toContain('How Reconcile decides')
    expect(markup).toContain('More payments to try')
    expect(markup.match(/class="case-card"/g)).toHaveLength(4)
    expect(markup).toContain('Regression fixtures (2)')
    expect(markup.indexOf('Regression fixtures')).toBeGreaterThan(markup.indexOf('Partial payment'))
  })

  it('never states an allocation on the case page before the server runs the case', () => {
    const markup = renderToString(<CasesView
      registry={{ version: 'v1', cases: [
        { id: 'spei-shorthand', title: 'Abbreviated bank reference', description: 'Inputs only.', amount: 5400000, group: 'library', reference: 'PAGO FACT 1432 Y 33 MENOS NC-88' },
      ] }}
      busy=""
      onOpen={async () => {}}
    />)

    expect(markup).not.toContain('MX$30,000')
    expect(markup).not.toContain('MX$24,000')
    expect(markup).not.toContain('Tempting guess')
    expect(markup).not.toContain('Message-supported split')
  })

  it('falls back to every server case when no library group is returned', () => {
    const markup = renderToString(<CasesView
      registry={{ version: 'v2', cases: [
        { id: 'bundle', title: 'Server bundle', description: 'A different registered example', amount: 1200000 },
        { id: 'other', title: 'Other case', description: 'Second example', amount: 300000 },
      ] }}
      busy=""
      onOpen={async () => {}}
    />)

    expect(markup).toContain('Server bundle')
    expect(markup).toContain('A different registered example')
    expect(markup).toContain('Other case')
    expect(markup).not.toContain('Regression fixtures')
  })

  it('explains a rules proposal in plain language and separates proposed effects from balances', () => {
    const detail = { proposal_id: 'proposal-1', revision: 3, reason: null, case: null, trace: { mode: 'rules-v2-conservative' } } as unknown as ProposalDetail
    const markup = renderToString(<DecisionSummary
      detail={detail}
      status="PROPOSED"
      cash={[{ invoice_id: '101', amount_mxn: '30000.00' }]}
      credits={[{ credit_note_id: '103', invoice_id: '102', amount_mxn: '1000.00' }]}
    />)

    expect(markup).toContain('The rules matched an exact invoice number')
    expect(markup).toContain('deterministic rules')
    expect(markup).toContain('Proposed cash')
    expect(markup).toContain('Proposed credit')
    expect(markup).toContain('Unchanged until approval')
  })

  it('explains an AI proposal as checked by code and still awaiting approval', () => {
    const detail = { reason: 'bounded interpretation: evidence_supported', trace: { mode: 'llm-direct' } } as unknown as ProposalDetail
    const markup = renderToString(<DecisionSummary detail={detail} status="PROPOSED" cash={[{ invoice_id: 'F-1432', amount_mxn: '30000.00' }]} credits={[]} />)

    expect(markup).toContain('GPT-6 Luna proposed this allocation')
    expect(markup).toContain('GPT-6 Luna, checked by code')
    expect(markup).toContain('Nothing is recorded until you approve')
  })

  it('explains unresolved work from the server reason code, not the case identifier', () => {
    const cases: Array<[string, string]> = [
      ['unrecognized source text', 'The rules can&#x27;t read this reference'],
      ['unrecognized_source_text', 'The rules can&#x27;t read this reference'],
      ['prompt-like instruction', 'Held: the note contains instructions for an AI system'],
      ['no explicit invoice reference', 'No invoice number was given'],
      ['bounded interpretation: ambiguous', 'The AI couldn&#x27;t tell which invoice was paid'],
    ]
    for (const [reason, title] of cases) {
      for (const caseInfo of [null, { id: 'insufficient', version: 'v1', variant: 'original' }]) {
        const detail = { reason, case: caseInfo, cash: [], credits: [], evidence: [] } as unknown as ProposalDetail
        const markup = renderToString(<DecisionSummary detail={detail} status="NEEDS_REVIEW" cash={[]} credits={[]} />)
        expect(markup).toContain(title)
        expect(markup).toContain('No balance has changed')
        expect(markup).not.toContain('Proposed cash')
      }
    }
  })

  it('keeps interpretation available for unresolved work and gives proposed work the reviewer lead', () => {
    expect(shouldShowInterpretation('PROPOSED', false)).toBe(false)
    expect(shouldShowInterpretation('PROPOSED', true)).toBe(true)
    expect(shouldShowInterpretation('NEEDS_REVIEW', false)).toBe(true)
  })

  it('shows server provenance, stage evidence, and unknown timing without fabricating values', () => {
    const trace: DecisionTrace = {
      source: 'unavailable',
      stages: [{ id: 'parse', name: 'Parse evidence', status: 'SUCCEEDED', summary: 'Parsed records', duration_ms: null, details: { candidates: ['101'] }, evidence: ['source-1'] }],
    }
    const markup = renderToString(<DecisionTracePanel trace={trace} onSource={() => {}} />)

    expect(markup).toContain('Unavailable')
    expect(markup).toContain('Not measured')
    expect(markup).toContain('Stage details')
    expect(markup).toContain('source-1')
    expect(markup).toContain('candidates')
  })

  it('accepts a comparison only for the detail revision and known input snapshot', () => {
    const detail = { proposal_id: 'proposal-1', revision: 3, trace: {}, model_trace: null, decision_trace: { input_fingerprint: 'f'.repeat(64), stages: [] } }
    const matching: Comparison = { revision: 3, input_fingerprint: 'f'.repeat(64), methods: [] }
    expect(comparisonMatchesDetail(detail, matching)).toBe(true)
    expect(comparisonMatchesDetail(detail, { ...matching, revision: 2 })).toBe(false)
    expect(comparisonMatchesDetail(detail, { ...matching, input_fingerprint: 'e'.repeat(64) })).toBe(false)
    const unavailableWithoutIdentity: Comparison = { ...matching, input_fingerprint: null, methods: [] }
    expect(comparisonMatchesDetail({ ...detail, decision_trace: undefined }, unavailableWithoutIdentity)).toBe(true)
    expect(comparisonMatchesDetail({ ...detail, decision_trace: undefined }, { ...unavailableWithoutIdentity, methods: [{ method: 'rules', status: 'proposed', source: 'rules', candidate: null, actionable: false, raw_score: null, duration_ms: null, usage: null, cost_usd: null, reason: null }] })).toBe(false)
  })

  it('renders exact source metadata in the readable source surface', () => {
    const source: SourceRecord = { source_id: 'source-1', kind: 'message', sha256: 'abc123', bytes: 42, version: 2, raw_text: 'raw source body', text: 'legacy source body', rows: [], issues: [], row_locators: [], metadata: { owner: 'case' } }
    const markup = renderToString(<SourceViewer sourceId="source-1" source={source} busy={false} error="" onClose={() => {}} />)

    expect(markup).toContain('raw source body')
    expect(markup).not.toContain('legacy source body')
    expect(markup).toContain('Version')
    expect(markup).toContain('2')
    expect(markup).toContain('abc123')
    expect(markup).toContain('Exact source metadata')
  })
})

describe('phase 1 money draft regressions', () => {
  it('converts a whole-MXN amount typed in the editor to centavos', () => {
    // An integer typed in the decimal-MXN editor is still a whole peso amount.
    expect(toCents('100')).toBe(10000)
  })

  it('converts the accepted decimal MXN examples exactly once', () => {
    expect(toCents('100.00')).toBe(10000)
    expect(toCents('54000')).toBe(5400000)
    expect(toCents('54000.00')).toBe(5400000)
    expect(toCents('0.01')).toBe(1)
  })

  it('round-trips persisted centavos into a visible MXN draft without changing the payload unit', () => {
    const draft = { invoice_id: '101', amount_mxn: centsToMxn(10000) }
    expect(draft.amount_mxn).toBe('100.00')
    expect(cashDraftToPayload(draft)).toEqual({ invoice_id: '101', amount: 10000 })
  })

  it('rejects malformed money only at submission validation', () => {
    for (const value of ['', '-1', '1e2', '1,000', '1.234', 'Infinity']) {
      expect(() => mxnToCents(value)).toThrow()
    }
    expect(mxnToCents('9999999999.99')).toBe(999999999999)
    expect(() => mxnToCents('10000000000')).toThrow()
    expect(reviewDraftError([{ invoice_id: '101', amount_mxn: '100.' }], [])).toMatch(/Cash line 1/)
  })

  it('marks edited drafts dirty and blocks their application state', () => {
    const persisted = [{ invoice_id: '101', amount_mxn: '100.00' }]
    const edited = [{ invoice_id: '101', amount_mxn: '100' }]
    expect(reviewDraftsEqual(edited, [], persisted, [])).toBe(false)
    expect(reviewDraftError(edited, [])).toBeUndefined()
  })

  it('changes the logical apply attempt when revision or payload identity changes', () => {
    expect(applyAttemptFingerprint('p', 1, 'a'.repeat(64), 'R')).toBe(applyAttemptFingerprint('p', 1, 'a'.repeat(64), 'R'))
    expect(applyAttemptFingerprint('p', 1, 'a'.repeat(64), 'R')).not.toBe(applyAttemptFingerprint('p', 2, 'b'.repeat(64), 'R'))
  })

  it('projects cash and credit deductions from the server balance before confirmation', () => {
    expect(projectedBalanceRows(
      [{ invoice_id: '101', opening_amount: 30000, cash_applied: 0, credit_applied: 0, remaining_amount: 30000 }],
      [{ invoice_id: '101', amount_mxn: '100.00' }],
      [{ credit_note_id: '103', invoice_id: '101', amount_mxn: '50.00' }],
    )).toEqual([{ invoice_id: '101', opening_amount: 30000, cash_applied: 0, credit_applied: 0, remaining_amount: 30000, projected_remaining_amount: 15000 }])
  })

  it('renders an accessible confirmation with persisted financial values and bank-transfer boundary', () => {
    const markup = renderToString(<ApplyConfirmation
      detail={{ revision: 3 }}
      cash={[{ invoice_id: '101', amount_mxn: '100.00' }]}
      credits={[{ credit_note_id: '103', invoice_id: '101', amount_mxn: '50.00' }]}
      balances={[{ invoice_id: '101', opening_amount: 30000, cash_applied: 0, credit_applied: 0, remaining_amount: 30000 }]}
      busy=""
      onCancel={() => {}}
      onConfirm={() => {}}
    />)

    expect(markup).toContain('role="dialog"')
    expect(markup).toContain('aria-describedby="apply-confirmation-description"')
    expect(markup).toContain('Review revision <strong>3</strong>')
    expect(markup).toContain('Cash')
    expect(markup).toContain('Credit')
    expect(markup).toContain('Projected invoice balances')
    expect(markup).toContain('records an allocation')
    expect(markup).toContain('does not move money in a bank account')
  })
})
