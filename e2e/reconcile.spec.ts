import { expect, test } from '@playwright/test'
import path from 'node:path'

const fixture = (name: string) => path.join(__dirname, 'fixtures', name)

test.describe('fresh import through reviewed application', () => {
  test('validates, commits, corrects, applies, reloads, exports, and reverses', async ({ page }) => {
    await page.goto('/')
    await expect(page.getByRole('heading', { name: 'Imports', exact: true })).toBeVisible()

    await page.getByLabel('Bank CSV').setInputFiles(fixture('bank.csv'))
    await page.getByLabel('Invoice CSV').setInputFiles(fixture('invoices.csv'))
    await page.getByLabel('Credit CSV').setInputFiles(fixture('credits.csv'))
    await page.getByLabel('Payment message TXT').setInputFiles(fixture('message.txt'))
    await page.getByLabel('Message time').fill('2026-09-14T10:00:00-06:00')
    await page.getByLabel('Payment source account ID').fill('acct-1')
    await page.getByLabel('Payment transaction ID').fill('pay-1')
    await page.getByRole('button', { name: 'Validate files' }).click()
    await expect(page.getByRole('heading', { name: /Preview complete|Batch/ })).toBeVisible()
    await expect(page.getByText('Ready to commit')).toBeVisible()

    await page.getByRole('button', { name: 'Commit accepted rows' }).click()
    await page.getByRole('button', { name: 'Review queue' }).click()
    await expect(page.getByRole('heading', { name: 'Review queue' })).toBeVisible()
    const proposal = page.locator('.proposal-card').first()
    await expect(proposal).toBeVisible()
    await proposal.click()

    await expect(page.getByRole('heading', { name: 'Allocation detail' })).toBeVisible()
    await page.getByLabel('Reviewer name').fill('E2E reviewer')
    const cashAmount = page.getByLabel('Amount (MXN)').first()
    if (await cashAmount.count()) await cashAmount.fill('30000.00')
    await page.getByRole('button', { name: 'Save correction' }).click()
    await expect(page.getByText(/revision 2|revision 1/i)).toBeVisible()

    await page.getByRole('button', { name: 'Apply allocation' }).click()
    await expect(page.getByText('APPLIED')).toBeVisible()

    await page.reload()
    await page.getByRole('button', { name: 'Review queue' }).click()
    await expect(page.locator('.proposal-card').first()).toBeVisible()
    await page.locator('.proposal-card').first().click()
    await expect(page.getByText('APPLIED')).toBeVisible()

    const download = page.waitForEvent('download')
    await page.getByRole('link', { name: 'Download CSV' }).click()
    await expect((await download).suggestedFilename()).toMatch(/\.csv$/)

    await page.getByLabel('Reversal reason').fill('E2E reversal check')
    await page.getByRole('button', { name: 'Reverse application' }).click()
    await expect(page.getByText('REVERSED')).toBeVisible()
  })
})
