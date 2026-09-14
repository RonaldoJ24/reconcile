import type {
  ImportSummary,
  ImportValidation,
  JobState,
  ProposalDetail,
  ProposalSummary,
  Session,
} from './types'

const base = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/$/, '')
let csrfToken = ''

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

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers)
  if (!(init.body instanceof FormData) && init.body !== undefined) {
    headers.set('Content-Type', 'application/json')
  }
  const method = (init.method ?? 'GET').toUpperCase()
  if (method !== 'GET' && csrfToken) headers.set('X-CSRF-Token', csrfToken)
  const response = await fetch(endpoint(path), { ...init, headers, credentials: 'include' })
  const body = await readBody(response)
  if (!response.ok) {
    const error = body && typeof body === 'object' && 'error' in body
      ? (body as { error?: { code?: string; message?: string; fields?: unknown[] } }).error
      : undefined
    const message = error?.message ?? (typeof body === 'string' ? body : response.statusText || 'Request failed')
    throw new ApiError(response.status, message, error?.code, error?.fields)
  }
  return body as T
}

export async function createSession(): Promise<Session> {
  const session = await request<Session>('/api/v1/session', { method: 'POST', body: '{}' })
  csrfToken = session.csrf_token ?? session.csrfToken ?? ''
  return session
}

export function getCsrfToken() {
  return csrfToken
}

export function listImports() {
  return request<ImportSummary[]>('/api/v1/imports')
}

export function listProposals() {
  return request<ProposalSummary[]>('/api/v1/proposals')
}

export function getProposal(id: string) {
  return request<ProposalDetail>(`/api/v1/proposals/${encodeURIComponent(id)}`)
}

export function validateImport(form: FormData) {
  return request<ImportValidation>('/api/v1/imports/validate', { method: 'POST', body: form })
}

export function commitImport(batchId: string) {
  return request<ImportValidation>(`/api/v1/imports/${encodeURIComponent(batchId)}/commit`, {
    method: 'POST',
    body: '{}',
  })
}

export function runJobOnce() {
  return request<JobState>('/api/v1/jobs/run-once', { method: 'POST', body: '{}' })
}

export function correctProposal(id: string, payload: unknown) {
  return request<ProposalDetail>(`/api/v1/proposals/${encodeURIComponent(id)}/correct`, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function applyProposal(id: string, payload: unknown) {
  return request<ProposalDetail>(`/api/v1/proposals/${encodeURIComponent(id)}/apply`, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function reverseApplication(id: string, payload: unknown) {
  return request<ProposalDetail>(`/api/v1/applications/${encodeURIComponent(id)}/reverse`, {
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
  return endpoint(`/api/v1/sources/${encodeURIComponent(id)}`)
}
