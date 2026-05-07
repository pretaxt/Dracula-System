import { test, expect } from '@playwright/test'

test.describe('Login page (no backend required)', () => {
  test('renders DRACULA brand and Sign In button', async ({ page }) => {
    await page.goto('/login')
    await expect(page.getByText('DRACULA').first()).toBeVisible()
    await expect(page.getByText('ARBITRAGE SYSTEM').first()).toBeVisible()
    await expect(page.getByRole('button', { name: /sign in/i })).toBeVisible()
  })

  test('Sign In button uses blood accent color', async ({ page }) => {
    await page.goto('/login')
    const btn = page.getByRole('button', { name: /sign in/i })
    await expect(btn).toBeVisible()
    const bg = await btn.evaluate((el) => getComputedStyle(el as HTMLElement).backgroundColor)
    // accent-blood (dark) #e34058 → rgb(227,64,88)
    // light override #b81a30 → rgb(184,26,48)
    expect(bg).toMatch(/rgb\(\s*(227|184)\s*,/)
  })

  test('html has data-theme attribute set', async ({ page }) => {
    await page.goto('/login')
    const theme = await page.evaluate(() => document.documentElement.getAttribute('data-theme'))
    expect(['dark', 'light']).toContain(theme)
  })
})

test.describe('Responsive smoke', () => {
  test('login page does not horizontal-overflow', async ({ page }) => {
    await page.goto('/login')
    const overflow = await page.evaluate(() => {
      const html = document.documentElement
      return html.scrollWidth - html.clientWidth
    })
    expect(overflow).toBeLessThanOrEqual(2)
  })
})

test.describe('Strategy detail — unknown id (auth-bypass via fake token)', () => {
  test('shows "策略不存在" message for nonexistent id', async ({ page }) => {
    await page.addInitScript(() => {
      const future = new Date(Date.now() + 60 * 60 * 1000).toISOString()
      window.localStorage.setItem(
        'dracula-auth',
        JSON.stringify({
          state: { token: 'e2e-fake-token', expiresAt: future },
          version: 0,
        }),
      )
    })
    await page.goto('/strategies/nonexistent-strategy-id')
    await expect(page.getByText('策略不存在').first()).toBeVisible({ timeout: 10_000 })
    await expect(page.getByText('返回策略中心').first()).toBeVisible()
  })
})
