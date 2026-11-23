import { expect, test, type Page } from '@playwright/test';

const BASE_URL = process.env.EIXNPOS_BASE_URL ?? 'http://127.0.0.1:4173';

interface NetworkMonitor {
  external: string[];
  failed: string[];
}

const SALES_SEED = [
  {
    id: 1,
    number: 1001,
    cashier: 1,
    cashier_username: 'jane',
    customer: null,
    status: 'completed',
    subtotal: '50.00',
    discount_total: '0.00',
    total: '50.00',
    lines: [
      {
        id: 11,
        product: 1,
        product_name: 'Widget',
        quantity: 5,
        unit_price: '10.00',
        discount_type: 'none',
        discount_value: '0.00',
        discount_amount: '0.00',
        discount_authorizer: null,
        line_total: '50.00',
      },
    ],
    payments: [
      { id: 21, method: 'cash', amount: '50.00', tendered: '60.00', change_due: '10.00', manager_approved: true, created_at: '2026-01-01T10:00:00Z' },
    ],
    note: '',
    needs_review: false,
    created_at: '2026-01-01T10:00:00Z',
    completed_at: '2026-01-01T10:05:00Z',
  },
  {
    id: 2,
    number: 1002,
    cashier: 2,
    cashier_username: 'kofi',
    customer: null,
    status: 'open',
    subtotal: '30.00',
    discount_total: '0.00',
    total: '30.00',
    lines: [],
    payments: [],
    note: '',
    needs_review: false,
    created_at: '2026-01-02T09:00:00Z',
    completed_at: null,
  },
];

const CASHIERS = [
  { id: 1, username: 'jane', role: 'cashier', active: true },
  { id: 2, username: 'kofi', role: 'cashier', active: true },
];

function authMe(role: string) {
  return {
    user: { id: 1, username: 'maya', role },
    must_change_password: false,
    business_info_complete: true,
  };
}

function json(data: unknown, status = 200) {
  return { status, contentType: 'application/json', body: JSON.stringify(data) };
}

interface ApiCalls {
  methods: string[];
  paths: string[];
  bodies: string[];
}

async function installMocks(page: Page, options: {
  role?: string;
  sales?: unknown[];
  salesStatus?: number;
  sessionOpen?: boolean;
} = {}): Promise<{ monitor: NetworkMonitor; calls: ApiCalls }> {
  const monitor: NetworkMonitor = { external: [], failed: [] };
  const calls: ApiCalls = { methods: [], paths: [], bodies: [] };

  page.on('requestfailed', (request) => {
    const url = new URL(request.url());
    if (url.origin === BASE_URL) {
      monitor.failed.push(
        `${request.method()} ${url.pathname}: ${request.failure()?.errorText ?? 'failed'}`,
      );
    }
  });

  await page.route('**/*', async (route) => {
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
    calls.paths.push(path);
    calls.methods.push(method);
    calls.bodies.push(request.postData() ?? '');

    if (path === '/api/auth/me/') {
      await route.fulfill(json(authMe(options.role ?? 'admin')));
      return;
    }
    if (path === '/api/sessions/current/') {
      await route.fulfill(json({ open: options.sessionOpen ?? true }));
      return;
    }
    if (path === '/api/users/' && url.search.includes('role=cashier')) {
      await route.fulfill(json(CASHIERS));
      return;
    }
    if (path === '/api/sales/' && method === 'GET') {
      if (options.salesStatus) {
        await route.fulfill(json({ detail: 'Server error' }, options.salesStatus));
        return;
      }
      await route.fulfill(json(options.sales ?? SALES_SEED));
      return;
    }
    if (/^\/api\/sales\/\d+\/(void|refund)\/$/.test(path) && method === 'POST') {
      const id = Number(path.split('/')[3]);
      const status = path.includes('/void/') ? 'voided' : 'refunded';
      const sale = (options.sales ?? SALES_SEED).find((s: any) => s.id === id) as any;
      await route.fulfill(json({ ...sale, status }));
      return;
    }

    await route.fulfill(json({ detail: `No mock for ${method} ${path}` }, 404));
  });

  return { monitor, calls };
}

function expectNoNetworkIssues(monitor: NetworkMonitor) {
  expect(monitor.external, `external requests: ${monitor.external.join(', ')}`).toEqual([]);
  expect(monitor.failed, `failed requests: ${monitor.failed.join(', ')}`).toEqual([]);
}

test('navigates to Sales Manager from the sidebar and renders KPIs and rows', async ({ page }) => {
  const { monitor } = await installMocks(page);
  await page.goto(`${BASE_URL}/`);
  await expect(page.getByRole('heading', { level: 1 })).toBeVisible();

  // Sidebar entry exists for a manager/admin.
  const sidebarLink = page.getByRole('link', { name: 'Sales Manager' });
  await expect(sidebarLink).toBeVisible();
  await sidebarLink.click();

  await expect(page).toHaveURL(new RegExp('/sales-manager'));
  await expect(page.getByRole('heading', { level: 1, name: 'Sales Manager' })).toBeVisible();
  expectNoNetworkIssues(monitor);

  // KPI summary bar and table rows.
  await expect(page.getByTestId('kpi-open-sales')).toHaveText('1');
  await expect(page.getByText('1001')).toBeVisible();
  await expect(page.getByText('1002')).toBeVisible();
  await expect(page.getByTestId('sales-table')).toBeVisible();
});

test('hides the Sales Manager sidebar entry for cashiers and redirects deep links', async ({ page }) => {
  const { monitor } = await installMocks(page, { role: 'cashier' });
  await page.goto(`${BASE_URL}/`);
  await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
  expectNoNetworkIssues(monitor);

  await expect(page.getByRole('link', { name: 'Sales Manager' })).toHaveCount(0);

  // Deep-linking straight to the page is blocked by the router guard:
  // RequireRole(['manager','admin']) redirects cashiers back to '/'.
  await page.goto(`${BASE_URL}/sales-manager`);
  await expect(page).toHaveURL(`${BASE_URL}/`);
  await expect(page.getByRole('heading', { name: 'Sales Manager' })).toHaveCount(0);
});

test('shows the empty state when there are no sales', async ({ page }) => {
  const { monitor } = await installMocks(page, { sales: [] });
  await page.goto(`${BASE_URL}/sales-manager`);
  await expect(page.getByRole('heading', { level: 1, name: 'Sales Manager' })).toBeVisible();
  expectNoNetworkIssues(monitor);

  await expect(page.getByText('No sales yet')).toBeVisible();
});

test('shows the error state when the sales API fails', async ({ page }) => {
  const { monitor } = await installMocks(page, { salesStatus: 500 });
  await page.goto(`${BASE_URL}/sales-manager`);
  await expect(page.getByRole('heading', { level: 1, name: 'Sales Manager' })).toBeVisible();
  expectNoNetworkIssues(monitor);

  await expect(page.getByRole('alert').first()).toContainText('Failed to load sales');
  await expect(page.getByTestId('sales-table')).toHaveCount(0);
});

test('search filters rows and persists the query in the URL', async ({ page }) => {
  const { monitor } = await installMocks(page);
  await page.goto(`${BASE_URL}/sales-manager`);
  await expect(page.getByRole('heading', { level: 1, name: 'Sales Manager' })).toBeVisible();
  expectNoNetworkIssues(monitor);

  await page.getByTestId('sales-search').fill('kofi');
  await expect(page.getByText('1002')).toBeVisible();
  await expect(page.getByText('1001')).toHaveCount(0);
  // URL persistence for shareable filtered views.
  await expect(page).toHaveURL(new RegExp('search=kofi'));

  // The no-match empty state is distinct from the no-sales state.
  await page.getByTestId('sales-search').fill('nobody');
  await expect(page.getByText('No sales match')).toBeVisible();
});

test('voids an open sale through the confirmation modal', async ({ page }) => {
  const { monitor, calls } = await installMocks(page);
  await page.goto(`${BASE_URL}/sales-manager`);
  await expect(page.getByText('1002')).toBeVisible();
  expectNoNetworkIssues(monitor);

  const openRow = page.getByText('1002').locator('xpath=ancestor::tr');
  await openRow.getByTestId('action-void').click();

  const confirm = page.getByRole('alertdialog');
  await expect(confirm).toContainText('Void sale #1002?');
  await confirm.getByTestId('action-confirm').click();

  await expect(confirm).toBeHidden();
  expect(calls.methods).toContain('POST');
  expect(calls.paths).toContain('/api/sales/2/void/');
  await expect(openRow.getByText('Voided')).toBeVisible();
});

test('requires a refund reason before calling the refund API', async ({ page }) => {
  const { monitor, calls } = await installMocks(page);
  await page.goto(`${BASE_URL}/sales-manager`);
  await expect(page.getByText('1001')).toBeVisible();
  expectNoNetworkIssues(monitor);

  const completedRow = page.getByText('1001').locator('xpath=ancestor::tr');
  await completedRow.getByTestId('action-refund').click();

  const confirm = page.getByRole('alertdialog');
  await expect(confirm).toContainText('Refund sale #1001?');
  // No reason entered yet — confirm must not call the API.
  await confirm.getByTestId('action-confirm').click();
  await expect(confirm.getByRole('alert')).toContainText('reason is required');
  expect(calls.paths).not.toContain('/api/sales/1/refund/');

  await confirm.getByPlaceholder('Why is this sale being refunded?').fill('Customer changed mind');
  await confirm.getByTestId('action-confirm').click();
  await expect(confirm).toBeHidden();
  expect(calls.paths).toContain('/api/sales/1/refund/');
  await expect(completedRow.getByText('Refunded')).toBeVisible();
});
