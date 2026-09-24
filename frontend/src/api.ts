import type {
  ImportSummary,
  ImportValidation,
  ImportCommit,
  InterpretationMode,
  InterpretationResponse,
  InterpretationProgress,
  JobState,
  CorrectResponse,
  ApplyResponse,
  ReverseResponse,
  CaseOpen,
  CaseRegistry,
  CaseVariant,
  Comparison,
  EvaluationResponse,
  ReliabilityExperiment,
  ReliabilityResult,
  ProposalDetail,
  ProposalSummary,
  Session,
  SourceRecord,
} from './types'

const base = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/$/, '')
let csrfToken = ''
let sessionRefresh: Promise<Session> | undefined

export class ApiError extends Error {
  status: number
  code?: string
  fields?: unknown[]

  constructor(status: number, message: string, code?: string, fields?: unknown[]) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
    this.fields = fields
  }
}

function endpoint(path: string) {
  return `${base}${path}`
}

async function readBody(response: Response): Promise<unknown> {
  const text = await response.text()
  if (!text) return undefined
  try {
    return JSON.parse(text) as unknown
  } catch {
    return text
  }
}

function responseError(response: Response, body: unknown): ApiError {
  const error = body && typeof body === 'object' && 'error' in body
    ? (body as { error?: { code?: string; message?: string; fields?: unknown[] } }).error
    : undefined
  const message = error?.message ?? (typeof body === 'string' ? body : response.statusText || 'Request failed')
  return new ApiError(response.status, message, error?.code, error?.fields)
}

function isCsrfError(error: ApiError) {
  return error.status === 403 && error.code === 'csrf_required'
}

async function request<T>(path: string, init: RequestInit = {}, retryCsrf = true): Promise<T> {
  const headers = new Headers(init.headers)
  if (!(init.body instanceof FormData) && init.body !== undefined) {
    headers.set('Content-Type', 'application/json')
  }
  const method = (init.method ?? 'GET').toUpperCase()
  if (method !== 'GET' && csrfToken) headers.set('X-CSRF-Token', csrfToken)
  const response = await fetch(endpoint(path), { ...init, headers, credentials: 'include' })
  const body = await readBody(response)
  if (!response.ok) {
    const error = responseError(response, body)
    if (retryCsrf && method !== 'GET' && isCsrfError(error)) {
      await refreshSessionOnce()
      return request<T>(path, init, false)
    }
    throw error
  }
  return body as T
}

export async function createSession(): Promise<Session> {
  const session = await request<Session>('/api/v1/session', { method: 'POST', body: '{}' }, false)
  csrfToken = session.csrf_token
  return session
}

// Deletes this synthetic preview session's cases and starts a fresh one.
export async function resetSession(): Promise<Session> {
  const session = await request<Session>('/api/v1/session/reset', { method: 'POST', body: '{}' })
  csrfToken = session.csrf_token
  return session
}

function refreshSessionOnce() {
  if (!sessionRefresh) {
    sessionRefresh = createSession().finally(() => { sessionRefresh = undefined })
  }
  return sessionRefresh
}

export function getCsrfToken() {
  return csrfToken
}

export function listImports() {
  return request<ImportSummary[]>('/api/v1/imports')
}

export function listCases() {
  return request<CaseRegistry>('/api/v1/cases')
}

export function openCase(caseId: string) {
  return request<CaseOpen>(`/api/v1/cases/${encodeURIComponent(caseId)}/open`, {
    method: 'POST',
    body: '{}',
  })
}

export function applyCaseVariant(caseId: string, expectedRevision: number, variant: CaseVariant) {
  return request<CaseOpen>(`/api/v1/cases/${encodeURIComponent(caseId)}/variant`, {
    method: 'POST',
    body: JSON.stringify({ expected_revision: expectedRevision, variant }),
  })
}

export function listProposals() {
  return request<ProposalSummary[]>('/api/v1/proposals')
}

export function getProposal(id: string) {
  return request<ProposalDetail>(`/api/v1/proposals/${encodeURIComponent(id)}`)
}

export function compareProposal(id: string, expectedRevision: number) {
  return request<Comparison>(`/api/v1/proposals/${encodeURIComponent(id)}/compare`, {
    method: 'POST',
    body: JSON.stringify({ expected_revision: expectedRevision }),
  })
}

export function getEvaluation() {
  return request<EvaluationResponse>('/api/v1/evaluation')
}

export function runReliabilityCheck(id: string, expectedRevision: number, experiment: ReliabilityExperiment) {
  return request<ReliabilityResult>(`/api/v1/proposals/${encodeURIComponent(id)}/reliability`, {
    method: 'POST',
    body: JSON.stringify({ expected_revision: expectedRevision, experiment }),
  })
}

export function getSource(id: string) {
  return request<SourceRecord>(sourceUrl(id))
}

export function interpretProposal(id: string, mode: InterpretationMode) {
  return request<InterpretationResponse>(`/api/v1/proposals/${encodeURIComponent(id)}/interpret`, {
    method: 'POST',
    body: JSON.stringify({ mode }),
  })
}

export async function streamInterpretProposal(
  id: string,
  mode: InterpretationMode,
  onProgress: (progress: InterpretationProgress) => void,
  signal?: AbortSignal,
  retryCsrf = true,
): Promise<InterpretationResponse> {
  const path = `/api/v1/proposals/${encodeURIComponent(id)}/interpret/stream`
  const body = JSON.stringify({ mode })
  const headers = new Headers({ 'Content-Type': 'application/json', Accept: 'text/event-stream' })
  if (csrfToken) headers.set('X-CSRF-Token', csrfToken)
  const response = await fetch(endpoint(path), {
    method: 'POST',
    body,
    signal,
    headers,
    credentials: 'include',
  })
  if (!response.ok) {
    const error = responseError(response, await readBody(response))
    if (retryCsrf && isCsrfError(error)) {
      await refreshSessionOnce()
      return streamInterpretProposal(id, mode, onProgress, signal, false)
    }
    throw error
  }
  if (!response.body) throw new ApiError(502, 'Interpretation stream returned no body.', 'stream_empty')

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let completed: InterpretationResponse | undefined
  const consume = (block: string) => {
    let event = 'message'
    const data: string[] = []
    for (const line of block.split('\n')) {
      if (line.startsWith('event:')) event = line.slice(6).trim()
      else if (line.startsWith('data:')) data.push(line.slice(5).trimStart())
    }
    if (!data.length) return
    let payload: unknown
    try {
      payload = JSON.parse(data.join('\n')) as unknown
    } catch {
      throw new ApiError(502, 'Interpretation stream contained invalid data.', 'stream_invalid')
    }
    if (event === 'progress') {
      if (payload && typeof payload === 'object' && 'stage' in payload) {
        const stage = (payload as { stage?: InterpretationProgress }).stage
        if (stage && typeof stage === 'object') onProgress(stage)
      }
    } else if (event === 'error') {
      const error = payload && typeof payload === 'object' && 'error' in payload
        ? (payload as { error?: { code?: string; message?: string; fields?: unknown[] } }).error
        : undefined
      throw new ApiError(502, error?.message ?? 'Interpretation stream failed.', error?.code, error?.fields)
    } else if (event === 'complete') {
      completed = payload as InterpretationResponse
    }
  }
  try {
    while (!completed) {
      const next = await reader.read()
      buffer += decoder.decode(next.value, { stream: !next.done })
      let boundary = buffer.indexOf('\n\n')
      while (boundary >= 0) {
        consume(buffer.slice(0, boundary))
        buffer = buffer.slice(boundary + 2)
        boundary = buffer.indexOf('\n\n')
      }
      if (next.done) break
    }
    if (buffer.trim()) consume(buffer)
    if (!completed) throw new ApiError(502, 'Interpretation stream ended before a result.', 'stream_incomplete')
    return completed
  } finally {
    try {
      await reader.cancel()
    } catch {
      // The stream may already be closed or aborted.
    }
    reader.releaseLock()
  }
}

export function validateImport(form: FormData) {
  return request<ImportValidation>('/api/v1/imports/validate', { method: 'POST', body: form })
}

export function commitImport(batchId: string) {
  return request<ImportCommit>(`/api/v1/imports/${encodeURIComponent(batchId)}/commit`, {
    method: 'POST',
    body: '{}',
  })
}

export function runJobOnce() {
  return request<JobState>('/api/v1/jobs/run-once', { method: 'POST', body: '{}' })
}

export function correctProposal(id: string, payload: unknown) {
  return request<CorrectResponse>(`/api/v1/proposals/${encodeURIComponent(id)}/correct`, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function applyProposal(id: string, payload: unknown) {
  return request<ApplyResponse>(`/api/v1/proposals/${encodeURIComponent(id)}/apply`, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function reverseApplication(id: string, payload: unknown) {
  return request<ReverseResponse>(`/api/v1/applications/${encodeURIComponent(id)}/reverse`, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function sampleUrl() {
  return endpoint('/api/v1/sample')
}

export function exportUrl() {
  return endpoint('/api/v1/exports/applications.csv')
}

export function sourceUrl(id: string) {
  return `/api/v1/sources/${encodeURIComponent(id)}`
}
