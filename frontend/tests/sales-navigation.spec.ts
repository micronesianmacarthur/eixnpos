/**
 * Regression tests for the /sales navigation guard (RequireOpenSession).
 *
 * Covers the scenarios from t_43790f5e / the bug fixed in wt/t_f004bb0d:
 *   1. An open session exists -> clicking 'Sales' opens the Sales page
 *      (this is the bug scenario: the old guard redirected to the dashboard).
 *   2. No open session and starting cash NOT completed -> dismissing the
 *      starting-cash modal (Escape / backdrop / close button) redirects to
 *      the dashboard '/'.
 *   3. No open session but starting cash completed -> POST /api/sessions/
 *      succeeds and the Sales page opens.
 *
 * Edge cases: invalid starting-cash input (empty / negative / over the cap),
 * a rejected session start, and a failed session check (non-fatal banner,
 * Sales still renders).
 *
 * The repo has no frontend unit-test runner; these are API-mocked
 * integration tests in the existing Playwright framework, matching the
 * style of tests/sales.spec.ts.
 */
import { expect, test, type Locator, type Page, type Route } from '@playwright/test';

const BASE_URL =
  process.env.EIXNPOS_BASE_URL ?? 'http://127.0.0.1:4173';

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
  website: '',
};

const PRODUCT = {
  id: 1,
  name: 'Widget',
  price: '10.00',
  quantity: 5,
  is_low_stock: false,
  active: true,
};

const SALE = {
  id: 1,
  number: 1001,
  cashier: 1,
  customer: null,
  status: 'open',
  subtotal: '0.00',
  discount_total: '0.00',
  total: '0.00',
  lines: [],
  payments: [],
  created_at: '2026-01-01T10:00:00Z',
};

const OPEN_SESSION = { open: true, sessionId: '7', startedAt: '2026-01-01T09:00:00Z', startingCash: '100.00' };
const NO_SESSION = { open: false };

function json(data: unknown, status = 200) {
  return { status, contentType: 'application/json', body: JSON.stringify(data) };
}

interface SessionMonitor {
  currentRequests: number;
  startBodies: Array<Record<string, unknown>>;
  /** When `holdStart` is set, the pending start route is parked here. */
  pendingStart: Route | null;
  /** Fulfill the parked start POST (requires `holdStart`). */
  releaseStart(data?: Record<string, unknown>): Promise<void>;
}

interface SessionOptions {
  /** Response for GET /api/sessions/current/. */
  current: Record<string, unknown> | { fail: number };
  /** Response for POST /api/sessions/ (null -> fail with 400). */
  start?: Record<string, unknown> | null;
  /** Park the POST /api/sessions/ response so the test can assert no
   *  dismissal happens while the request is in flight, then release it. */
  holdStart?: boolean;
}

async function installSessionMocks(page: Page, opts: SessionOptions): Promise<SessionMonitor> {
  const monitor: SessionMonitor = {
    currentRequests: 0,
    startBodies: [],
    pendingStart: null,
    releaseStart: async (data) => {
      const route = monitor.pendingStart;
      if (!route) throw new Error('releaseStart() called but no start POST is parked');
      monitor.pendingStart = null;
      await route.fulfill(json(data ?? OPEN_SESSION));
    },
  };

  await page.route('**/*', async (route: Route) => {
    const request = route.request();
    const url = new URL(request.url());

    if (url.origin !== BASE_URL) {
      await route.abort('blockedbyclient');
      return;
    }
    if (!url.pathname.startsWith('/api/')) {
      await route.continue();
      return;
    }

    const path = url.pathname;
    const method = request.method().toUpperCase();

    if (path === '/api/auth/me/') {
      await route.fulfill(json(AUTH_ME));
      return;
    }
    if (path === '/api/business/') {
      await route.fulfill(json(BUSINESS_INFO));
      return;
    }
    if (path === '/api/products/') {
      await route.fulfill(json([PRODUCT]));
      return;
    }
    if (path === '/api/sales/') {
      await route.fulfill(json(method === 'POST' ? SALE : [SALE]));
      return;
    }
    if (path === '/api/sessions/current/') {
      monitor.currentRequests += 1;
      if ('fail' in opts.current) {
        await route.abort('failed');
      } else {
        await route.fulfill(json(opts.current));
      }
      return;
    }
    if (path === '/api/sessions/' && method === 'POST') {
      const payload = JSON.parse(request.postData() ?? '{}') as Record<string, unknown>;
      monitor.startBodies.push(payload);
      if (opts.holdStart) {
        // Park the response so the test can exercise dismissal attempts while
        // the start request is still in flight, then release it.
        monitor.pendingStart = route;
        return;
      }
      if (opts.start === null) {
        await route.fulfill(
          json({ starting_cash: [{ message: 'A session is already open.', code: 'invalid' }] }, 400),
        );
      } else {
        await route.fulfill(json(opts.start ?? OPEN_SESSION));
      }
      return;
    }

    await route.fulfill(json({ detail: `No mock for ${method} ${path}` }, 404));
  });

  return monitor;
}

async function gotoDashboard(page: Page) {
  await page.goto(`${BASE_URL}/`);
  await expect(page.getByRole('heading', { level: 1, name: 'Dashboard' })).toBeVisible();
}

async function clickSalesInSidebar(page: Page) {
  await page.getByRole('link', { name: 'Sales', exact: true }).click();
}

const startShiftModal = (page: Page): Locator => page.getByRole('dialog', { name: 'Start your shift' });

test('open session exists: clicking Sales opens the Sales page (bug scenario)', async ({ page }) => {
  const monitor = await installSessionMocks(page, { current: OPEN_SESSION });
  await gotoDashboard(page);

  await clickSalesInSidebar(page);

  // The guard must NOT redirect back to the dashboard when a session is open.
  await expect(page).toHaveURL(`${BASE_URL}/sales`);
  await expect(page.getByRole('heading', { level: 1, name: 'Sale' })).toBeVisible();
  await expect(startShiftModal(page)).toHaveCount(0);
  // The guard asked the backend exactly once about the current session.
  expect(monitor.currentRequests).toBe(1);
});

test('open session exists: navigating directly to /sales keeps the Sales page', async ({ page }) => {
  await installSessionMocks(page, { current: OPEN_SESSION });

  await page.goto(`${BASE_URL}/sales`);

  await expect(page).toHaveURL(`${BASE_URL}/sales`);
  await expect(page.getByRole('heading', { level: 1, name: 'Sale' })).toBeVisible();
  await expect(startShiftModal(page)).toHaveCount(0);
});

test('no open session, cash not completed: Escape dismisses the modal and redirects to the dashboard', async ({ page }) => {
  await installSessionMocks(page, { current: NO_SESSION });

  await page.goto(`${BASE_URL}/sales`);
  const modal = startShiftModal(page);
  await expect(modal).toBeVisible();

  await modal.press('Escape');

  await expect(page).toHaveURL(`${BASE_URL}/`);
  await expect(page.getByRole('heading', { level: 1, name: 'Dashboard' })).toBeVisible();
});

test('no open session: Escape dismisses regardless of where focus sits (focus on BODY)', async ({ page }) => {
  await installSessionMocks(page, { current: NO_SESSION });

  await page.goto(`${BASE_URL}/sales`);
  const modal = startShiftModal(page);
  await expect(modal).toBeVisible();

  // Click the helper text (a non-focusable element) so focus leaves the
  // autofocused input and falls back to the document body — the exact
  // scenario that broke the old onKeyDown-on-the-dialog-div handler.
  await modal.getByText(/There is no open session/).click();
  await expect
    .poll(() => page.evaluate(() => document.activeElement?.tagName))
    .toBe('BODY');

  await page.keyboard.press('Escape');

  await expect(page).toHaveURL(`${BASE_URL}/`);
  await expect(page.getByRole('heading', { level: 1, name: 'Dashboard' })).toBeVisible();
});

test('no open session: no dismissal while the start request is pending (Escape, backdrop, and close all ignored)', async ({ page }) => {
  const monitor = await installSessionMocks(page, { current: NO_SESSION, holdStart: true });

  await page.goto(`${BASE_URL}/sales`);
  const modal = startShiftModal(page);
  await expect(modal).toBeVisible();

  // Submit the form; the POST is now parked in-flight by the mock.
  await page.getByLabel('Starting cash').fill('100.00');
  await modal.getByRole('button', { name: 'Start shift' }).click();
  await expect(monitor.pendingStart).toBeTruthy();

  // Escape while starting: must stay put (not redirect to /).
  await page.keyboard.press('Escape');
  await expect(page).toHaveURL(`${BASE_URL}/sales`);
  await expect(modal).toBeVisible();

  // Backdrop mousedown while starting: must stay put.
  await page.locator('.mm-modal-backdrop').click({ position: { x: 4, y: 4 } });
  await expect(modal).toBeVisible();
  await expect(page).toHaveURL(`${BASE_URL}/sales`);
  // The close button is disabled while starting.
  await expect(page.getByRole('button', { name: 'Close' })).toBeDisabled();

  // Release the response: the normal contract resumes — successful POST opens Sales.
  await monitor.releaseStart();
  await expect(page).toHaveURL(`${BASE_URL}/sales`);
  await expect(page.getByRole('heading', { level: 1, name: 'Sale' })).toBeVisible();
  await expect(startShiftModal(page)).toHaveCount(0);
  expect(monitor.startBodies).toEqual([{ starting_cash: '100.00' }]);
});

test('no open session: clicking the backdrop redirects to the dashboard', async ({ page }) => {
  await installSessionMocks(page, { current: NO_SESSION });

  await page.goto(`${BASE_URL}/sales`);
  const modal = startShiftModal(page);
  await expect(modal).toBeVisible();

  // Click the backdrop itself (outside the dialog box).
  await page.locator('.mm-modal-backdrop').click({ position: { x: 4, y: 4 } });

  await expect(page).toHaveURL(`${BASE_URL}/`);
  await expect(page.getByRole('heading', { level: 1, name: 'Dashboard' })).toBeVisible();
});

test('no open session: the close button redirects to the dashboard', async ({ page }) => {
  await installSessionMocks(page, { current: NO_SESSION });

  await page.goto(`${BASE_URL}/sales`);
  await expect(startShiftModal(page)).toBeVisible();

  await page.getByRole('button', { name: 'Close' }).click();

  await expect(page).toHaveURL(`${BASE_URL}/`);
});

test('no open session, starting cash completed: Sales page opens after starting the shift', async ({ page }) => {
  const monitor = await installSessionMocks(page, { current: NO_SESSION, start: OPEN_SESSION });

  await page.goto(`${BASE_URL}/sales`);
  const modal = startShiftModal(page);
  await expect(modal).toBeVisible();

  await page.getByLabel('Starting cash').fill('100.00');
  await modal.getByRole('button', { name: 'Start shift' }).click();

  await expect(page).toHaveURL(`${BASE_URL}/sales`);
  await expect(page.getByRole('heading', { level: 1, name: 'Sale' })).toBeVisible();
  await expect(startShiftModal(page)).toHaveCount(0);
  expect(monitor.startBodies).toEqual([{ starting_cash: '100.00' }]);
});

test('starting cash validation: empty input is rejected without a session POST', async ({ page }) => {
  const monitor = await installSessionMocks(page, { current: NO_SESSION, start: OPEN_SESSION });

  await page.goto(`${BASE_URL}/sales`);
  const modal = startShiftModal(page);
  await expect(modal).toBeVisible();

  await modal.getByRole('button', { name: 'Start shift' }).click();

  await expect(page.getByRole('alert')).toContainText('Enter a starting cash amount.');
  expect(monitor.startBodies).toEqual([]);
  // Still on /sales with the modal open — not redirected.
  await expect(page).toHaveURL(`${BASE_URL}/sales`);
  await expect(modal).toBeVisible();
});

test('starting cash validation: negative and over-cap amounts are rejected', async ({ page }) => {
  const monitor = await installSessionMocks(page, { current: NO_SESSION, start: OPEN_SESSION });

  await page.goto(`${BASE_URL}/sales`);
  const modal = startShiftModal(page);
  const cash = page.getByLabel('Starting cash');
  const start = modal.getByRole('button', { name: 'Start shift' });

  await cash.fill('-5');
  await start.click();
  await expect(page.getByRole('alert')).toContainText('cannot be negative');
  expect(monitor.startBodies).toEqual([]);

  await cash.fill('1001');
  await start.click();
  await expect(page.getByRole('alert')).toContainText('cannot exceed $1,000.00');
  expect(monitor.startBodies).toEqual([]);
});

test('starting cash rejected by the backend: error is shown and the cashier stays on /sales', async ({ page }) => {
  await installSessionMocks(page, { current: NO_SESSION, start: null });

  await page.goto(`${BASE_URL}/sales`);
  const modal = startShiftModal(page);
  await expect(modal).toBeVisible();

  await page.getByLabel('Starting cash').fill('50.00');
  await modal.getByRole('button', { name: 'Start shift' }).click();

  await expect(page.getByRole('alert')).toContainText('A session is already open.');
  await expect(page).toHaveURL(`${BASE_URL}/sales`);
  await expect(modal).toBeVisible();
});

test('session check fails: Sales still renders with a non-fatal banner', async ({ page }) => {
  await installSessionMocks(page, { current: { fail: 1 } });

  await page.goto(`${BASE_URL}/sales`);

  // A failed check must never trap or redirect the cashier.
  await expect(page).toHaveURL(`${BASE_URL}/sales`);
  await expect(page.getByRole('heading', { level: 1, name: 'Sale' })).toBeVisible();
  await expect(startShiftModal(page)).toHaveCount(0);
  await expect(page.locator('.mm-alert--banner')).toBeVisible();
});
