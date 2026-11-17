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

/**
 * Structured result returned by the pandas-backed summary endpoint
 * (`compute_session_summary`). Mirrors the backend contract so the UI renders
 * revenue and profit/loss from the computation rather than client-side math.
 */
const SESSION_SUMMARY = {
  session: {
    id: 7,
    cashier: 'maya',
    status: 'open',
    opened_at: '2026-11-06T09:00:00Z',
    closed_at: null,
  },
  summary: {
    sale_count: 4,
    item_count: 6,
    gross_sales: '440.00',
    discounts: '20.00',
    tax_total: '10.00',
    revenue: '430.00',
    cost_of_goods: '210.00',
    profit_loss: '220.00',
    profit_margin: 0.5116,
    by_payment: { cash: '300.00', card: '130.00' },
    by_category: { Snacks: '430.00' },
  },
  warnings: { invalid_sale_amounts: 0, invalid_line_amounts: 0, invalid_payment_amounts: 0 },
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

async function installApiMocks(
  page: Page,
  options: { failSummary?: boolean; noOpenSession?: boolean } = {},
): Promise<NetworkMonitor> {
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
    if (path === '/api/sessions/current/') {
      if (options.noOpenSession) {
        await route.fulfill(json({ open: false }));
        return;
      }
      await route.fulfill(
        json({
          open: true,
          sessionId: String(SESSION.id),
          startedAt: SESSION.opened_at,
          startingCash: SESSION.starting_cash,
          register: 1,
          day: '2026-11-06',
        }),
      );
      return;
    }
    if (path === `/api/sessions/${SESSION.id}/`) {
      await route.fulfill(json(SESSION));
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
    const sessionZreport = /^\/api\/sessions\/(\d+)\/zreport\/$/.exec(path);
    if (sessionZreport) {
      await route.fulfill({
        status: 200,
        contentType: 'application/pdf',
        body: '%PDF-1.4 test zreport',
      });
      return;
    }
    const sessionSummary = /^\/api\/sessions\/(\d+)\/summary\/$/.exec(path);
    if (sessionSummary && method === 'GET') {
      if (options.failSummary) {
        await route.fulfill(json({ detail: 'summary unavailable' }, 500));
        return;
      }
      await route.fulfill(json(SESSION_SUMMARY));
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

test('provides a print link to the Z-report PDF after close', async ({ page, context }) => {
  const monitor = await openClose(page);

  await page.getByRole('button', { name: 'Continue to Summary' }).click();
  await page.getByRole('button', { name: 'Continue to Close & Backup' }).click();
  await page.getByRole('button', { name: 'Close Session & Print Z-Report' }).click();
  await expect(page.locator('.close-closed-notice')).toBeVisible();

  const popupPromise = context.waitForEvent('page');
  await page.getByRole('button', { name: 'Print Z-report (PDF)' }).click();
  const popup = await popupPromise;
  await expect.poll(() => popup.url()).toContain('/api/sessions/7/zreport/');
  await popup.close();
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

test('summary shows revenue and profit/loss from the computed summary', async ({ page }) => {
  const monitor = await openClose(page);

  await page.getByRole('button', { name: 'Continue to Summary' }).click();
  await expect(page.getByRole('heading', { name: 'Session summary' })).toBeVisible();

  // Summary section reflects the structured result of compute_session_summary.
  await expect(page.locator('.close-summary-value')).toHaveText([
    '4',       // Sales (sale_count)
    '$440.00', // Gross sales
    '$430.00', // Revenue
    '$210.00', // Cost of goods
    '$220.00', // Profit / Loss
    '51.2%',   // Margin (0.5116)
  ]);

  const summaryPanel = page.locator('.close-summary-grid');
  await expect(summaryPanel.getByText('Profit / Loss')).toBeVisible();
  await expect(summaryPanel.getByText('51.2%')).toBeVisible();

  // By-payment breakdown comes from the computed summary too.
  await expect(page.locator('.close-summary-section').first()).toContainText('Cash');
  await expect(page.locator('.close-summary-section').first()).toContainText('$300.00');
  await expect(page.locator('.close-summary-section').first()).toContainText('Card');
  await expect(page.locator('.close-summary-section').first()).toContainText('$130.00');

  // Cash sales figure on the count step derives from summary.by_payment.
  await page.getByRole('button', { name: 'Back', exact: true }).click();
  await expect(page.locator('.close-cash-stat-value').nth(1)).toHaveText('$300.00');
  await expect(page.locator('.close-variance')).toHaveText(/Over\/Short: \+?\$400\.00/);

  expectNoNetworkIssues(monitor);
});

test('summary shows an error state when the summary endpoint fails', async ({ page }) => {
  const monitor = await installApiMocks(page, { failSummary: true });

  await page.goto(`${BASE_URL}/close`);
  await expect(page.getByRole('heading', { level: 1, name: 'Close' })).toBeVisible();
  await page.evaluate(async () => {
    await document.fonts.ready;
  });
  await page.getByRole('button', { name: 'Continue to Summary' }).click();

  // The summary section surfaces the load error instead of stale data.
  await expect(page.locator('.mm-alert--danger')).toContainText('summary unavailable');
  await expect(page.getByText('$430.00')).toHaveCount(0);

  expectNoNetworkIssues(monitor);
});

test('applies cash counts to the over/short display', async ({ page }) => {
  const monitor = await openClose(page);

  // Expected cash = 100.00 starting cash + 300.00 cash sales (summary
  // by_payment.cash) = 400.00. Counted starts at 0.
  await expect(page.locator('.close-variance')).toHaveText(/Over\/Short: \+?\$400\.00/);

  const rows = page.locator('.close-denom-row');
  await rows.nth(INDICES.twenties).locator('input').fill('2'); // +40
  await expect(page.locator('.close-variance')).toHaveText(/Over\/Short: \+?\$360\.00/);

  await rows.nth(INDICES.hundreds).locator('input').fill('2'); // +200 total => 240
  await expect(page.locator('.close-variance')).toHaveText(/Over\/Short: \+?\$160\.00/);

  expectNoNetworkIssues(monitor);
});

test('closes the session and requires starting cash before the next session starts', async ({
  page,
}) => {
  const monitor = await openClose(page);

  // Drive to the close & backup section and close the session.
  await page.getByRole('button', { name: 'Continue to Summary' }).click();
  await page.getByRole('button', { name: 'Continue to Close & Backup' }).click();
  await page.getByRole('button', { name: 'Close Session & Print Z-Report' }).click();
  await expect(page.locator('.close-closed-notice')).toBeVisible();

  // The next-session prompt appears and its start button is blocked until
  // a valid starting cash value is entered.
  const nextForm = page.getByRole('form', { name: 'Start next session' });
  await expect(nextForm).toBeVisible();
  const startBtn = nextForm.getByRole('button', { name: 'Start Next Session' });
  await expect(startBtn).toBeDisabled();

  // Type a starting cash value; the start button unlocks.
  await page.getByLabel('Starting cash').fill('150.00');
  await expect(startBtn).toBeEnabled();
  await startBtn.click();

  // The entered value is posted and used for the next session.
  await expect(page.locator('.close-next-notice')).toContainText('$150.00');
  const createReq = monitor.requests.find(
    (r) => r.method === 'POST' && r.path === '/api/sessions/',
  );
  expect(createReq).toBeTruthy();
  expect(JSON.parse(createReq?.body ?? '{}')).toEqual({ starting_cash: '150.00' });

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

test('two accessible registers: only the non-default one open -> guard and ClosePage agree (no wrong-session close, no redirect loop)', async ({
  page,
}) => {
  // Scenario from ADR-001 acceptance / review S2: the cashier can operate two
  // registers (default = lowest-pk register 1, sibling = register 2). Only the
  // sibling register 2 holds an open shift. `GET /api/sessions/current/` (no
  // register) resolves the *default* register 1, which has NO open session, so
  // it reports `open: false`.
  const monitor = await installApiMocks(page, { noOpenSession: true });

  // The /close route is behind RequireOpenSession, which resolves the same
  // default register/day. With no open session there it must show the
  // start-shift modal (not redirect) — ClosePage must never mount against a
  // sibling's shift. If the cashier dismisses the modal, they land on the
  // dashboard.
  await page.goto(`${BASE_URL}/close`);

  // The start-shift modal should appear (not a redirect to dashboard).
  await expect(page.getByRole('heading', { level: 2, name: 'Start your shift' })).toBeVisible();

  // Dismiss the modal — this should redirect to the dashboard.
  await page.getByRole('button', { name: 'Close' }).click();
  await expect(page.getByRole('heading', { level: 1, name: 'Dashboard' })).toBeVisible();

  // A redirect loop would keep bouncing; assert the dashboard stays put and
  // no session close was ever attempted (no POST to /api/sessions/<id>/close/).
  await expect(page.getByRole('heading', { level: 1, name: 'Dashboard' })).toBeVisible();
  await expect(page.getByRole('heading', { level: 1, name: 'Close' })).toHaveCount(0);

  const anyClose = monitor.requests.some(
    (r) => r.method === 'POST' && /\/api\/sessions\/\d+\/close\/$/.test(r.path),
  );
  expect(anyClose, 'must never close a sibling register session').toBe(false);

  // ClosePage must not even scan the session list for "an open one".
  expect(
    monitor.requests.some((r) => r.method === 'GET' && r.path === '/api/sessions/'),
  ).toBe(false);

  expectNoNetworkIssues(monitor);
});
