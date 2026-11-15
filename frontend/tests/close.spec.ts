import { expect, test, type Page, type Route } from '@playwright/test';

const BASE_URL = 'http://127.0.0.1:4173';

interface NetworkMonitor {
  external: string[];
  failed: string[];
  requests: Array<{ method: string; path: string; body?: string }>;
}

const AUTH_ME = {
  user: { id: 1, username: 'maya', role: 'admin' },
  must_change_password: false,
  business_info_complete: true,
};

const BUSINESS_INFO = {
  company_name: 'eixn Corner Store',
  phone: '555-0100',
  email: 'hello@eixn.test',
  address: '12 Market Street',
  website: 'https://eixn.example',
  backup_path: '/backup',
};

const SESSION = {
  id: 7,
  cashier: 1,
  starting_cash: '100.00',
  status: 'open',
  opened_at: '2026-11-06T09:00:00Z',
  closed_at: null,
  cash_counts: {},
  cash_removed: '0.00',
  expected_cash: '0.00',
  counted_cash: '0.00',
  variance: '0.00',
  sales_total: '0.00',
  notes: '',
};

/** Rendered row indices: Bills are grouped first (0-6), then Coins (7-13). */
const INDICES: Record<string, number> = {
  ones: 0,
  twos: 1,
  fives: 2,
  tens: 3,
  twenties: 4,
  fifties: 5,
  hundreds: 6,
  pennies: 7,
  nickels: 8,
  dimes: 9,
  quarters: 10,
  half_dollars: 11,
  dollars: 12,
  two_dollars: 13,
};

function json(data: unknown, status = 200) {
  return { status, contentType: 'application/json', body: JSON.stringify(data) };
}

async function installApiMocks(page: Page): Promise<NetworkMonitor> {
  const monitor: NetworkMonitor = { external: [], failed: [], requests: [] };

  page.on('requestfailed', (request) => {
    const url = new URL(request.url());
    if (url.origin === BASE_URL) {
      monitor.failed.push(
        `${request.method()} ${url.pathname}: ${request.failure()?.errorText ?? 'failed'}`,
      );
    }
  });

  await page.route('**/*', async (route: Route) => {
    const request = route.request();
    const url = new URL(request.url());

    if (url.protocol === 'data:' || url.protocol === 'blob:') {
      await route.continue();
      return;
    }
    if (url.origin !== BASE_URL) {
      monitor.external.push(url.href);
      await route.abort('blockedbyclient');
      return;
    }
    if (!url.pathname.startsWith('/api/')) {
      await route.continue();
      return;
    }

    const path = url.pathname;
    const method = request.method().toUpperCase();
    monitor.requests.push({ method, path, body: request.postData() ?? undefined });

    if (path === '/api/auth/me/') {
      await route.fulfill(json(AUTH_ME));
      return;
    }
    if (path === '/api/business/') {
      await route.fulfill(json(BUSINESS_INFO));
      return;
    }
    if (path === '/api/sessions/') {
      if (method === 'POST') {
        await route.fulfill(json({ ...SESSION, ...JSON.parse(request.postData() || '{}') }, 201));
        return;
      }
      await route.fulfill(json([SESSION]));
      return;
    }
    const sessionClose = /^\/api\/sessions\/(\d+)\/close\/$/.exec(path);
    if (sessionClose && method === 'POST') {
      await route.fulfill(
        json({ ...SESSION, status: 'closed', counted_cash: '125.00' }),
      );
      return;
    }
    if (path === '/api/sales/') {
      await route.fulfill(json([]));
      return;
    }
    if (path === '/api/backups/trigger/') {
      await route.fulfill(json({ path: '/backup/eixnpos-20260101.sqlite3' }));
      return;
    }
    if (path === '/api/products/' || path === '/api/customers/' || path === '/api/suppliers/') {
      await route.fulfill(json([]));
      return;
    }

    await route.fulfill(json({ detail: `No mock for ${method} ${path}` }, 404));
  });

  return monitor;
}

async function openClose(page: Page): Promise<NetworkMonitor> {
  const monitor = await installApiMocks(page);
  await page.goto(`${BASE_URL}/close`);
  await expect(page.locator('.app-shell')).toBeVisible();
  await expect(page.getByRole('heading', { level: 1, name: 'Close' })).toBeVisible();
  await page.evaluate(async () => {
    await document.fonts.ready;
  });
  return monitor;
}

function expectNoNetworkIssues(monitor: NetworkMonitor) {
  expect(monitor.external, `external requests: ${monitor.external.join(', ')}`).toEqual([]);
  expect(monitor.failed, `failed requests: ${monitor.failed.join(', ')}`).toEqual([]);
}

test('shows the three flow sections in order', async ({ page }) => {
  const monitor = await openClose(page);

  const steps = page.locator('.close-flow-head [role="listitem"]');
  const stepLabels = page.locator('.close-step-label');
  await expect(steps).toHaveCount(3);
  await expect(stepLabels.nth(0)).toHaveText('Count cash');
  await expect(stepLabels.nth(1)).toHaveText('Summary');
  await expect(stepLabels.nth(2)).toHaveText('Close & backup');

  // Cash count section is the active step and visible first.
  await expect(page.getByRole('heading', { name: 'Count cash' })).toBeVisible();
  await expect(steps.nth(0)).toHaveAttribute('aria-current', 'step');

  expectNoNetworkIssues(monitor);
});

test('renders the preservation of cash-count denominations', async ({ page }) => {
  const monitor = await openClose(page);

  const groupTitles = page.locator('.close-denom-group-title');
  await expect(groupTitles.nth(0)).toHaveText('Bills');
  await expect(groupTitles.nth(1)).toHaveText('Coins');

  // 14 denominations (same key set as the backend CURRENCY_DEFS).
  const rows = page.locator('.close-denom-row');
  await expect(rows).toHaveCount(14);

  // Starting cash is shown.
  await expect(page.locator('.close-cash-stat-value').first()).toHaveText('$100.00');

  expectNoNetworkIssues(monitor);
});

test('paginates forward through summary to close & backup in order', async ({ page }) => {
  const monitor = await openClose(page);
  const steps = page.locator('.close-flow-head [role="listitem"]');

  await expect(page.getByRole('heading', { name: 'Count cash' })).toBeVisible();

  // Back is disabled on the first step.
  await expect(page.getByRole('button', { name: 'Back', exact: true })).toBeDisabled();

  // Forward to Summary.
  await page.getByRole('button', { name: 'Continue to Summary' }).click();
  await expect(page.getByRole('heading', { name: 'Session summary' })).toBeVisible();
  await expect(steps.nth(1)).toHaveAttribute('aria-current', 'step');

  // Forward to Close & backup.
  await page.getByRole('button', { name: 'Continue to Close & Backup' }).click();
  await expect(page.getByRole('heading', { name: 'Close & backup' })).toBeVisible();
  await expect(steps.nth(2)).toHaveAttribute('aria-current', 'step');

  // No further Next control on the final section; Back returns to Summary.
  await expect(page.getByRole('button', { name: 'Continue to Close & Backup' })).toHaveCount(0);
  await page.getByRole('button', { name: 'Back', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Session summary' })).toBeVisible();

  expectNoNetworkIssues(monitor);
});

test('applies cash counts to the over/short display', async ({ page }) => {
  const monitor = await openClose(page);

  // Expected cash = 100.00 starting cash, no cash sales. Counted starts at 0.
  await expect(page.locator('.close-variance')).toHaveText(/Over\/Short: \+?\$100\.00/);

  const rows = page.locator('.close-denom-row');
  await rows.nth(INDICES.twenties).locator('input').fill('2'); // +40
  await expect(page.locator('.close-variance')).toHaveText(/Over\/Short: \+?\$60\.00/);

  await rows.nth(INDICES.hundreds).locator('input').fill('2'); // +200 total => 240
  await expect(page.locator('.close-variance')).toHaveText(/Over\/Short: -\$140\.00/);

  expectNoNetworkIssues(monitor);
});

test('runs a backup and closes the session from the final section', async ({ page }) => {
  const monitor = await openClose(page);

  await page.getByRole('button', { name: 'Continue to Summary' }).click();
  await page.getByRole('button', { name: 'Continue to Close & Backup' }).click();

  await page.getByRole('button', { name: 'Backup to USB' }).click();
  await expect(page.locator('.close-backup-status')).toHaveText(
    'Backup written to /backup/eixnpos-20260101.sqlite3',
  );

  await page.getByRole('button', { name: 'Close Session & Print Z-Report' }).click();
  await expect(page.locator('.close-closed-notice')).toBeVisible();
  await expect(page.locator('.close-closed-notice')).toContainText('Session closed');

  const closeReq = monitor.requests.find(
    (r) => r.method === 'POST' && /\/api\/sessions\/\d+\/close\/$/.test(r.path),
  );
  expect(closeReq).toBeTruthy();
  const sentCounts = JSON.parse(closeReq?.body ?? '{}').counts;
  // The full cash_count denomination set is sent (all zeros here), matching
  // the backend cash_count.CURRENCY_DEFS keys, so existing counting applies.
  expect(Object.keys(sentCounts).sort()).toEqual(
    [
      'dimes', 'dollars', 'fifties', 'fives', 'half_dollars', 'hundreds',
      'nickels', 'ones', 'pennies', 'quarters', 'tens', 'twenties',
      'two_dollars', 'twos',
    ].sort(),
  );
  expect(Object.values(sentCounts).every((v) => v === 0)).toBe(true);

  expectNoNetworkIssues(monitor);
});
