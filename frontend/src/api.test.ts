import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError, applyProposal, createSession } from './api'

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
})
