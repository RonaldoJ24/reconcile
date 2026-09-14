export type Mode = 'rules-v1' | string

export type Session = {
  mode: Mode
  expires_at?: string
  csrf_token?: string
  csrfToken?: string
}

export type RowIssue = {
  row?: number
  record?: number
  field?: string
  code?: string
  message: string
}

export type ImportValidation = {
  batch_id?: string
  batchId?: string
  row_issues?: RowIssue[]
  rowIssues?: RowIssue[]
  accepted_counts?: Record<string, number>
  acceptedCounts?: Record<string, number>
  rejected_counts?: Record<string, number>
  rejectedCounts?: Record<string, number>
  accepted?: number
  rejected?: number
  sources?: Array<{
    kind?: string
    source_id?: string
    accepted?: number
    rejected?: number
    issues?: RowIssue[]
  }>
  status?: string
}

export type ImportSummary = {
  id?: string
  batch_id?: string
  batchId?: string
  status?: string
  created_at?: string
  accepted_counts?: Record<string, number>
  rejected_counts?: Record<string, number>
  [key: string]: unknown
}

export type ProposalSummary = {
  id?: string
  proposal_id?: string
  proposalId?: string
  payment_id?: string
  status?: string
  payer_name?: string
  payerName?: string
  amount_cents?: number | string
  amount?: number | string
  currency?: string
  revision?: number
  [key: string]: unknown
}

export type CashLine = {
  invoice_id?: string
  invoiceId?: string
  amount_cents?: number | string
  amountCents?: number | string
  amount?: number | string
  customer_name?: string
  customerName?: string
  [key: string]: unknown
}

export type CreditLine = {
  credit_note_id?: string
  creditNoteId?: string
  invoice_id?: string
  invoiceId?: string
  amount_cents?: number | string
  amountCents?: number | string
  amount?: number | string
  [key: string]: unknown
}

export type Evidence = {
  source_id?: string
  sourceId?: string
  kind?: string
  start?: number
  end?: number
  start_offset?: number
  end_offset?: number
  record?: number
  excerpt?: string
  text?: string
  quote?: string
  [key: string]: unknown
}

export type ProposalDetail = ProposalSummary & {
  payment?: Record<string, unknown>
  cash?: CashLine[]
  credits?: CreditLine[]
  cash_lines?: CashLine[]
  cashLines?: CashLine[]
  credit_lines?: CreditLine[]
  creditLines?: CreditLine[]
  unapplied_amount_cents?: number | string
  unappliedAmountCents?: number | string
  unapplied_amount?: number | string
  balances?: Record<string, unknown> | Record<string, unknown>[]
  evidence?: Evidence[]
  alternatives?: (Record<string, unknown> | unknown[])[]
  trace?: unknown
  version_token?: string
  versionToken?: string
  application_id?: string
  applicationId?: string
  application?: Record<string, unknown>
  revision?: number
  reviewer?: string
  [key: string]: unknown
}

export type JobState = {
  state?: string
  status?: string
  job_id?: string
  jobId?: string
  [key: string]: unknown
}
