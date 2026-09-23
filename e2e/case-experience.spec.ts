import { expect, test } from '@playwright/test'
import path from 'node:path'

const openEngineeringView = async (page: import('@playwright/test').Page) => {
  const details = page.locator('details.engineering-details')
  await expect(details).toBeVisible()
  if (!(await details.evaluate((element) => (element as HTMLDetailsElement).open))) await details.locator(':scope > summary').click()
}
const revealRegressionCases = async (page: import('@playwright/test').Page) => {
  // Wait for the case list to render before deciding whether the fold exists.
  await expect(page.locator('button[data-case-id]').first()).toBeVisible()
  const details = page.locator('details.regression-cases')
  if (await details.count() && !(await details.evaluate((element) => (element as HTMLDetailsElement).open))) await details.locator(':scope > summary').click()
}

const proposalId = 'proposal-1'
const paymentId = 'payment-1'
const sourceId = 'source-1'
const token = 'a'.repeat(64)
const expectNoHorizontalOverflow = async (page: import('@playwright/test').Page) => {
  const size = await page.evaluate(() => ({ page: document.documentElement.scrollWidth, viewport: window.innerWidth }))
  expect(size.page).toBeLessThanOrEqual(size.viewport)
}
const qualityDemoPath = (name: string) => path.join(__dirname, '..', 'output', 'quality-demo', name)

const cases = [
  { id: 'straightforward', title: 'Straightforward payment', description: 'One clear invoice match.', amount: 10000 },
  { id: 'bundle', title: 'Bundled payment', description: 'Several plausible invoice matches.', amount: 5400000 },
  { id: 'correction', title: 'Correction case', description: 'Review an allocation that needs a correction.', amount: 300000 },
  { id: 'insufficient', title: 'Insufficient payment', description: 'The payment does not cover every balance.', amount: 10000 },
  { id: 'adversarial', title: 'Adversarial evidence', description: 'Inspect conflicting evidence before deciding.', amount: 10000 },
]

async function mockCaseApi(page: import('@playwright/test').Page) {
  let openedCase: string | undefined
  const openCounts = new Map<string, number>()
  let jobRuns = 0

  const proposalForCase = (caseId: string) => caseId === 'bundle' ? proposalId : `${caseId}-proposal`
  const paymentForCase = (caseId: string) => caseId === 'bundle' ? paymentId : `payment-${caseId}`
  const activeCase = () => openedCase ? cases.find((item) => item.id === openedCase) ?? cases[0] : undefined

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
    const openMatch = url.pathname.match(/^\/api\/v1\/cases\/([^/]+)\/open$/)
    if (openMatch && method === 'POST') {
      const caseId = decodeURIComponent(openMatch[1])
      const resumed = openCounts.has(caseId)
      openCounts.set(caseId, (openCounts.get(caseId) ?? 0) + 1)
      openedCase = caseId
      jobRuns = 0
      await route.fulfill({ json: { case_id: caseId, scenario_version: 'v1', payment_id: paymentForCase(caseId), proposal_id: null, jobs: ['job-1'], resumed } })
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
      const currentCase = activeCase()
      await route.fulfill({ json: currentCase ? [{ proposal_id: proposalForCase(currentCase.id), payment_id: paymentForCase(currentCase.id), status: 'NEEDS_REVIEW', revision: 1, amount: currentCase.amount, payer_name: 'Case payer', source_account_id: 'acct-case', transaction_id: paymentForCase(currentCase.id), booking_date: '2026-01-15', application_id: null }] : [] })
      return
    }
    const currentCase = activeCase()
    const currentProposalId = currentCase ? proposalForCase(currentCase.id) : undefined
    if (currentProposalId && url.pathname === `/api/v1/proposals/${currentProposalId}` && method === 'GET') {
      await route.fulfill({ json: {
        proposal_id: currentProposalId,
        payment_id: paymentForCase(currentCase.id),
        status: 'NEEDS_REVIEW',
        revision: 1,
        reason: 'Needs evidence review',
        payment: { id: paymentForCase(currentCase.id), amount: currentCase.amount, reference: `${currentCase.title} payment`, payer_name: 'Case payer', source_account_id: 'acct-case', transaction_id: paymentForCase(currentCase.id), booking_date: '2026-01-15' },
        cash: [{ invoice_id: '101', amount: 10000 }],
        credits: [{ credit_note_id: '103', invoice_id: '102', amount: 100000 }],
        balances: {
          '101': { opening_amount: 3000000, cash_applied: 0, credit_applied: 0, remaining_amount: 2990000 },
          '102': { opening_amount: 2500000, cash_applied: 0, credit_applied: 100000, remaining_amount: 2400000 },
        },
        unapplied_cash: 5290000,
        version_token: token,
        application_id: null,
        case: { id: currentCase.id, version: 'v1' },
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

    await expect(page.getByRole('heading', { name: 'Which invoices does this payment settle?' })).toBeVisible()
    await expect(page.locator('button[data-case-id]')).toHaveCount(5)
    await expect(page.locator('.session-meta')).toHaveAttribute('aria-label', /recorded-case-engine/)
    await expect(page.locator('[data-case-id="bundle"]')).toBeEnabled()

    await page.locator('[data-case-id="bundle"]').click()
    await expect(page).toHaveURL(/#proposal\/proposal-1$/)
    await expect(page.getByRole('heading', { name: 'Review this payment allocation' })).toBeVisible()
    await expect(page.getByRole('heading', { name: 'A person needs to decide' })).toBeVisible()
    await expect(page.getByText('Reason: Needs evidence review')).toBeVisible()
    await expect(page.getByText('Invoice 101', { exact: true })).toBeVisible()
    await expect(page.getByText('Credit note 103', { exact: true })).toBeVisible()
    await expect(page.getByText('Linked invoice 102')).toBeVisible()
    await expect(page.getByRole('button', { name: 'Read with AI' })).toBeDisabled()
    await expect(page.getByRole('button', { name: 'With ranking context' })).toBeDisabled()

    const skipLink = page.getByRole('link', { name: 'Skip to content' })
    await skipLink.focus()
    await skipLink.press('Enter')
    await expect(page).toHaveURL(/#proposal\/proposal-1$/)
    await expect.poll(() => page.evaluate(() => document.activeElement?.id)).toBe('main-content')

    await page.getByRole('button', { name: 'Open source' }).first().click()
    const source = page.getByRole('dialog').filter({ hasText: 'source-1' })
    await expect(source).toBeVisible()
    await expect(source).toContainText('exact source text')
    await expect(source).toContainText('abc123')
    await expect(source).toContainText('42')
    await source.getByLabel('Close source').click()
    await expect(source).toBeHidden()

    await openEngineeringView(page)
    await page.getByText('Stage details').click()
    await expect(page.getByText('rules-v1')).toBeVisible()
    await expect(page.getByRole('button', { name: /Open source source-1/ })).toBeVisible()
    await expect(page.getByText('Not measured')).toBeVisible()

    await page.goBack()
    await expect(page.getByRole('heading', { name: 'Which invoices does this payment settle?' })).toBeVisible()
    await page.goForward()
    await expect(page.getByRole('heading', { name: 'Review this payment allocation' })).toBeVisible()
    await page.reload()
    await expect(page.getByRole('heading', { name: 'Review this payment allocation' })).toBeVisible()
    await page.getByText('Stage details').click()
    await expect(page.getByText('rules-v1')).toBeVisible()
    await page.getByRole('button', { name: 'Open source' }).first().click()
    await expect(page.getByRole('dialog').filter({ hasText: 'source-1' })).toContainText('exact source text')
    await page.getByRole('dialog').filter({ hasText: 'source-1' }).getByLabel('Close source').click()
    await expectNoHorizontalOverflow(page)
    await page.screenshot({ path: qualityDemoPath(`mock-case-${test.info().project.name}.png`), fullPage: true })
  })

  test('opens and resumes every registered case without changing the viewport', async ({ page }) => {
    await mockCaseApi(page)
    await page.goto('/')
    await expect(page.locator('button[data-case-id]')).toHaveCount(5)

    for (const scenario of cases) {
      const card = page.locator(`[data-case-id="${scenario.id}"]`)
      const firstOpen = page.waitForResponse((response) => response.request().method() === 'POST' && response.url().includes(`/api/v1/cases/${scenario.id}/open`))
      await card.click()
      await expect((await firstOpen).json()).resolves.toMatchObject({ case_id: scenario.id, resumed: false })
      await expect(page.getByRole('heading', { name: 'Review this payment allocation' })).toBeVisible()
      await expect(page.locator(`.decision-summary[data-case-id="${scenario.id}"]`)).toBeVisible()
      await expectNoHorizontalOverflow(page)

      await page.getByRole('button', { name: 'Cases' }).click()
      const resumedOpen = page.waitForResponse((response) => response.request().method() === 'POST' && response.url().includes(`/api/v1/cases/${scenario.id}/open`))
      await page.locator(`[data-case-id="${scenario.id}"]`).click()
      await expect((await resumedOpen).json()).resolves.toMatchObject({ case_id: scenario.id, resumed: true })
      await expect(page.getByRole('heading', { name: 'Review this payment allocation' })).toBeVisible()
      await expectNoHorizontalOverflow(page)

      if (scenario !== cases[cases.length - 1]) await page.getByRole('button', { name: 'Cases' }).click()
    }

    await page.screenshot({ path: qualityDemoPath(`mock-cases-${test.info().project.name}.png`), fullPage: true })
  })

  test('real backend opens and resumes all five cases with persisted evidence', async ({ page }) => {
    test.skip(process.env.E2E_REAL_CASES !== '1', 'Run with E2E_REAL_CASES=1 after the case API is available.')
    test.setTimeout(240_000)

    await page.goto('/')
    await expect(page.locator('button[data-case-id]')).toHaveCount(10, { timeout: 90_000 })
    await revealRegressionCases(page)
    await expectNoHorizontalOverflow(page)
    await page.screenshot({ path: qualityDemoPath(`real-case-list-${test.info().project.name}.png`), fullPage: true })

    for (const scenario of cases) {
      // Returning to Cases re-renders the folded regression list.
      await revealRegressionCases(page)
      const card = page.locator(`[data-case-id="${scenario.id}"]`)
      const firstOpen = page.waitForResponse((response) => response.request().method() === 'POST' && new URL(response.url()).pathname === `/api/v1/cases/${scenario.id}/open`)
      await card.click()
      const firstBody = await (await firstOpen).json() as { case_id?: string; resumed?: boolean }
      expect(firstBody).toMatchObject({ case_id: scenario.id, resumed: false })
      await expect(page.getByRole('heading', { name: 'Review this payment allocation' })).toBeVisible()
      await expect(page.locator(`.decision-summary[data-case-id="${scenario.id}"]`)).toBeVisible()
      await expectNoHorizontalOverflow(page)

      if (scenario.id === 'bundle') {
        await openEngineeringView(page)
        await expect(page.getByRole('heading', { name: 'How this decision was produced' })).toBeVisible()
        await page.getByText('Stage details').first().click()
        await expect(page.locator('.trace-stage-details').first()).toBeVisible()
        await page.screenshot({ path: qualityDemoPath(`real-bundle-trace-${test.info().project.name}.png`), fullPage: true })
        const sourceButton = page.getByRole('button', { name: 'Open source' }).first()
        await expect(sourceButton).toBeVisible()
        await sourceButton.click()
        const source = page.getByRole('dialog')
        await expect(source).toBeVisible()
        await expect(source).toContainText('Source ID')
        await expect(source).toContainText('Exact source metadata')
        await page.screenshot({ path: qualityDemoPath(`real-bundle-source-${test.info().project.name}.png`), fullPage: true })
        await source.getByLabel('Close source').click()
        await page.reload()
        await expect(page.getByRole('heading', { name: 'How this decision was produced' })).toBeVisible()
        await expect(page.getByRole('button', { name: 'Open source' }).first()).toBeVisible()
      }

      await page.getByRole('button', { name: 'Cases' }).click()
      await expect(page.locator('button[data-case-id]')).toHaveCount(10)
      await revealRegressionCases(page)
      const resumedOpen = page.waitForResponse((response) => response.request().method() === 'POST' && new URL(response.url()).pathname === `/api/v1/cases/${scenario.id}/open`)
      await page.locator(`[data-case-id="${scenario.id}"]`).click()
      const resumedBody = await (await resumedOpen).json() as { case_id?: string; resumed?: boolean }
      expect(resumedBody).toMatchObject({ case_id: scenario.id, resumed: true })
      await expect(page.getByRole('heading', { name: 'Review this payment allocation' })).toBeVisible()
      await expectNoHorizontalOverflow(page)
      if (scenario !== cases[cases.length - 1]) await page.getByRole('button', { name: 'Cases' }).click()
    }
  })
})
