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

const caseId = 'bundle'
const proposalId = 'proposal-case-lab'
const sourceId = 'source-case-lab'
const token = 'c'.repeat(64)
const fingerprint = 'd'.repeat(64)
const cases = [
  { id: 'straightforward', title: 'Straightforward payment', description: 'One clear invoice match.', amount: 10000 },
  { id: 'bundle', title: 'Bundled payment', description: 'Several plausible invoice matches.', amount: 5400000 },
  { id: 'correction', title: 'Correction case', description: 'Review an allocation that needs a correction.', amount: 300000 },
  { id: 'insufficient', title: 'Insufficient payment', description: 'The payment does not cover every balance.', amount: 10000 },
  { id: 'adversarial', title: 'Adversarial evidence', description: 'Inspect conflicting evidence before deciding.', amount: 10000 },
]

const qualityDemoPath = (name: string) => path.join(__dirname, '..', 'output', 'quality-demo', name)

const comparison = {
  revision: 1,
  input_fingerprint: fingerprint,
  methods: [
    { method: 'rules', status: 'proposed', source: 'rules', candidate: { cash: [{ invoice_id: '101', amount: 10000 }], credits: [] }, actionable: false, raw_score: null, duration_ms: null, usage: null, cost_usd: null, reason: null },
    { method: 'bounded_correction', status: 'deferred', source: 'local', candidate: null, actionable: false, raw_score: null, duration_ms: null, usage: null, cost_usd: null, reason: 'Underdetermined.' },
    { method: 'shadow_ranker', status: 'proposed', source: 'local', candidate: { cash: [{ invoice_id: '101', amount: 10000 }], credits: [] }, actionable: false, raw_score: 0.75, duration_ms: null, usage: null, cost_usd: null, reason: null },
    { method: 'direct', status: 'unavailable', source: 'unavailable', candidate: null, actionable: false, raw_score: null, duration_ms: null, usage: null, cost_usd: null, reason: 'No authenticated observation.' },
    { method: 'hybrid', status: 'unavailable', source: 'unavailable', candidate: null, actionable: false, raw_score: null, duration_ms: null, usage: null, cost_usd: null, reason: 'No authenticated observation.' },
  ],
}

const detailFor = (revision: number, variant: 'original' | 'ambiguous' | 'prompt_like') => ({
  proposal_id: proposalId,
  status: 'PROPOSED',
  revision,
  reason: 'Review the registered case before approval.',
  payment: { id: 'payment-case-lab', amount: 5400000, reference: 'Bundled payment', payer_name: 'Case payer', source_account_id: 'acct-case', transaction_id: 'txn-case-lab', booking_date: '2026-01-15' },
  cash: [{ invoice_id: '101', amount: 10000 }],
  credits: [],
  balances: { '101': { opening_amount: 3000000, cash_applied: 0, credit_applied: 0, remaining_amount: 2990000 } },
  unapplied_cash: 5390000,
  version_token: token,
  application_id: null,
  case: { id: caseId, version: `v1-${variant}`, variant },
  alternatives: [],
  signals: [],
  trace: {},
  interpretation: null,
  review_required: false,
  capabilities: { interpret: false, correct: true, apply: true, reverse: false },
  evidence: [{ source_id: sourceId, start: 0, end: 20, quote: 'invoice 101 for 100' }],
  decision_trace: { schema_version: 'v1', source: 'local', input_fingerprint: fingerprint, stages: [{ id: 'parse', name: 'Parse registered message', status: 'SUCCEEDED', summary: 'Read the active case message.', duration_ms: null, details: { variant, candidates: ['101'] }, evidence: [sourceId] }] },
  model_trace: {},
  comparison: null,
})

const reliabilityResult = {
  experiment: 'invalid_citation',
  synthetic: true,
  validator: 'citation-validator-v1',
  expected: { valid: false, source_id: sourceId },
  observed: { valid: false, issue: 'span_out_of_bounds' },
  passed: true,
  application_id: null,
  effects_before: { application_groups: 0, cash_applications: 0, credit_applications: 0, cash_centavos: 0, credit_centavos: 0 },
  effects_after: { application_groups: 0, cash_applications: 0, credit_applications: 0, cash_centavos: 0, credit_centavos: 0 },
}

async function mockCaseLabApi(page: import('@playwright/test').Page, options: { delayVariant?: boolean; delayReliability?: boolean } = {}) {
  let opened = false
  let revision = 1
  let variant: 'original' | 'ambiguous' | 'prompt_like' = 'original'
  let compareRequests = 0
  const reliabilityRequests: Array<{ expected_revision?: number; experiment?: string }> = []
  let releaseVariant: (() => void) | undefined
  let releaseReliability: (() => void) | undefined

  await page.route('**/api/v1/**', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    const method = request.method()
    if (url.pathname === '/api/v1/session' && method === 'POST') {
      await route.fulfill({ json: { mode: 'local', csrf_token: 'mock-csrf', provider_access: false, active_engine: 'recorded-case-engine', capabilities: { interpret: false, correct: true, apply: true, reverse: false } } })
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
      await route.fulfill({ json: opened ? [{ proposal_id: proposalId, status: 'PROPOSED', revision, payment_id: 'payment-case-lab', amount: 5400000, payer_name: 'Case payer', source_account_id: 'acct-case', transaction_id: 'txn-case-lab', booking_date: '2026-01-15', application_id: null }] : [] })
      return
    }
    if (url.pathname === `/api/v1/cases/${caseId}/open` && method === 'POST') {
      opened = true
      await route.fulfill({ json: { case_id: caseId, scenario_version: `v1-${variant}`, variant, payment_id: 'payment-case-lab', proposal_id: proposalId, jobs: [], resumed: false } })
      return
    }
    if (url.pathname === `/api/v1/cases/${caseId}/variant` && method === 'POST') {
      const body = request.postDataJSON() as { expected_revision?: number; variant?: typeof variant }
      expect(body.expected_revision).toBe(revision)
      expect(['original', 'ambiguous', 'prompt_like']).toContain(body.variant)
      if (options.delayVariant) await new Promise<void>((resolve) => { releaseVariant = resolve })
      variant = body.variant ?? variant
      revision += 1
      await route.fulfill({ json: { case_id: caseId, scenario_version: `v1-${variant}`, variant, payment_id: 'payment-case-lab', proposal_id: proposalId, jobs: [], resumed: true } })
      return
    }
    if (url.pathname === `/api/v1/proposals/${proposalId}` && method === 'GET') {
      await route.fulfill({ json: detailFor(revision, variant) })
      return
    }
    if (url.pathname === `/api/v1/proposals/${proposalId}/compare` && method === 'POST') {
      const body = request.postDataJSON() as { expected_revision?: number }
      expect(body.expected_revision).toBe(revision)
      compareRequests += 1
      await route.fulfill({ json: { ...comparison, revision } })
      return
    }
    if (url.pathname === `/api/v1/proposals/${proposalId}/reliability` && method === 'POST') {
      const body = request.postDataJSON() as { expected_revision?: number; experiment?: string }
      reliabilityRequests.push(body)
      expect(body.expected_revision).toBe(revision)
      if (options.delayReliability) await new Promise<void>((resolve) => { releaseReliability = resolve })
      await route.fulfill({ json: { ...reliabilityResult, experiment: body.experiment ?? 'invalid_citation' } })
      return
    }
    if (url.pathname === `/api/v1/sources/${sourceId}` && method === 'GET') {
      await route.fulfill({ json: { source_id: sourceId, kind: 'message', sha256: 'e'.repeat(64), bytes: 20, version: 'v1', raw_text: 'invoice 101 for 100', text: 'invoice 101 for 100', rows: [], issues: [], row_locators: [], metadata: {} } })
      return
    }
    await route.fulfill({ status: 404, json: { error: { message: 'mock route not found' } } })
  })
  return { compareRequests: () => compareRequests, reliabilityRequests, releaseVariant: () => releaseVariant?.(), releaseReliability: () => releaseReliability?.() }
}

test.describe('case lab controls', () => {
  test('changes a registered variant and runs a review-only reliability check', async ({ page }) => {
    const state = await mockCaseLabApi(page)
    await page.goto('/#cases')
    await expect(page.locator('[data-case-id="bundle"]')).toBeEnabled()
    await page.locator('[data-case-id="bundle"]').click()
    await expect(page.getByRole('heading', { name: 'Review this payment allocation' })).toBeVisible()
    await openEngineeringView(page)

    const variantPanel = page.getByRole('region', { name: 'Change message variant' })
    await expect(variantPanel).toContainText('Current: Original message')
    await expect(variantPanel).toContainText('Scenario v1-original')
    await variantPanel.getByLabel('Variant').selectOption('ambiguous')
    await variantPanel.getByRole('button', { name: 'Change case variant' }).click()
    await expect(variantPanel).toContainText('Current: Ambiguous message')
    await expect(variantPanel).toContainText('Scenario v1-ambiguous')
    await expect(page.getByRole('region', { name: 'Review-only observations' })).toContainText('No comparison was recorded')

    const reliabilityPanel = page.getByRole('region', { name: 'Synthetic check' })
    await expect(reliabilityPanel).toContainText('Synthetic check')
    await reliabilityPanel.getByLabel('Experiment').selectOption('invalid_citation')
    await reliabilityPanel.getByRole('button', { name: 'Run synthetic check' }).click()
    await expect(reliabilityPanel).toContainText('Server result')
    await expect(reliabilityPanel).toContainText('citation-validator-v1')
    await expect(reliabilityPanel).toContainText('span_out_of_bounds')
    await expect(reliabilityPanel).toContainText('Cash centavos')
    expect(state.reliabilityRequests).toEqual([{ expected_revision: 2, experiment: 'invalid_citation' }])

    await reliabilityPanel.getByLabel('Experiment').selectOption('duplicate_apply')
    await expect(reliabilityPanel.getByRole('button', { name: 'Run synthetic check' })).toBeDisabled()
    await expect(reliabilityPanel).toContainText('only after an explicit reviewer application')
    await reliabilityPanel.getByLabel('Experiment').selectOption('invalid_allocation')
    await expect(reliabilityPanel.getByRole('button', { name: 'Run synthetic check' })).toBeEnabled()
    expect(state.compareRequests()).toBe(0)
    await expect(page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).resolves.toBe(true)
    await page.screenshot({ path: qualityDemoPath(`mock-case-lab-${test.info().project.name}.png`), fullPage: true })
  })

  test('blocks financial actions and comparison while a case lab request is in flight', async ({ page }) => {
    const state = await mockCaseLabApi(page, { delayVariant: true, delayReliability: true })
    await page.goto('/#cases')
    await page.locator('[data-case-id="bundle"]').click()
    await expect(page.getByRole('heading', { name: 'Review this payment allocation' })).toBeVisible()
    await openEngineeringView(page)

    const variantPanel = page.getByRole('region', { name: 'Change message variant' })
    await variantPanel.getByLabel('Variant').selectOption('prompt_like')
    await variantPanel.getByRole('button', { name: 'Change case variant' }).click()
    await expect(variantPanel.getByRole('button', { name: 'Changing variant…' })).toBeVisible()
    await expect(page.getByRole('button', { name: 'Compare methods' })).toBeDisabled()
    await expect(page.getByRole('button', { name: 'Apply allocation' })).toBeDisabled()
    await expect(page.getByRole('button', { name: 'Edit allocation' }).first()).toBeDisabled()
    await expect(page.getByLabel('Reviewer name')).toBeDisabled()
    state.releaseVariant()
    await expect(variantPanel).toContainText('Prompt-like message')

    const reliabilityPanel = page.getByRole('region', { name: 'Synthetic check' })
    await reliabilityPanel.getByLabel('Experiment').selectOption('invalid_citation')
    await reliabilityPanel.getByRole('button', { name: 'Run synthetic check' }).click()
    await expect(reliabilityPanel.getByRole('button', { name: 'Running synthetic check…' })).toBeVisible()
    await expect(page.getByRole('button', { name: 'Compare methods' })).toBeDisabled()
    await expect(page.getByRole('button', { name: 'Apply allocation' })).toBeDisabled()
    await expect(page.getByRole('button', { name: 'Edit allocation' }).first()).toBeDisabled()
    await expect(page.getByLabel('Reviewer name')).toBeDisabled()
    state.releaseReliability()
    await expect(reliabilityPanel).toContainText('Server result')
  })
})

test.describe('real case lab', () => {
  test('runs registered variants and reliability checks without changing financial history', async ({ page }) => {
    test.skip(process.env.E2E_REAL_CASE_LAB !== '1', 'Run with E2E_REAL_CASE_LAB=1 after the case lab endpoints are available.')
    test.setTimeout(300_000)

    const expectNoHorizontalOverflow = async () => {
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
    }
    let realProposalId = ''
    const runSynthetic = async (experiment: string) => {
      const panel = page.getByRole('region', { name: 'Synthetic check' })
      await panel.getByLabel('Experiment').selectOption(experiment)
      const responsePromise = page.waitForResponse((response) => response.request().method() === 'POST' && new URL(response.url()).pathname === `/api/v1/proposals/${realProposalId}/reliability`)
      await panel.getByRole('button', { name: 'Run synthetic check' }).click()
      await expect(panel).toContainText('Server result', { timeout: 90_000 })
      await expect(panel).toContainText('Passed')
      await expect(panel.locator('.reliability-effects tbody tr')).toHaveCount(5)
      return await (await responsePromise).json() as {
        experiment: string
        application_id: string | null
        passed: boolean
        observed: Record<string, unknown>
        effects_before: Record<string, number>
        effects_after: Record<string, number>
      }
    }

    await page.goto('/#cases')
    await expect(page.locator('button[data-case-id]')).toHaveCount(10, { timeout: 90_000 })
    await revealRegressionCases(page)
    await page.locator('[data-case-id="bundle"]').click()
    await expect(page.getByRole('heading', { name: 'Review this payment allocation' })).toBeVisible({ timeout: 90_000 })
    await openEngineeringView(page)
    realProposalId = decodeURIComponent(new URL(page.url()).hash.replace('#proposal/', ''))
    expect(realProposalId).not.toBe(proposalId)
    await page.getByRole('button', { name: 'Compare methods' }).click()
    await expect(page.locator('.comparison-method')).toHaveCount(5)
    await expectNoHorizontalOverflow()

    const variants = [
      ['ambiguous', 'Ambiguous message'],
      ['original', 'Original message'],
      ['prompt_like', 'Prompt-like message'],
    ] as const
    for (const [value, label] of variants) {
      const panel = page.getByRole('region', { name: 'Change message variant' })
      await panel.getByLabel('Variant').selectOption(value)
      await panel.getByRole('button', { name: 'Change case variant' }).click()
      await expect(panel).toContainText(`Current: ${label}`, { timeout: 90_000 })
      await expect(page.getByRole('region', { name: 'Review-only observations' })).toContainText('No comparison was recorded')
      await expectNoHorizontalOverflow()
    }
    await page.reload()
    await expect(page.getByRole('heading', { name: 'Review this payment allocation' })).toBeVisible({ timeout: 90_000 })
    await expect(page.getByRole('region', { name: 'Change message variant' })).toContainText('Current: Prompt-like message')
    const originalPanel = page.getByRole('region', { name: 'Change message variant' })
    await originalPanel.getByLabel('Variant').selectOption('original')
    await originalPanel.getByRole('button', { name: 'Change case variant' }).click()
    await expect(originalPanel).toContainText('Current: Original message', { timeout: 90_000 })
    await expect(page.getByRole('region', { name: 'Review-only observations' })).toContainText('No comparison was recorded')

    const beforeChecks = await runSynthetic('invalid_allocation')
    const citationChecks = await runSynthetic('invalid_citation')
    const staleChecks = await runSynthetic('stale_apply')
    for (const result of [beforeChecks, citationChecks, staleChecks]) {
      expect(result.passed).toBe(true)
      expect(result.effects_before).toEqual(result.effects_after)
    }

    await page.getByLabel('Reviewer name').fill('Case lab reviewer')
    const applyResponse = page.waitForResponse((response) => response.request().method() === 'POST' && new URL(response.url()).pathname.endsWith('/apply'))
    await page.getByRole('button', { name: 'Apply allocation' }).click()
    const confirmation = page.getByRole('dialog', { name: 'Record this allocation?' })
    await expect(confirmation).toBeVisible()
    await confirmation.getByRole('button', { name: 'Confirm and apply' }).click()
    const applyBody = await (await applyResponse).json() as { application_id?: string }
    expect(applyBody.application_id).toBeTruthy()
    await expect(page.locator('.payment-card .status-pill')).toHaveText('Recorded', { timeout: 90_000 })

    const appliedVariant = page.getByRole('region', { name: 'Change message variant' })
    await expect(appliedVariant.getByRole('button', { name: 'Change case variant' })).toBeDisabled()
    await expect(appliedVariant).toContainText('cannot change an applied or reversed revision')
    const duplicateResult = await runSynthetic('duplicate_apply')
    expect(duplicateResult.passed).toBe(true)
    expect(duplicateResult.effects_before).toEqual(duplicateResult.effects_after)
    expect(duplicateResult.effects_after).toEqual({
      application_groups: 1,
      cash_applications: 2,
      credit_applications: 1,
      cash_centavos: 5400000,
      credit_centavos: 100000,
    })
    expect(duplicateResult.application_id).toBe(applyBody.application_id)
    expect(duplicateResult.observed).toMatchObject({ same_application: true, application_id: applyBody.application_id })
    const reliabilityPanel = page.getByRole('region', { name: 'Synthetic check' })
    await expect(reliabilityPanel).toContainText(applyBody.application_id as string)
    await reliabilityPanel.screenshot({ path: qualityDemoPath(`real-case-lab-reliability-duplicate-${test.info().project.name}.png`) })

    await page.getByLabel('Reversal reason').fill('Case lab reversal')
    await page.getByRole('button', { name: 'Reverse application' }).click()
    await expect(page.locator('.payment-card .status-pill')).toHaveText('Reversed', { timeout: 90_000 })
    await expect(appliedVariant.getByRole('button', { name: 'Change case variant' })).toBeDisabled()
    await expectNoHorizontalOverflow()
    await page.screenshot({ path: qualityDemoPath(`real-case-lab-${test.info().project.name}.png`), fullPage: true })
  })
})
