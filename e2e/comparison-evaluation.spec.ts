import { expect, test } from '@playwright/test'
import path from 'node:path'

const proposalId = 'proposal-comparison'
const token = 'b'.repeat(64)
const sourceId = 'source-comparison'
const cases = [{ id: 'bundle', title: 'Bundled payment', description: 'Several plausible invoice matches.', amount: 5400000 }]

const detail = {
  proposal_id: proposalId,
  status: 'PROPOSED',
  revision: 1,
  reason: 'Review the persisted candidate before approval.',
  payment: { id: 'payment-comparison', amount: 5400000, reference: 'Bundle payment', payer_name: 'Case payer', source_account_id: 'acct-case', transaction_id: 'txn-comparison', booking_date: '2026-01-15' },
  cash: [{ invoice_id: '101', amount: 10000 }],
  credits: [{ credit_note_id: '103', invoice_id: '102', amount: 100000 }],
  balances: {
    '101': { opening_amount: 3000000, cash_applied: 0, credit_applied: 0, remaining_amount: 3000000 },
    '102': { opening_amount: 2500000, cash_applied: 0, credit_applied: 100000, remaining_amount: 2400000 },
  },
  unapplied_cash: 5290000,
  version_token: token,
  application_id: null,
  case: { id: 'bundle', version: 'v1' },
  alternatives: [],
  signals: [],
  trace: {},
  interpretation: null,
  review_required: true,
  capabilities: { interpret: false, correct: true, apply: true, reverse: false },
  evidence: [{ source_id: sourceId, start: 0, end: 18, quote: 'invoice 101 and 102' }],
  decision_trace: { schema_version: 'v1', source: 'local', input_fingerprint: 'd'.repeat(64), stages: [{ id: 'parse', name: 'Parse evidence', status: 'SUCCEEDED', summary: 'Read the registered payment message.', duration_ms: null, details: { candidates: ['101', '102'] }, evidence: [sourceId] }] },
  model_trace: { preserved: true },
  comparison: null,
}

const comparison = {
  revision: 1,
  input_fingerprint: 'd'.repeat(64),
  methods: [
    { method: 'rules', status: 'proposed', source: 'rules', candidate: { cash: [{ invoice_id: '101', amount: 10000 }], credits: [{ credit_note_id: '103', invoice_id: '102', amount: 100000 }] }, actionable: false, raw_score: null, duration_ms: null, usage: null, cost_usd: null, reason: null },
    { method: 'bounded_correction', status: 'deferred', source: 'local', candidate: null, actionable: false, raw_score: null, duration_ms: null, usage: null, cost_usd: null, reason: 'Underdetermined candidate set.' },
    { method: 'shadow_ranker', status: 'proposed', source: 'local', candidate: { cash: [{ invoice_id: '101', amount: 10000 }], credits: [{ credit_note_id: '103', invoice_id: '102', amount: 100000 }] }, actionable: false, raw_score: 0.75, duration_ms: null, usage: null, cost_usd: null, reason: null },
    { method: 'direct', status: 'unavailable', source: 'unavailable', candidate: null, actionable: false, raw_score: null, duration_ms: null, usage: null, cost_usd: null, reason: 'No authenticated exact-input observation is available.' },
    { method: 'hybrid', status: 'unavailable', source: 'unavailable', candidate: null, actionable: false, raw_score: null, duration_ms: null, usage: null, cost_usd: null, reason: 'No authenticated exact-input observation is available.' },
  ],
}

const evaluation = {
  schema_version: 'evaluation-summary-v1',
  active_engine: 'rules-v2-conservative',
  provenance: { kind: 'historical_aggregate', report_path: 'reports/release-v1/evaluation.json', report_sha256: 'a'.repeat(64), evaluated_at: '2026-09-14T23:12:08.754954+00:00', release_commit: 'b'.repeat(40) },
  historical: [{ method: 'rules-v1', split: 'final', groups: 500, proposals: 300, correct_proposals_per_v1_labels: 250, incorrect_proposals_per_v1_labels: 50, precision: 0.8333333333, coverage: 0.6, underdetermined_groups: 250, abstained_underdetermined: 200 }],
  v2: { status: 'not_evaluated', provider_calls_this_continuation: 0, final_access_this_continuation: false, independent_domain_review: 'pending' },
  limitations: ['Synthetic agent-generated labels; no independent domain validation or real-world accuracy claim.'],
}

const qualityDemoPath = (name: string) => path.join(__dirname, '..', 'output', 'quality-demo', name)

async function mockApi(page: import('@playwright/test').Page, options: { delayCompare?: boolean; failEvaluationOnce?: boolean; delaySessionMs?: number } = {}) {
  let opened = false
  let compareRequests = 0
  let evaluationFailed = false
  let sessionReady = false
  let earlyEvaluationRequests = 0
  await page.route('**/api/v1/**', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    const method = request.method()
    if (url.pathname === '/api/v1/session' && method === 'POST') {
      if (options.delaySessionMs) await new Promise((resolve) => setTimeout(resolve, options.delaySessionMs))
      sessionReady = true
      await route.fulfill({ json: { mode: 'local', csrf_token: 'mock-csrf', provider_access: false, active_engine: 'rules-v2-conservative', capabilities: { interpret: false, correct: true, apply: true, reverse: false } } })
      return
    }
    if (url.pathname === '/api/v1/cases' && method === 'GET') {
      await route.fulfill({ json: { version: 'case-fixtures-v1', cases } })
      return
    }
    if (url.pathname === '/api/v1/imports' && method === 'GET') {
      await route.fulfill({ json: [] })
      return
    }
    if (url.pathname === '/api/v1/proposals' && method === 'GET') {
      await route.fulfill({ json: opened ? [{ proposal_id: proposalId, payment_id: 'payment-comparison', status: 'PROPOSED', revision: 1, amount: 5400000, payer_name: 'Case payer', source_account_id: 'acct-case', transaction_id: 'txn-comparison', booking_date: '2026-01-15', application_id: null }] : [] })
      return
    }
    if (url.pathname === '/api/v1/cases/bundle/open' && method === 'POST') {
      opened = true
      await route.fulfill({ json: { case_id: 'bundle', scenario_version: 'v1', payment_id: 'payment-comparison', proposal_id: proposalId, jobs: [], resumed: false } })
      return
    }
    if (url.pathname === `/api/v1/proposals/${proposalId}` && method === 'GET') {
      await route.fulfill({ json: detail })
      return
    }
    if (url.pathname === `/api/v1/proposals/${proposalId}/compare` && method === 'POST') {
      const body = request.postDataJSON() as { expected_revision?: number }
      expect(body.expected_revision).toBe(1)
      compareRequests += 1
      if (options.delayCompare && compareRequests === 1) await new Promise((resolve) => setTimeout(resolve, 400))
      await route.fulfill({ json: comparison })
      return
    }
    if (url.pathname === '/api/v1/evaluation' && method === 'GET') {
      if (!sessionReady) {
        earlyEvaluationRequests += 1
        await route.fulfill({ status: 401, json: { error: { message: 'session required' } } })
        return
      }
      if (options.failEvaluationOnce && !evaluationFailed) {
        evaluationFailed = true
        await route.fulfill({ status: 503, json: { error: { message: 'Packaged evaluation temporarily unavailable.' } } })
        return
      }
      await route.fulfill({ json: evaluation })
      return
    }
    if (url.pathname === `/api/v1/sources/${sourceId}` && method === 'GET') {
      await route.fulfill({ json: { source_id: sourceId, kind: 'message', sha256: 'c'.repeat(64), bytes: 20, version: 'v1', raw_text: 'invoice 101 and 102', text: 'invoice 101 and 102', rows: [], issues: [], row_locators: [], metadata: {} } })
      return
    }
    await route.fulfill({ status: 404, json: { error: { message: 'mock route not found' } } })
  })
  return { comparisonRequests: () => compareRequests, earlyEvaluationRequests: () => earlyEvaluationRequests }
}

test.describe('comparison and evaluation', () => {
  test('compares the persisted revision without exposing financial actions', async ({ page }) => {
    const state = await mockApi(page, { delayCompare: true })
    await page.goto('/#cases')
    await expect(page.getByRole('button', { name: 'Open bundled payment case' })).toBeEnabled()
    await page.getByRole('button', { name: 'Open bundled payment case' }).click()
    await expect(page).toHaveURL(/#proposal\/proposal-comparison$/)

    const panel = page.getByRole('region', { name: 'Review-only observations' })
    await expect(panel.getByRole('button', { name: 'Compare methods' })).toBeEnabled()
    const compareResponse = page.waitForResponse((response) => response.request().method() === 'POST' && response.url().includes(`/proposals/${proposalId}/compare`))
    await panel.getByRole('button', { name: 'Compare methods' }).click()
    await page.getByRole('button', { name: 'Edit allocation' }).first().click()
    const amount = page.locator('input[aria-describedby="amount-format-help"]').first()
    await amount.fill('200.00')
    await compareResponse
    expect(state.comparisonRequests()).toBe(1)
    await expect(panel).not.toContainText('Raw score (not confidence)')
    await expect(panel.getByRole('button', { name: 'Compare methods' })).toBeDisabled()
    await expect(panel).toContainText('Save or discard unsaved correction changes before comparing this revision.')
    await page.getByRole('button', { name: 'Discard changes' }).click()
    await panel.getByRole('button', { name: 'Compare methods' }).click()
    await expect(panel).toContainText(`Same input snapshot · fingerprint ${'d'.repeat(64)}`)
    await expect(panel).toContainText('Local model execution')
    await expect(panel).toContainText('Raw score (not confidence)')
    await expect(panel.getByRole('button', { name: /Apply/i })).toHaveCount(0)
    await page.screenshot({ path: qualityDemoPath(`mock-comparison-${test.info().project.name}.png`), fullPage: true })
  })

  test('loads the packaged evaluation from a direct route and can retry a failed request', async ({ page }) => {
    const state = await mockApi(page, { delaySessionMs: 150, failEvaluationOnce: true })
    await page.goto('/#evaluation')
    await expect(page.getByRole('heading', { name: 'What the preserved report measured' })).toBeVisible()
    await expect(page).toHaveURL(/#evaluation$/)
    await expect(page.getByRole('alert')).toContainText('Packaged evaluation temporarily unavailable.')
    await page.getByRole('button', { name: 'Retry evaluation' }).click()
    await expect(page.getByRole('columnheader', { name: 'Abstained on underdetermined cases' })).toBeVisible()
    await expect(page.getByText('Active engine: rules-v2-conservative')).toBeVisible()
    await expect(page.getByText('Not evaluated')).toBeVisible()
    expect(state.earlyEvaluationRequests()).toBe(0)
    await page.screenshot({ path: qualityDemoPath(`mock-evaluation-${test.info().project.name}.png`), fullPage: true })
  })

  test('reaches evaluation from primary navigation', async ({ page }) => {
    await mockApi(page)
    await page.goto('/#cases')
    await page.getByRole('button', { name: 'Evaluation' }).click()
    await expect(page).toHaveURL(/#evaluation$/)
    await expect(page.getByRole('columnheader', { name: 'Abstained on underdetermined cases' })).toBeVisible()
  })

  test('real backend persists comparison observations and serves the packaged evaluation', async ({ page }) => {
    test.skip(process.env.E2E_REAL_CASE_LAB !== '1', 'Run with E2E_REAL_CASE_LAB=1 after the comparison and evaluation endpoints are available.')
    test.setTimeout(240_000)

    await page.goto('/#cases')
    await expect(page.locator('.case-card')).toHaveCount(5, { timeout: 90_000 })
    await page.getByRole('button', { name: 'Open bundled payment case' }).click()
    await expect(page.getByRole('heading', { name: 'Allocation detail' })).toBeVisible({ timeout: 90_000 })
    const panel = page.getByRole('region', { name: 'Review-only observations' })
    await panel.getByRole('button', { name: 'Compare methods' }).click()
    await expect(panel.locator('.comparison-method')).toHaveCount(5)
    await expect(panel).toContainText('Raw score (not confidence)')
    await expect(panel).toContainText('Local model execution')
    await expect(panel.locator('article').filter({ hasText: 'direct' })).toContainText('Unavailable')
    await expect(panel.locator('article').filter({ hasText: 'hybrid' })).toContainText('Unavailable')
    await page.reload()
    await expect(page.getByRole('heading', { name: 'Allocation detail' })).toBeVisible({ timeout: 90_000 })
    const persistedPanel = page.getByRole('region', { name: 'Review-only observations' })
    await expect(persistedPanel.locator('.comparison-method')).toHaveCount(5)
    await expect(persistedPanel).toContainText('Raw score (not confidence)')
    await page.screenshot({ path: qualityDemoPath(`real-comparison-${test.info().project.name}.png`), fullPage: true })

    await page.getByRole('button', { name: 'Evaluation' }).click()
    await expect(page.locator('.evaluation-table-wrap tbody tr')).toHaveCount(4, { timeout: 90_000 })
    await expect(page.getByText('Not evaluated')).toBeVisible()
    await page.locator('.evaluation-provenance summary').click()
    await expect(page.locator('.evaluation-provenance')).toContainText('reports/release-v1/evaluation.json')
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
    await page.screenshot({ path: qualityDemoPath(`real-evaluation-${test.info().project.name}.png`), fullPage: true })
  })
})
