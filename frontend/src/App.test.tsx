import { renderToString } from 'react-dom/server'
import { afterEach, describe, expect, it, vi } from 'vitest'
import App, { InterpretationAction, runJobsUntilSettled } from './App'
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
})
