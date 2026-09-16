import { expect, test } from '@playwright/test'

const proposalId = 'proposal-1'
const paymentId = 'payment-1'
const sourceId = 'source-1'
const token = 'a'.repeat(64)

const cases = [
  { id: 'straightforward', title: 'Straightforward payment', description: 'One clear invoice match.', amount: 10000 },
  { id: 'bundle', title: 'Bundled payment', description: 'Several plausible invoice matches.', amount: 5400000 },
  { id: 'correction', title: 'Correction case', description: 'Review an allocation that needs a correction.', amount: 300000 },
  { id: 'insufficient', title: 'Insufficient payment', description: 'The payment does not cover every balance.', amount: 10000 },
  { id: 'adversarial', title: 'Adversarial evidence', description: 'Inspect conflicting evidence before deciding.', amount: 10000 },
]

async function mockCaseApi(page: import('@playwright/test').Page) {
  let opened = false
  let jobRuns = 0

  await page.route('**/api/v1/**', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    const method = request.method()
    if (url.pathname === '/api/v1/session' && method === 'POST') {
      await route.fulfill({ json: { mode: 'local', csrf_token: 'mock-csrf', provider_access: false, active_engine: 'recorded-case-engine', capabilities: { interpret: true, correct: true, apply: true, reverse: true } } })
      return
    }
    if (url.pathname === '/api/v1/cases' && method === 'GET') {
      await route.fulfill({ json: { version: 'case-fixtures-v1', cases } })
      return
    }
    if (url.pathname === '/api/v1/cases/bundle/open' && method === 'POST') {
      opened = true
      jobRuns = 0
      await route.fulfill({ json: { case_id: 'bundle', scenario_version: 'v1', payment_id: paymentId, proposal_id: null, jobs: ['job-1'], resumed: false } })
      return
    }
    if (url.pathname === '/api/v1/jobs/run-once' && method === 'POST') {
      jobRuns += 1
      await route.fulfill({ json: { job_id: 'job-1', status: jobRuns === 1 ? 'RUNNING' : 'SUCCEEDED' } })
      return
    }
    if (url.pathname === '/api/v1/imports' && method === 'GET') {
      await route.fulfill({ json: [] })
      return
    }
    if (url.pathname === '/api/v1/proposals' && method === 'GET') {
      await route.fulfill({ json: opened ? [{ proposal_id: proposalId, payment_id: paymentId, status: 'NEEDS_REVIEW', revision: 1, amount: 5400000, payer_name: 'Case payer', source_account_id: 'acct-case', transaction_id: paymentId, booking_date: '2026-01-15', application_id: null }] : [] })
      return
    }
    if (url.pathname === `/api/v1/proposals/${proposalId}` && method === 'GET') {
      await route.fulfill({ json: {
        proposal_id: proposalId,
        payment_id: paymentId,
        status: 'NEEDS_REVIEW',
        revision: 1,
        reason: 'Needs evidence review',
        payment: { id: paymentId, amount: 5400000, reference: 'Bundle payment', payer_name: 'Case payer', source_account_id: 'acct-case', transaction_id: paymentId, booking_date: '2026-01-15' },
        cash: [{ invoice_id: '101', amount: 10000 }],
        credits: [{ credit_note_id: '103', invoice_id: '102', amount: 100000 }],
        balances: {
          '101': { opening_amount: 3000000, cash_applied: 0, credit_applied: 0, remaining_amount: 2990000 },
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
        capabilities: { interpret: false, correct: true, apply: false, reverse: false },
        evidence: [{ source_id: sourceId, start: 0, end: 12, quote: 'invoice 101' }],
        decision_trace: {
          schema_version: 'v1',
          source: 'recorded',
          stages: [{ id: 'parse', name: 'Parse evidence', status: 'SUCCEEDED', summary: 'Read the registered payment message.', duration_ms: null, details: { validator: 'rules-v1', candidates: ['101', '102'] }, evidence: [sourceId] }],
        },
        model_trace: { legacy: 'preserved' },
      } })
      return
    }
    if (url.pathname === `/api/v1/sources/${sourceId}` && method === 'GET') {
      await route.fulfill({ json: { source_id: sourceId, kind: 'message', sha256: 'abc123', bytes: 42, text: 'exact source text\n', rows: [], issues: [], row_locators: [], metadata: { version: 'v1', owner: 'case' } } })
      return
    }
    await route.fulfill({ status: 404, json: { detail: 'mock route not found' } })
  })
}

test.describe('case study workspace', () => {
  test('opens the bundled case, exposes evidence and trace, and preserves browser routes', async ({ page }) => {
    await mockCaseApi(page)
    await page.goto('/')

    await expect(page.getByRole('heading', { name: 'Start with a bundled payment case' })).toBeVisible()
    await expect(page.locator('.case-card')).toHaveCount(5)
    await expect(page.locator('.session-meta')).toContainText('recorded-case-engine')
    await expect(page.getByRole('button', { name: 'Open bundled payment case' })).toBeEnabled()

    await page.getByRole('button', { name: 'Open bundled payment case' }).click()
    await expect(page).toHaveURL(/#proposal\/proposal-1$/)
    await expect(page.getByRole('heading', { name: 'Allocation detail' })).toBeVisible()
    await expect(page.getByRole('heading', { name: 'Needs review' })).toBeVisible()
    await expect(page.getByText('Reason: Needs evidence review')).toBeVisible()
    await expect(page.getByText('Invoice 101', { exact: true })).toBeVisible()
    await expect(page.getByText('Credit note 103', { exact: true })).toBeVisible()
    await expect(page.getByText('Linked invoice 102')).toBeVisible()
    await expect(page.getByRole('button', { name: 'Direct' })).toBeDisabled()
    await expect(page.getByRole('button', { name: 'Hybrid' })).toBeDisabled()

    const skipLink = page.getByRole('link', { name: 'Skip to content' })
    await skipLink.focus()
    await skipLink.press('Enter')
    await expect(page).toHaveURL(/#proposal\/proposal-1$/)
    await expect.poll(() => page.evaluate(() => document.activeElement?.id)).toBe('main-content')

    await page.getByRole('button', { name: 'Open source' }).first().click()
    const source = page.getByRole('dialog', { name: /source-1/ })
    await expect(source).toBeVisible()
    await expect(source).toContainText('exact source text')
    await expect(source).toContainText('abc123')
    await expect(source).toContainText('42')
    await source.getByLabel('Close source').click()
    await expect(source).toBeHidden()

    await page.getByText('Stage details').click()
    await expect(page.getByText('rules-v1')).toBeVisible()
    await expect(page.getByRole('button', { name: /Open source source-1/ })).toBeVisible()
    await expect(page.getByText('Not measured')).toBeVisible()

    await page.goBack()
    await expect(page.getByRole('heading', { name: 'Start with a bundled payment case' })).toBeVisible()
    await page.goForward()
    await expect(page.getByRole('heading', { name: 'Allocation detail' })).toBeVisible()
    await page.reload()
    await expect(page.getByRole('heading', { name: 'Allocation detail' })).toBeVisible()
    await page.screenshot({ path: `output/quality-demo/mock-case-${test.info().project.name}.png`, fullPage: true })
  })
})
