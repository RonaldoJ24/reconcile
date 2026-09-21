import { expect, test } from '@playwright/test'
import path from 'node:path'

const fixture = (name: string) => path.join(__dirname, 'fixtures', name)
const expectNoHorizontalOverflow = async (page: import('@playwright/test').Page) => {
  const size = await page.evaluate(() => ({
    page: document.documentElement.scrollWidth,
    viewport: window.innerWidth,
  }))
  expect(size.page).toBeLessThanOrEqual(size.viewport)
}

test.describe('fresh import through reviewed application', () => {
  test('validates, commits, corrects, applies, reloads, exports, and reverses', async ({ page }) => {
    test.setTimeout(180_000)
    await page.goto('/')
    await page.getByRole('button', { name: 'Imports' }).click()
    await expect(page.getByRole('heading', { name: 'Match incoming payments to the right invoices' })).toBeVisible()
    await expect(page.getByText('Nothing is applied automatically')).toBeVisible()
    await expect(page.getByRole('link', { name: 'Download demo packet' })).toBeVisible()
    await expectNoHorizontalOverflow(page)

    await page.getByLabel('Bank CSV').setInputFiles(fixture('bank.csv'))
    await page.getByLabel('Invoice CSV').setInputFiles(fixture('invoices.csv'))
    await page.getByLabel('Credit CSV').setInputFiles(fixture('credits.csv'))
    await page.getByRole('button', { name: 'Validate files' }).click()
    await expect(page.getByRole('alert')).toContainText('Public demo requires all four unmodified files')
    await page.getByRole('button', { name: 'Dismiss error' }).click()
    await page.getByLabel('Payment message TXT').setInputFiles(fixture('message.txt'))
    await page.getByLabel('Message time').fill('2026-01-15T12:00:00+00:00')
    await page.getByLabel('Payment source account ID').fill('acct-1')
    await page.getByLabel('Payment transaction ID').fill('pay-54k')
    await page.getByRole('button', { name: 'Validate files' }).click()
    await expect(page.getByRole('heading', { name: /Preview complete|Batch/ })).toBeVisible()
    await expect(page.getByText('Ready to commit')).toBeVisible()

    await page.getByRole('button', { name: 'Commit accepted rows' }).click()
    await expect(page.getByText(/Job (SUCCEEDED|IDLE)/)).toBeVisible({ timeout: 30_000 })
    await page.getByRole('button', { name: 'Review queue' }).click()
    await expect(page.getByRole('heading', { name: 'Review queue' })).toBeVisible()
    const proposal = page.locator('.proposal-card').first()
    await expect(proposal).toBeVisible()
    await proposal.click()

    await expect(page.getByRole('heading', { name: 'Allocation detail' })).toBeVisible()
    await expectNoHorizontalOverflow(page)
    await page.locator('.read-only-action').getByRole('button', { name: 'Edit allocation' }).click()
    await page.getByLabel('Reviewer name').fill('E2E reviewer')
    const cashSection = page.locator('.correction-section').first()
    const creditSection = page.locator('.correction-section').nth(1)
    if (await cashSection.locator('.line-editor').count() === 0) {
      await cashSection.getByRole('button', { name: 'Add line' }).click()
      await cashSection.getByRole('button', { name: 'Add line' }).click()
      const addedCash = cashSection.locator('.line-editor')
      await addedCash.nth(0).getByLabel('Invoice ID').fill('101')
      await addedCash.nth(0).getByLabel('Amount (MXN)').fill('')
      await addedCash.nth(0).getByLabel('Amount (MXN)').pressSequentially('100')
      await addedCash.nth(1).getByLabel('Invoice ID').fill('102')
      await addedCash.nth(1).getByLabel('Amount (MXN)').fill('24000')
    }
    if (await creditSection.locator('.line-editor').count() === 0) {
      await creditSection.getByRole('button', { name: 'Add line' }).click()
      const addedCredit = creditSection.locator('.line-editor').first()
      await addedCredit.getByLabel('Credit note ID').fill('103')
      await addedCredit.getByLabel('Invoice ID').fill('102')
      await addedCredit.getByLabel('Amount (MXN)').fill('1000')
    }
    const cashAmount = page.getByLabel('Amount (MXN)').first()
    const proposalDetail = (response: import('@playwright/test').Response) => {
      const url = new URL(response.url())
      return response.request().method() === 'GET' && /^\/api\/v1\/proposals\/[^/]+$/.test(url.pathname)
    }
    const correction = (request: import('@playwright/test').Request) => {
      const url = new URL(request.url())
      return request.method() === 'POST' && /\/api\/v1\/proposals\/[^/]+\/correct$/.test(url.pathname)
    }

    expect(await cashAmount.count()).toBeGreaterThan(0)
    await cashAmount.fill('')
    await cashAmount.pressSequentially('100')
    const firstCorrection = page.waitForRequest(correction)
    const firstPersisted = page.waitForResponse(proposalDetail)
    await page.getByRole('button', { name: 'Save correction' }).click()
    const firstPayload = JSON.parse((await firstCorrection).postData() ?? '{}') as { cash?: Array<{ amount?: number }> }
    expect(firstPayload.cash?.[0]?.amount).toBe(10_000)
    const firstDetail = await (await firstPersisted).json() as { cash?: Array<{ amount?: number }> }
    expect(firstDetail.cash?.[0]?.amount).toBe(10_000)
    await expect(page.locator('.page-heading').getByText(/revision 2/i)).toBeVisible()

    await page.locator('.read-only-action').getByRole('button', { name: 'Edit allocation' }).click()
    await cashAmount.fill('100.00')
    const secondCorrection = page.waitForRequest(correction)
    const secondPersisted = page.waitForResponse(proposalDetail)
    await page.getByRole('button', { name: 'Save correction' }).click()
    const secondPayload = JSON.parse((await secondCorrection).postData() ?? '{}') as { cash?: Array<{ amount?: number }> }
    expect(secondPayload.cash?.[0]?.amount).toBe(10_000)
    const secondDetail = await (await secondPersisted).json() as { cash?: Array<{ amount?: number }> }
    expect(secondDetail.cash?.[0]?.amount).toBe(10_000)
    await expect(page.locator('.page-heading').getByText(/revision 3/i)).toBeVisible()

    await page.getByRole('button', { name: 'Apply allocation' }).click()
    const confirmation = page.getByRole('dialog', { name: 'Apply persisted allocation?' })
    await expect(confirmation).toBeVisible()
    await expect(confirmation).toContainText('Review revision 3')
    await expect(confirmation).toContainText('100.00 MXN')
    await expect(confirmation).toContainText('Projected invoice balances')
    await expect(confirmation).toContainText('does not move money in a bank account')
    await page.screenshot({ path: path.join(__dirname, '..', 'output', 'quality-demo', `confirmation-${test.info().project.name}.png`), fullPage: true })
    await confirmation.getByRole('button', { name: 'Confirm and apply' }).click()
    await expect(page.locator('.payment-card .status-pill')).toHaveText('APPLIED')
    await expect(page.getByRole('button', { name: 'Applied', exact: true })).toBeDisabled()
    const balances = page.getByRole('table', { name: 'Remaining balances' })
    await expect(balances.locator('tbody tr').filter({ hasText: '101' })).toContainText('29,900.00')
    await expect(balances.locator('tbody tr').filter({ hasText: '102' })).toContainText('0.00')
    await page.screenshot({ path: path.join(__dirname, '..', 'output', 'quality-demo', `applied-${test.info().project.name}.png`), fullPage: true })

    await page.reload()
    await page.getByRole('button', { name: 'Review queue' }).click()
    await expect(page.locator('.proposal-card').first()).toBeVisible()
    await page.locator('.proposal-card').first().click()
    await expect(page.locator('.payment-card .status-pill')).toHaveText('APPLIED')
    const reloadedBalances = page.getByRole('table', { name: 'Remaining balances' })
    await expect(reloadedBalances.locator('tbody tr').filter({ hasText: '101' })).toContainText('29,900.00')
    await expect(reloadedBalances.locator('tbody tr').filter({ hasText: '102' })).toContainText('0.00')

    const download = page.waitForEvent('download')
    await page.getByRole('link', { name: 'Download CSV' }).click()
    await expect((await download).suggestedFilename()).toMatch(/\.csv$/)

    await page.getByLabel('Reviewer name').fill('E2E reversal reviewer')
    await page.getByLabel('Reversal reason').fill('E2E reversal check')
    await page.getByRole('button', { name: 'Reverse application' }).click()
    await expect(page.locator('.payment-card .status-pill')).toHaveText('REVERSED')
    const reversedBalances = page.getByRole('table', { name: 'Remaining balances' })
    await expect(reversedBalances.locator('tbody tr').filter({ hasText: '101' })).toContainText('30,000.00')
    await expect(reversedBalances.locator('tbody tr').filter({ hasText: '102' })).toContainText('25,000.00')
  })
})
