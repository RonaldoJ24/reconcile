import { expect, test } from '@playwright/test'

const proposalId = 'proposal-1'
const token = 'a'.repeat(64)

function detail(status: 'PROPOSED' | 'APPLIED' | 'REVERSED') {
  return {
    proposal_id: proposalId,
    status,
    revision: 1,
    payment: { id: 'payment-1', amount: 5_400_000, reference: 'Mock payment', payer_name: 'Mock payer', source_account_id: 'acct-1', transaction_id: 'pay-1', booking_date: '2026-01-15' },
    cash: [{ invoice_id: '101', amount: 3_000_000 }],
    credits: [{ credit_note_id: '103', invoice_id: '102', amount: 100_000 }],
    balances: {
      '101': { opening_amount: 3_000_000, cash_applied: 0, credit_applied: 0, remaining_amount: 3_000_000 },
      '102': { opening_amount: 2_500_000, cash_applied: 0, credit_applied: 0, remaining_amount: 2_500_000 },
    },
    version_token: token,
    application_id: status === 'PROPOSED' ? null : 'application-1',
    evidence: [], alternatives: [], signals: [], reason: null, unapplied_cash: 2_400_000, trace: {}, interpretation: null, review_required: false,
  }
}

async function mockApi(page: import('@playwright/test').Page, uncertainFirstApply = false) {
  let status: 'PROPOSED' | 'APPLIED' | 'REVERSED' = 'PROPOSED'
  let failedApply = false
  const applyKeys: string[] = []

  await page.route('**/api/v1/**', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    const method = request.method()
    if (url.pathname === '/api/v1/session' && method === 'POST') {
      await route.fulfill({ json: { mode: 'local', csrf_token: 'mock-csrf', provider_access: true, active_engine: 'rules-v2', capabilities: { interpret: true, correct: true, apply: true, reverse: true } } })
      return
    }
    if (url.pathname === '/api/v1/cases' && method === 'GET') {
      await route.fulfill({ json: { version: 'mock-v1', cases: [
        { id: 'straightforward', title: 'Straightforward payment', description: 'One clear invoice match.', amount: 10000 },
        { id: 'bundle', title: 'Bundled payment', description: 'Several plausible invoice matches.', amount: 5400000 },
        { id: 'correction', title: 'Correction case', description: 'Review an allocation that needs a correction.', amount: 300000 },
        { id: 'insufficient', title: 'Insufficient payment', description: 'The payment does not cover every balance.', amount: 10000 },
        { id: 'adversarial', title: 'Adversarial evidence', description: 'Inspect conflicting evidence before deciding.', amount: 10000 },
      ] } })
      return
    }
    if (url.pathname === '/api/v1/imports' && method === 'GET') {
      await route.fulfill({ json: [] })
      return
    }
    if (url.pathname === '/api/v1/proposals' && method === 'GET') {
      await route.fulfill({ json: [{ proposal_id: proposalId, status, revision: 1, payment_id: 'payment-1', amount: 5_400_000, payer_name: 'Mock payer', source_account_id: 'acct-1', transaction_id: 'pay-1', booking_date: '2026-01-15', application_id: status === 'PROPOSED' ? null : 'application-1' }] })
      return
    }
    if (url.pathname === `/api/v1/proposals/${proposalId}` && method === 'GET') {
      await route.fulfill({ json: detail(status) })
      return
    }
    if (url.pathname === `/api/v1/proposals/${proposalId}/correct` && method === 'POST') {
      status = 'PROPOSED'
      await route.fulfill({ json: { proposal_id: proposalId, revision: 1, status } })
      return
    }
    if (url.pathname === `/api/v1/proposals/${proposalId}/apply` && method === 'POST') {
      const payload = JSON.parse(request.postData() ?? '{}') as { idempotency_key?: string }
      applyKeys.push(payload.idempotency_key ?? '')
      if (uncertainFirstApply && !failedApply) {
        failedApply = true
        await route.abort('failed')
        return
      }
      status = 'APPLIED'
      await route.fulfill({ json: { application_id: 'application-1', proposal_id: proposalId, revision: 1, reversed: false } })
      return
    }
    if (url.pathname === '/api/v1/applications/application-1/reverse' && method === 'POST') {
      status = 'REVERSED'
      await route.fulfill({ json: { application_id: 'application-1', reversed: true } })
      return
    }
    await route.fulfill({ status: 404, json: { detail: 'mock route not found' } })
  })
  return applyKeys
}

async function openProposal(page: import('@playwright/test').Page) {
  await page.goto('/')
  await page.getByRole('button', { name: 'Review queue' }).click()
  await page.locator('.proposal-card').first().click()
  await expect(page.getByRole('heading', { name: 'Allocation detail' })).toBeVisible()
}

test.describe('financial interaction integrity', () => {
  test('blocks apply for invalid or unsaved visible edits and supports discard', async ({ page }) => {
    await mockApi(page)
    await openProposal(page)
    await page.getByLabel('Reviewer name').fill('Mock reviewer')
    await page.getByRole('button', { name: 'Edit allocation' }).first().click()
    const apply = page.getByRole('button', { name: 'Apply allocation' })
    const amount = page.getByLabel('Amount (MXN)').first()
    await expect(page.locator('#amount-format-help')).toBeVisible()
    await expect(amount).toHaveAttribute('aria-describedby', 'amount-format-help')

    await amount.fill('100.')
    await expect(apply).toBeDisabled()
    await expect(page.getByText(/Cash line 1:/)).toBeVisible()

    await amount.fill('30000')
    await expect(apply).toBeDisabled()
    await expect(page.getByText(/Unsaved changes/)).toBeVisible()
    await page.getByRole('button', { name: 'Discard changes' }).click()
    await expect(amount).toHaveValue('30000.00')
    await expect(page.getByText(/Unsaved changes/)).toHaveCount(0)
    await expect(apply).toBeEnabled()
    await apply.click()
    await expect(page.getByRole('dialog', { name: 'Apply persisted allocation?' })).toBeVisible()
    await page.keyboard.press('Escape')
    await expect(page.getByRole('dialog', { name: 'Apply persisted allocation?' })).toBeHidden()
  })

  test('retries uncertain apply with one key and keeps reviewer usable for reversal', async ({ page }) => {
    const applyKeys = await mockApi(page, true)
    await openProposal(page)
    await page.getByLabel('Reviewer name').fill('Initial reviewer')
    await page.getByRole('button', { name: 'Apply allocation' }).click()
    const dialog = page.getByRole('dialog', { name: 'Apply persisted allocation?' })
    await expect(dialog).toBeVisible()
    await expect(dialog).toContainText('Review revision 1')
    await expect(dialog).toContainText('Projected invoice balances')
    await expect(dialog).toContainText('does not move money in a bank account')

    await dialog.getByRole('button', { name: 'Confirm and apply' }).click()
    await expect(page.locator('.error-banner')).toBeVisible()
    await expect(dialog).toBeVisible()
    await dialog.getByRole('button', { name: 'Confirm and apply' }).click()
    await expect(page.locator('.payment-card .status-pill')).toHaveText('APPLIED')
    expect(applyKeys).toHaveLength(2)
    expect(applyKeys[0]).toBe(applyKeys[1])

    const reviewer = page.getByLabel('Reviewer name')
    await expect(reviewer).toBeEnabled()
    await reviewer.fill('Reversal reviewer')
    await page.getByLabel('Reversal reason').fill('Mock reversal')
    await page.getByRole('button', { name: 'Reverse application' }).click()
    await expect(page.locator('.payment-card .status-pill')).toHaveText('REVERSED')
  })

  test('keeps the confirmation modal open when Escape is pressed during apply', async ({ page }) => {
    await mockApi(page)
    let releaseApply: (() => Promise<void>) | undefined
    await page.route(`**/api/v1/proposals/${proposalId}/apply`, async (route) => {
      await new Promise<void>((resolve) => {
        releaseApply = async () => {
          resolve()
          await route.fallback()
        }
      })
    })

    await openProposal(page)
    await page.getByLabel('Reviewer name').fill('Busy apply reviewer')
    await page.getByRole('button', { name: 'Apply allocation' }).click()
    const dialog = page.getByRole('dialog', { name: 'Apply persisted allocation?' })
    const applyRequest = page.waitForRequest((request) => request.url().endsWith(`/api/v1/proposals/${proposalId}/apply`) && request.method() === 'POST')
    await dialog.getByRole('button', { name: 'Confirm and apply' }).click()
    await applyRequest
    await expect(dialog).toBeVisible()
    await page.keyboard.press('Escape')
    await expect(dialog).toBeVisible()
    await releaseApply?.()
    await expect(page.locator('.payment-card .status-pill')).toHaveText('APPLIED')
  })

  test('does not let stale validation restore commit and invalidates a successful result on input change', async ({ page }) => {
    await mockApi(page)
    let validationCount = 0
    let releaseFirst: (() => Promise<void>) | undefined
    await page.route('**/api/v1/imports/validate', async (route) => {
      validationCount += 1
      if (validationCount === 1) {
        await new Promise<void>((resolve, reject) => {
          releaseFirst = async () => {
            try {
              await route.fulfill({ json: { batch_id: 'stale-batch', accepted: 2, rejected: 0 } })
              resolve()
            } catch (cause) {
              reject(cause)
            }
          }
        })
        return
      }
      await route.fulfill({ json: { batch_id: 'fresh-batch', accepted: 2, rejected: 0 } })
    })

    await page.goto('/')
    await page.getByRole('button', { name: 'Imports' }).click()
    await page.getByLabel('Bank CSV').setInputFiles({ name: 'bank.csv', mimeType: 'text/csv', buffer: Buffer.from('source_account_id,transaction_id,amount\nacct,pay,100\n') })
    await page.getByLabel('Invoice CSV').setInputFiles({ name: 'invoices.csv', mimeType: 'text/csv', buffer: Buffer.from('invoice_id,outstanding_amount\n101,100\n') })
    const firstRequest = page.waitForRequest((request) => request.url().endsWith('/api/v1/imports/validate') && request.method() === 'POST')
    await page.getByRole('button', { name: 'Validate files' }).click()
    await firstRequest
    await expect.poll(() => Boolean(releaseFirst)).toBe(true)

    await page.getByLabel('Payment transaction ID').fill('changed-before-response')
    await expect(page.getByRole('button', { name: 'Commit accepted rows' })).toHaveCount(0)
    await releaseFirst?.()
    await expect(page.getByRole('button', { name: 'Commit accepted rows' })).toHaveCount(0)

    await page.getByRole('button', { name: 'Validate files' }).click()
    await expect(page.getByRole('button', { name: 'Commit accepted rows' })).toBeVisible()
    await page.getByLabel('Invoice CSV').setInputFiles({ name: 'invoices-new.csv', mimeType: 'text/csv', buffer: Buffer.from('invoice_id,outstanding_amount\n102,100\n') })
    await expect(page.getByRole('button', { name: 'Commit accepted rows' })).toHaveCount(0)
  })

  test('locks correction fields, line controls, and reviewer while save is in flight', async ({ page }) => {
    await mockApi(page)
    let releaseCorrect: (() => Promise<void>) | undefined
    await page.route(`**/api/v1/proposals/${proposalId}/correct`, async (route) => {
      await new Promise<void>((resolve, reject) => {
        releaseCorrect = async () => {
          try {
            await route.fulfill({ json: { proposal_id: proposalId, revision: 1, status: 'PROPOSED' } })
            resolve()
          } catch (cause) {
            reject(cause)
          }
        }
      })
    })

    await openProposal(page)
    const reviewer = page.getByLabel('Reviewer name')
    const amount = page.getByLabel('Amount (MXN)').first()
    await reviewer.fill('Busy reviewer')
    await page.getByRole('button', { name: 'Edit allocation' }).first().click()
    await amount.fill('30000')
    await page.getByRole('button', { name: 'Save correction' }).click()
    await expect.poll(() => Boolean(releaseCorrect)).toBe(true)
    await expect(reviewer).toBeDisabled()
    await expect(amount).toBeDisabled()
    await expect(page.getByRole('button', { name: 'Remove cash line' })).toBeDisabled()
    await expect(page.getByRole('button', { name: 'Add line' }).first()).toBeDisabled()
    await releaseCorrect?.()
    await expect(reviewer).toBeEnabled()
  })
})
