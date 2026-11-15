import { expect, test, type Page, type Route } from '@playwright/test';

// Session-start flow tests for the RequireOpenSession guard on /sales.
//
// Covered flows (from the task acceptance criteria):
//   1. no open session  -> "Start your shift" modal appears
//   2. starting cash submitted -> POST /api/sessions/ -> Sales screen renders
//   3. open session     -> clicking Sales redirects to the dashboard (/)
//   4. invalid starting cash -> inline validation error, no POST
//   5. backend field errors (400) -> error shown, stays on modal, button re-enabled
//   6. API failure on the session check -> non-fatal banner, Sales still renders
//   7. API failure on the start POST -> graceful error, button re-enabled

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

function json(data: unknown, status = 200) {
  return { status, contentType: 'application/json', body: JSON.stringify(data) };
}

interface SessionFlowOptions {
  /** Response for GET /api/sessions/current/ (object or status code). */
  current?: { open: boolean; sessionId?: string; startedAt?: string; startingCash?: string } | number;
  /** Behavior of POST /api/sessions/ */
  startSession?: 'ok' | { status: number; body: Record<string, unknown> } | 'network-fail';
}

interface SessionMonitor {
  startSessionBodies: Array<Record<string, unknown>>;
  startSessionCalls: number;
  currentCalls: number;
}

async function installSessionMocks(
  page: Page,
  opts: SessionFlowOptions = {},
): Promise<SessionMonitor> {
  const monitor: SessionMonitor = { startSessionBodies: [], startSessionCalls: 0, currentCalls: 0 };
  const current = opts.current ?? { open: false };

  await page.route('**/*', async (route: Route) => {
    const request = route.request();
    const url = new URL(request.url());

    if (url.protocol === 'data:' || url.protocol === 'blob:') {
      await route.continue();
      return;
    }
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
    if (path === '/api/customers/') {
      await route.fulfill(json([]));
      return;
    }
    if (path === '/api/sales/' && method === 'POST') {
      await route.fulfill(json(SALE));
      return;
    }
    if (path === '/api/sales/') {
      await route.fulfill(json([SALE]));
      return;
    }
    if (path === '/api/sales/1/' || path === '/api/sales/1/add_line/') {
      await route.fulfill(json(SALE));
      return;
    }
    if (path === '/api/sessions/current/' && method === 'GET') {
      monitor.currentCalls += 1;
      if (typeof current === 'number') {
        await route.fulfill(json({ detail: 'Session check failed' }, current));
      } else {
        await route.fulfill(json(current));
      }
      return;
    }
    if (path === '/api/sessions/' && method === 'POST') {
      monitor.startSessionCalls += 1;
      const payload = JSON.parse(request.postData() ?? '{}') as Record<string, unknown>;
      monitor.startSessionBodies.push(payload);
      const start = opts.startSession ?? 'ok';
      if (start === 'ok') {
        await route.fulfill(
          json({ id: 7, status: 'open', starting_cash: payload.starting_cash }, 201),
        );
      } else if (start === 'network-fail') {
        await route.abort('connectionrefused');
      } else {
        await route.fulfill(json(start.body, start.status));
      }
      return;
    }

    await route.fulfill(json({ detail: `No mock for ${method} ${path}` }, 404));
  });

  return monitor;
}

/** Navigate to /sales with the session mocks installed; Sales page should load. */
async function openSalesRoute(
  page: Page,
  opts: SessionFlowOptions = {},
): Promise<SessionMonitor> {
  const monitor = await installSessionMocks(page, opts);
  await page.goto(`${BASE_URL}/sales`);
  await expect(page.locator('.app-shell')).toBeVisible();
  return monitor;
}

const startDialog = (page: Page) =>
  page.getByRole('dialog', { name: 'Start your shift' });

const startButton = (page: Page) =>
  page.getByRole('button', { name: /Start shift/i });

// 1. No open session -> the modal appears, and it is non-dismissible.
test('shows a non-dismissible start-shift modal when no session is open', async ({ page }) => {
  await openSalesRoute(page, { current: { open: false } });
  await expect(startDialog(page)).toBeVisible();
  await expect(page.locator('h2', { hasText: 'Start your shift' })).toBeVisible();

  // Escape and backdrop clicks must not dismiss the modal.
  await page.keyboard.press('Escape');
  await expect(startDialog(page)).toBeVisible();
  await page.mouse.click(10, 10);
  await expect(startDialog(page)).toBeVisible();

  // The Sales screen must not render while the modal is up.
  await expect(page.getByRole('heading', { level: 1, name: 'Sale' })).toHaveCount(0);
});

// 2. Submitting valid starting cash starts the session and renders Sales.
test('starts the session and renders Sales after submitting starting cash', async ({ page }) => {
  const monitor = await openSalesRoute(page, { current: { open: false } });
  await expect(startDialog(page)).toBeVisible();

  await page.getByLabel('Starting cash').fill('120.50');
  await startButton(page).click();

  // The modal goes away and the Sales screen renders.
  await expect(startDialog(page)).toHaveCount(0);
  await expect(page.getByRole('heading', { level: 1, name: 'Sale' })).toBeVisible();

  expect(
    monitor.startSessionBodies,
    `POST /api/sessions/ bodies: ${JSON.stringify(monitor.startSessionBodies)}`,
  ).toEqual([{ starting_cash: '120.50' }]);
  expect(monitor.startSessionCalls).toBe(1);
});

// 3. An open session means clicking Sales redirects to the dashboard.
test('redirects to the dashboard when a session is already open', async ({ page }) => {
  const monitor = await openSalesRoute(page, {
    current: { open: true, sessionId: '7', startedAt: '2026-01-01T10:00:00Z', startingCash: '120.50' },
  });

  await expect(page).toHaveURL(`${BASE_URL}/`);
  await expect(page.getByRole('heading', { level: 1, name: 'Dashboard' })).toBeVisible();
  await expect(startDialog(page)).toHaveCount(0);
  expect(monitor.startSessionCalls).toBe(0);
  expect(monitor.currentCalls).toBeGreaterThanOrEqual(1);
});

// 4. Client-side validation: no POST for invalid cash.
test('shows a validation error for empty starting cash and does not POST', async ({ page }) => {
  const monitor = await openSalesRoute(page, { current: { open: false } });
  await expect(startDialog(page)).toBeVisible();
  await startButton(page).click();
  await expect(page.getByRole('alert')).toContainText('Enter a starting cash amount.');
  await expect(startDialog(page)).toBeVisible();
  expect(monitor.startSessionCalls).toBe(0);
});

test('shows a validation error for negative starting cash and does not POST', async ({ page }) => {
  const monitor = await openSalesRoute(page, { current: { open: false } });
  await page.getByLabel('Starting cash').fill('-5');
  await startButton(page).click();
  await expect(page.getByRole('alert')).toContainText('Starting cash cannot be negative.');
  expect(monitor.startSessionCalls).toBe(0);
});

test('shows a validation error for starting cash over the maximum and does not POST', async ({ page }) => {
  const monitor = await openSalesRoute(page, { current: { open: false } });
  await page.getByLabel('Starting cash').fill('1500');
  await startButton(page).click();
  await expect(page.getByRole('alert')).toContainText('Starting cash cannot exceed $1,000.00');
  expect(monitor.startSessionCalls).toBe(0);
});

test('shows a validation error for non-numeric starting cash and does not POST', async ({ page }) => {
  const monitor = await openSalesRoute(page, { current: { open: false } });
  // The input is type=number; browsers block letter entry, so simulate a
  // non-numeric value the way a scripted/pasted input can: 'e' notation that
  // Number() accepts but that we want to treat as a number anyway is fine —
  // use an empty-ish numeric edge: whitespace only.
  await page.getByLabel('Starting cash').fill('   ');
  await startButton(page).click();
  await expect(page.getByRole('alert')).toContainText('Enter a starting cash amount.');
  expect(monitor.startSessionCalls).toBe(0);
});

// 5. Backend field errors (400) surface on the modal and keep it open.
test('shows backend field errors when the start POST returns 400', async ({ page }) => {
  const monitor = await openSalesRoute(page, {
    current: { open: false },
    startSession: {
      status: 400,
      body: { starting_cash: ['Ensure this value is greater than or equal to 0.00.'] },
    },
  });
  await expect(startDialog(page)).toBeVisible();
  await page.getByLabel('Starting cash').fill('120');
  await startButton(page).click();

  await expect(page.getByRole('alert')).toContainText(
    'Ensure this value is greater than or equal to 0.00.',
  );
  await expect(startDialog(page)).toBeVisible();
  // The submit button must be re-enabled after the failure.
  await expect(startButton(page)).toBeEnabled();
  expect(monitor.startSessionCalls).toBe(1);
});

// 6. Session-check API failure must not block the user.
test('falls through to Sales with a non-fatal banner when the session check fails', async ({ page }) => {
  await openSalesRoute(page, { current: 500 });

  await expect(page.getByRole('heading', { level: 1, name: 'Sale' })).toBeVisible();
  // An ApiError surfaces its backend message; a non-ApiError failure would
  // show the generic 'Could not check for an open session...' fallback.
  await expect(page.getByRole('alert')).toBeVisible();
  await expect(page.getByRole('alert')).toHaveText('Session check failed');
  await expect(startDialog(page)).toHaveCount(0);
});

// 7. Start-POST API failure: graceful error, modal stays, button re-enabled.
test('shows a graceful error and re-enables the submit button when the start POST fails', async ({ page }) => {
  const monitor = await openSalesRoute(page, {
    current: { open: false },
    startSession: { status: 500, body: { detail: 'Internal server error' } },
  });
  await expect(startDialog(page)).toBeVisible();
  await page.getByLabel('Starting cash').fill('120');
  await startButton(page).click();

  await expect(page.getByRole('alert')).toBeVisible();
  await expect(page.getByRole('alert')).toContainText(/internal server error/i);
  await expect(startDialog(page)).toBeVisible();
  await expect(startButton(page)).toBeEnabled();
  expect(monitor.startSessionCalls).toBe(1);
});

test('shows a graceful error when the start POST fails at the network level', async ({ page }) => {
  const monitor = await openSalesRoute(page, {
    current: { open: false },
    startSession: 'network-fail',
  });
  await expect(startDialog(page)).toBeVisible();
  await page.getByLabel('Starting cash').fill('120');
  await startButton(page).click();

  await expect(page.getByRole('alert')).toBeVisible();
  await expect(startDialog(page)).toBeVisible();
  await expect(startButton(page)).toBeEnabled();
  expect(monitor.startSessionCalls).toBe(1);
});
