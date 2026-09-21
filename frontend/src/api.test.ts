import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError, applyCaseVariant, applyProposal, compareProposal, createSession, getEvaluation, getSource, runReliabilityCheck } from './api'

afterEach(() => vi.restoreAllMocks())

describe('API transport boundary', () => {
  it('establishes the session and sends its CSRF token on mutations', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ mode: 'local', csrf_token: 'csrf-test' }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ status: 'APPLIED' }), { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)

    await createSession()
    await applyProposal('proposal-1', { expected_revision: 1 })

    expect(fetchMock).toHaveBeenNthCalledWith(1, '/api/v1/session', expect.objectContaining({ credentials: 'include', body: '{}' }))
    const requestInit = fetchMock.mock.calls[1][1] as RequestInit
    const headers = new Headers(requestInit.headers)
    expect(requestInit.credentials).toBe('include')
    expect(headers.get('Content-Type')).toBe('application/json')
    expect(headers.get('X-CSRF-Token')).toBe('csrf-test')
  })

  it('preserves structured server errors', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ error: { code: 'STALE', message: 'Proposal is stale', fields: ['revision'] } }), { status: 409 })))
    await expect(applyProposal('proposal-1', {})).rejects.toMatchObject({ status: 409, code: 'STALE', message: 'Proposal is stale' } satisfies Partial<ApiError>)
  })

  it('requests source records from the API path without reapplying the base URL', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ source_id: 'source-1' }), { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)

    await getSource('source-1')

    expect(fetchMock).toHaveBeenCalledWith('/api/v1/sources/source-1', expect.objectContaining({ credentials: 'include' }))
  })

  it('compares a proposal at the requested revision and keeps the request review-only', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      revision: 7,
      input_fingerprint: 'a'.repeat(64),
      methods: [],
    }), { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)

    await expect(compareProposal('proposal/1', 7)).resolves.toMatchObject({ revision: 7 })

    expect(fetchMock).toHaveBeenCalledWith('/api/v1/proposals/proposal%2F1/compare', expect.objectContaining({
      method: 'POST',
      body: JSON.stringify({ expected_revision: 7 }),
      credentials: 'include',
    }))
  })

  it('loads the server packaged evaluation summary without a client side metric source', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      schema_version: 'evaluation-summary-v1',
      active_engine: 'rules-v2-conservative',
      provenance: { kind: 'historical_aggregate', report_path: 'reports/release-v1/evaluation.json', report_sha256: 'a'.repeat(64), evaluated_at: '2026-09-14T00:00:00Z', release_commit: 'b'.repeat(40) },
      historical: [],
      v2: { status: 'not_evaluated', provider_calls_this_continuation: 0, final_access_this_continuation: false, independent_domain_review: 'pending' },
      limitations: [],
    }), { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)

    await expect(getEvaluation()).resolves.toMatchObject({ active_engine: 'rules-v2-conservative' })
    expect(fetchMock).toHaveBeenCalledWith('/api/v1/evaluation', expect.objectContaining({ credentials: 'include' }))
  })

  it('changes a registered case variant with its expected revision', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      case_id: 'bundle', scenario_version: 'v1-ambiguous', variant: 'ambiguous', payment_id: 'payment-1', proposal_id: 'proposal-1', jobs: [], resumed: true,
    }), { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)

    await expect(applyCaseVariant('bundle', 3, 'ambiguous')).resolves.toMatchObject({ variant: 'ambiguous' })
    expect(fetchMock).toHaveBeenCalledWith('/api/v1/cases/bundle/variant', expect.objectContaining({
      method: 'POST',
      body: JSON.stringify({ expected_revision: 3, variant: 'ambiguous' }),
    }))
  })

  it('requests a named reliability experiment at the persisted revision', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      experiment: 'invalid_citation', synthetic: true, validator: 'citation-validator-v1', expected: {}, observed: {}, passed: false, application_id: null,
      effects_before: { application_groups: 0, cash_applications: 0, credit_applications: 0, cash_centavos: 0, credit_centavos: 0 },
      effects_after: { application_groups: 0, cash_applications: 0, credit_applications: 0, cash_centavos: 0, credit_centavos: 0 },
    }), { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)

    await expect(runReliabilityCheck('proposal-1', 2, 'invalid_citation')).resolves.toMatchObject({ experiment: 'invalid_citation' })
    expect(fetchMock).toHaveBeenCalledWith('/api/v1/proposals/proposal-1/reliability', expect.objectContaining({
      method: 'POST',
      body: JSON.stringify({ expected_revision: 2, experiment: 'invalid_citation' }),
    }))
  })
})
