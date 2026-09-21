import { renderToString } from 'react-dom/server'
import { afterEach, describe, expect, it, vi } from 'vitest'
import App, { ApplyConfirmation, InterpretationAction, applyAttemptFingerprint, cashDraftToPayload, projectedBalanceRows, reviewDraftError, reviewDraftsEqual, runJobsUntilSettled, toCents } from './App'
import { centsToMxn, mxnToCents } from './money'
import { interpretProposal } from './api'

afterEach(() => vi.restoreAllMocks())

describe('Phase 4 interpretation UI', () => {
  it('does not request interpretation while the app renders', () => {
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)

    renderToString(<App />)

    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('exposes separate direct and hybrid actions', () => {
    const markup = renderToString(<InterpretationAction enabled busy="" message="" onInterpret={async () => {}} />)

    expect(markup).toContain('>Direct<')
    expect(markup).toContain('>Hybrid<')
    expect(markup).toContain('never applies money')
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
    expect(markup).toContain('Financial application still requires a reviewer.')
    expect(markup).toMatch(/button[^>]+disabled/)
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
      [{ invoice_id: '101', remaining_amount: 30000 }],
      [{ invoice_id: '101', amount_mxn: '100.00' }],
      [{ credit_note_id: '103', invoice_id: '101', amount_mxn: '50.00' }],
    )).toEqual([{ invoice_id: '101', remaining_amount: 30000, projected_remaining_amount: 15000 }])
  })

  it('renders an accessible confirmation with persisted financial values and bank-transfer boundary', () => {
    const markup = renderToString(<ApplyConfirmation
      detail={{ revision: 3 }}
      cash={[{ invoice_id: '101', amount_mxn: '100.00' }]}
      credits={[{ credit_note_id: '103', invoice_id: '101', amount_mxn: '50.00' }]}
      balances={[{ invoice_id: '101', remaining_amount: 30000 }]}
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
