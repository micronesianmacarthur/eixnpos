import { expect, test, type Page, type Route } from '@playwright/test';

const BASE_URL =
  process.env.EIXNPOS_BASE_URL ?? 'http://127.0.0.1:4173';

interface NetworkMonitor {
  external: string[];
  failed: string[];
  /** Parsed JSON bodies of POST /api/sessions/, in order. */
  createSessionBodies: Array<Record<string, unknown>>;
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
  website: '',
};

const OPEN_SESSION = {
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

const PRODUCT = {
  id: 1,
  name: 'Widget',
  price: '10.00',
  quantity: 5,
  is_low_stock: false,
  active: true,
};

function json(data: unknown, status = 200) {
  return { status, contentType: 'application/json', body: JSON.stringify(data) };
}

/**
 * Installs mocks for the endpoints the guard path touches. `sessions` controls
 * what GET /api/sessions/ returns; a created session is echoed back on POST.
 */
async function installApiMocks(
  page: Page,
  options: { sessions?: Array<typeof OPEN_SESSION> } = {},
): Promise<NetworkMonitor> {
  const monitor: NetworkMonitor = { external: [], failed: [], createSessionBodies: [] };
  const sessionList = options.sessions ?? [OPEN_SESSION];

  page.on('requestfailed', (request) => {
    const url = new URL(request.url());
    if (url.origin === BASE_URL) {
      monitor.failed.push(`${request.method()} ${url.pathname}: ${request.failure()?.errorText ?? 'failed'}`);
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

    if (path === '/api/auth/me/') {
      await route.fulfill(json(AUTH_ME));
      return;
    }
    if (path === '/api/business/') {
      await route.fulfill(json(BUSINESS_INFO));
      return;
    }
    if (path === '/api/sessions/' && method === 'POST') {
      const payload = JSON.parse(request.postData() ?? '{}') as Record<string, unknown>;
      monitor.createSessionBodies.push(payload);
      await route.fulfill(json({ ...OPEN_SESSION, ...payload }, 201));
      return;
    }
    if (path === '/api/sessions/') {
      await route.fulfill(json(sessionList));
      return;
    }
    if (path === '/api/products/') {
      await route.fulfill(json([PRODUCT]));
      return;
    }
    if (path === '/api/sales/') {
      await route.fulfill(json([]));
      return;
    }
    if (path === '/api/customers/' || path === '/api/suppliers/') {
      await route.fulfill(json([]));
      return;
    }

    await route.fulfill(json({ detail: `No mock for ${method} ${path}` }, 404));
  });

  return monitor;
}

function expectNoNetworkIssues(monitor: NetworkMonitor) {
  expect(monitor.external, `external requests: ${monitor.external.join(', ')}`).toEqual([]);
  expect(monitor.failed, `failed requests: ${monitor.failed.join(', ')}`).toEqual([]);
}

/** The sidebar contains dashboard, sales, sales-manager, customers, close, etc. */
async function clickNav(page: Page, label: string) {
  await page.getByRole('link', { name: label, exact: true }).click();
}

/**
 * With no open session the guard must show the StartSessionModal instead of
 * opening the requested transaction screen.
 */
test('no open session: clicking Sales opens the modal and not the sales screen', async ({
  page,
}) => {
  const monitor = await installApiMocks(page, { sessions: [] });
  await page.goto(`${BASE_URL}/`);
  await expect(page.getByRole('heading', { level: 1, name: 'Dashboard' })).toBeVisible();

  await clickNav(page, 'Sales');

  // The guard mounts on /sales, finds no session, and renders the modal in
  // place of the target screen — the sales page never opens.
  await expect(page.getByRole('dialog')).toBeVisible();
  await expect(page.getByRole('dialog')).toContainText('Start session');
  await expect(page.getByRole('heading', { level: 1, name: 'Sale' })).toHaveCount(0);

  expectNoNetworkIssues(monitor);
});

test('no open session: dismissing the modal stays on the current screen', async ({ page }) => {
  const monitor = await installApiMocks(page, { sessions: [] });
  await page.goto(`${BASE_URL}/`);
  await expect(page.getByRole('heading', { level: 1, name: 'Dashboard' })).toBeVisible();

  await clickNav(page, 'Sales');
  await expect(page.getByRole('dialog')).toBeVisible();

  await page.getByRole('button', { name: 'Cancel' }).click();

  // Cancel returns to the dashboard (the screen we came from); the sales
  // screen stays closed.
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(page.getByRole('heading', { level: 1, name: 'Dashboard' })).toBeVisible();
  await expect(page.getByRole('heading', { level: 1, name: 'Sale' })).toHaveCount(0);
  await expect(page).toHaveURL(`${BASE_URL}/`);

  expectNoNetworkIssues(monitor);
});

test('no open session: starting a session continues the requested navigation', async ({
  page,
}) => {
  const monitor = await installApiMocks(page, { sessions: [] });
  await page.goto(`${BASE_URL}/`);
  await expect(page.getByRole('heading', { level: 1, name: 'Dashboard' })).toBeVisible();

  await clickNav(page, 'Sales');
  await expect(page.getByRole('dialog')).toBeVisible();

  await page.getByLabel('Starting cash').fill('25.00');
  await page.getByRole('button', { name: 'Start Session' }).click();

  // Starting the session creates it and proceeds to the originally requested
  // screen (Sales), now that a session is open.
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(page.getByRole('heading', { level: 1, name: 'Sale' })).toBeVisible();
  expect(monitor.createSessionBodies).toEqual([{ starting_cash: '25.00' }]);

  expectNoNetworkIssues(monitor);
});

test('open session: navigation to Sales works normally', async ({ page }) => {
  const monitor = await installApiMocks(page, { sessions: [OPEN_SESSION] });
  await page.goto(`${BASE_URL}/`);
  await expect(page.getByRole('heading', { level: 1, name: 'Dashboard' })).toBeVisible();

  await clickNav(page, 'Sales');

  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(page.getByRole('heading', { level: 1, name: 'Sale' })).toBeVisible();
  expect(monitor.createSessionBodies).toEqual([]);

  expectNoNetworkIssues(monitor);
});

test('no open session: Sales Manager and Close are gated too', async ({ page }) => {
  const monitor = await installApiMocks(page, { sessions: [] });
  await page.goto(`${BASE_URL}/`);
  await expect(page.getByRole('heading', { level: 1, name: 'Dashboard' })).toBeVisible();

  for (const label of ['Sales Manager', 'Close']) {
    await clickNav(page, label);
    await expect(page.getByRole('dialog')).toBeVisible();
    await page.getByRole('button', { name: 'Cancel' }).click();
    await expect(page.getByRole('dialog')).toHaveCount(0);
  }

  expectNoNetworkIssues(monitor);
});
