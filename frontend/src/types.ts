export type Mode = 'rules-v1' | string

export type InterpretationStatus = 'selected' | 'needs_review' | 'unavailable'
export type InterpretationSource = 'live' | 'cache' | 'none'
export type InterpretationMode = 'direct' | 'hybrid'

export type InterpretationCitation = {
  source_id?: string
  start?: number
  end?: number
  quote?: string
}

export type Interpretation = {
  status: InterpretationStatus
  source: InterpretationSource
  mode: InterpretationMode
  candidate_id?: string
  reason_code?: string
  citations?: InterpretationCitation[]
  failure_code?: string
  trace?: unknown
}

export type InterpretationResponse = {
  proposal_id: string
  revision: number
  status: string
  interpretation: Interpretation
}

export type Capabilities = {
  interpret: boolean
  correct: boolean
  apply: boolean
  reverse: boolean
}

export type Session = {
  mode: Mode
  expires_at: string
  csrf_token: string
  provider_access: boolean
  active_engine?: string
  capabilities?: Capabilities
}

export type CaseSummary = {
  id: string
  title: string
  description: string
  amount: number
}

export type CaseRegistry = {
  version: string
  cases: CaseSummary[]
}

export type CaseOpen = {
  case_id: string
  scenario_version: string
  payment_id: string
  proposal_id: string | null
  jobs: string[]
  resumed: boolean
}

export type RowIssue = {
  row?: number
  record?: number
  field?: string
  code?: string
  message: string
}

export type ImportSourceReport = {
  kind: string
  source_id: string
  accepted: number
  rejected: number
  issues: RowIssue[]
}

export type ImportValidation = {
  batch_id: string
  sources: ImportSourceReport[]
  accepted: number
  rejected: number
}

export type ImportCommit = {
  batch_id: string
  payments: number
  invoices: number
  credits: number
  conflicts: Array<Record<string, unknown>>
  jobs: string[]
}

export type ImportSummary = {
  batch_id: string
  status: string
  created_at: string
}

export type ProposalSummary = {
  proposal_id: string
  status: string
  revision: number
  payment_id: string
  amount: number
  payer_name: string
  source_account_id: string
  transaction_id: string
  booking_date: string
  application_id: string | null
}

export type CashLine = {
  invoice_id: string
  amount: number
}

export type CreditLine = {
  credit_note_id: string
  invoice_id: string
  amount: number
}

export type Payment = {
  id: string
  amount: number
  reference: string
  payer_name: string
  source_account_id: string
  transaction_id: string
  booking_date: string
}

export type InvoiceBalance = {
  opening_amount: number
  cash_applied: number
  credit_applied: number
  remaining_amount: number
}

export type Evidence = {
  source_id: string
  start: number
  end: number
  quote: string
}

export type SourceRecord = {
  source_id: string
  kind: string
  sha256: string
  bytes: number
  version?: number | string
  raw_text?: string | null
  text: string | null
  rows: Record<string, unknown>[]
  issues: RowIssue[]
  row_locators: Record<string, unknown>[]
  metadata: Record<string, unknown>
}

export type DecisionTraceStage = {
  id: string
  name: string
  status: string
  summary: string
  duration_ms: number | null
  details?: unknown
  evidence?: string[]
}

export type DecisionTrace = {
  schema_version?: string
  input_fingerprint?: string
  source?: 'live' | 'cache' | 'recorded' | 'unavailable' | 'rules' | 'local'
  stages: DecisionTraceStage[]
}

export type ComparisonStatus = 'proposed' | 'deferred' | 'unavailable' | 'failed'
export type ComparisonSource = 'live' | 'cache' | 'recorded' | 'unavailable' | 'rules' | 'local'

export type ComparisonUsage = {
  input_tokens: number | null
  output_tokens: number | null
  provider_cache_tokens: number | null
  reasoning_tokens: number | null
}

export type ComparisonAllocation = {
  cash: CashLine[]
  credits: CreditLine[]
}

export type ComparisonMethod = {
  method: string
  status: ComparisonStatus
  source: ComparisonSource
  candidate: ComparisonAllocation | null
  actionable: false
  raw_score: number | null
  duration_ms: number | null
  usage: ComparisonUsage | null
  cost_usd: number | null
  reason: string | null
}

export type Comparison = {
  input_fingerprint: string | null
  revision: number
  methods: ComparisonMethod[]
}

export type EvaluationHistoricalRow = {
  method: string
  split: string
  groups: number
  proposals: number
  correct_proposals_per_v1_labels: number
  incorrect_proposals_per_v1_labels: number
  precision: number
  coverage: number
  underdetermined_groups: number
  abstained_underdetermined: number
}

export type EvaluationProvenance = {
  kind: string
  report_path: string
  report_sha256: string
  evaluated_at: string
  release_commit: string
}

export type EvaluationV2 = {
  status: string
  provider_calls_this_continuation: number
  final_access_this_continuation: boolean
  independent_domain_review: string
}

export type EvaluationSummary = {
  schema_version: string
  provenance: EvaluationProvenance
  historical: EvaluationHistoricalRow[]
  v2: EvaluationV2
  limitations: string[]
}

export type EvaluationResponse = EvaluationSummary & {
  active_engine: string
}

export type ProposalDetail = {
  proposal_id: string
  status: string
  revision: number
  payment: Payment
  cash: CashLine[]
  credits: CreditLine[]
  evidence: Evidence[]
  alternatives: string[][]
  signals: string[]
  reason: string | null
  version_token: string | null
  balances: Record<string, InvoiceBalance>
  unapplied_cash: number
  application_id: string | null
  trace: Record<string, unknown>
  interpretation: Interpretation | null
  review_required: boolean
  capabilities?: Partial<Capabilities>
  active_engine?: string
  case?: { id: string; version: string } | null
  decision_trace?: DecisionTrace | null
  comparison?: Comparison | null
  model_trace?: Record<string, unknown> | null
}

export type CorrectResponse = {
  proposal_id: string
  revision: number
  status: string
}

export type ApplyResponse = {
  application_id: string
  proposal_id: string
  revision: number
  reversed: boolean
}

export type ReverseResponse = {
  application_id: string
  reversed: boolean
}

export type JobState = {
  job_id: string | null
  status: string
}
