import { expect, test, type Page } from '@playwright/test';

const BASE_URL =
  process.env.EIXNPOS_BASE_URL ?? 'http://127.0.0.1:4173';

interface NetworkMonitor {
  external: string[];
  failed: string[];
}

const AUTH_ME = {
  user: { id: 1, username: 'maya', role: 'admin' },
  must_change_password: false,
  business_info_complete: true,
};

const SUPPLIERS = [
  {
    id: 1,
    name: 'Acme Paper Co.',
    contact_name: 'Dana Reyes',
    email: 'dana@acmepaper.test',
    phone: '+1 555 0141',
    address: '',
    notes: '',
    active: true,
    created_at: '2026-05-02T10:00:00Z',
  },
  {
    id: 2,
    name: 'Nordic Beverage Supply',
    contact_name: 'Ingrid Larsen',
    email: '',
    phone: '+1 555 0188',
    address: '',
    notes: '',
    active: false,
    created_at: '2026-05-14T10:00:00Z',
  },
];

function json(data: unknown, status = 200) {
  return { status, contentType: 'application/json', body: JSON.stringify(data) };
}

interface ApiCalls {
  methods: string[];
  paths: string[];
}

async function installMocks(page: Page, options: {
  list?: unknown[];
  listStatus?: number;
} = {}): Promise<{ monitor: NetworkMonitor; calls: ApiCalls }> {
  const monitor: NetworkMonitor = { external: [], failed: [] };
  const calls: ApiCalls = { methods: [], paths: [] };

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

    if (path === '/api/auth/me/') {
      await route.fulfill(json(AUTH_ME));
      return;
    }
    if (path === '/api/suppliers/' && method === 'POST') {
      const posted = JSON.parse(request.postData() ?? '{}');
      await route.fulfill(
        json({ id: 99, created_at: new Date().toISOString(), ...posted }),
      );
      return;
    }
    if (path === '/api/suppliers/') {
      await route.fulfill(json(options.list ?? [], options.listStatus ?? 200));
      return;
    }
    if (path.startsWith('/api/suppliers/') && method === 'PATCH') {
      const id = Number(path.split('/')[3]);
      const posted = JSON.parse(request.postData() ?? '{}');
      await route.fulfill(json({ id, ...posted }));
      return;
    }
    if (path.startsWith('/api/suppliers/') && method === 'DELETE') {
      await route.fulfill({ status: 204 });
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

async function openSuppliers(page: Page) {
  await page.goto(`${BASE_URL}/suppliers`);
  await expect(
    page.getByRole('heading', { level: 1, name: 'Suppliers' }),
  ).toBeVisible();
  await page.evaluate(async () => {
    await document.fonts.ready;
  });
}

test('renders suppliers fetched from the API', async ({ page }) => {
  const { monitor } = await installMocks(page, { list: SUPPLIERS });
  await openSuppliers(page);
  expectNoNetworkIssues(monitor);

  await expect(page.getByText('Acme Paper Co.')).toBeVisible();
  await expect(page.getByText('Nordic Beverage Supply')).toBeVisible();
  await expect(page.getByText('Dana Reyes')).toBeVisible();
  // 2 suppliers on file · 1 active · 1 inactive
  await expect(page.getByText(/2 suppliers on file/)).toBeVisible();
});

test('shows an empty state when the API has no suppliers', async ({ page }) => {
  const { monitor } = await installMocks(page, { list: [] });
  await openSuppliers(page);
  expectNoNetworkIssues(monitor);

  await expect(page.getByText('No suppliers yet')).toBeVisible();
});

test('shows an error state when the API fails', async ({ page }) => {
  const { monitor } = await installMocks(page, { listStatus: 500 });
  await openSuppliers(page);
  expectNoNetworkIssues(monitor);

  await expect(page.getByRole('alert')).toContainText('Request failed (500)');
});

test('searches and filters suppliers', async ({ page }) => {
  const { monitor } = await installMocks(page, { list: SUPPLIERS });
  await openSuppliers(page);
  expectNoNetworkIssues(monitor);

  await page.getByLabel('Search suppliers').fill('acme');
  await expect(page.getByText('Acme Paper Co.')).toBeVisible();
  await expect(page.getByText('Nordic Beverage Supply')).not.toBeVisible();

  await page.getByLabel('Search suppliers').fill('');
  await page.getByLabel('Filter by supplier status').selectOption('Active');
  await expect(page.getByText('Acme Paper Co.')).toBeVisible();
  await expect(page.getByText('Nordic Beverage Supply')).not.toBeVisible();

  await page.getByLabel('Filter by supplier status').selectOption('Inactive');
  await expect(page.getByText('Nordic Beverage Supply')).toBeVisible();
  await expect(page.getByText('Acme Paper Co.')).not.toBeVisible();
});

test('creates a supplier via POST', async ({ page }) => {
  const { monitor, calls } = await installMocks(page, { list: SUPPLIERS });
  await openSuppliers(page);
  expectNoNetworkIssues(monitor);

  await page.getByRole('button', { name: '+ New supplier' }).click();
  await page.getByLabel('Name', { exact: true }).fill('Fjord Foods');
  await page.getByRole('button', { name: 'Create supplier' }).click();

  await expect(page.getByText('Fjord Foods')).toBeVisible();
  expect(calls.paths).toContain('/api/suppliers/');
  expect(calls.methods).toContain('POST');
});

test('edits a supplier via PATCH', async ({ page }) => {
  const { monitor, calls } = await installMocks(page, { list: SUPPLIERS });
  await openSuppliers(page);
  expectNoNetworkIssues(monitor);

  await page.getByRole('button', { name: 'Edit Acme Paper Co.' }).click();
  await page.getByLabel('Name', { exact: true }).fill('Acme Paper LLC');
  await page.getByRole('button', { name: 'Save changes' }).click();

  await expect(page.getByText('Acme Paper LLC')).toBeVisible();
  expect(calls.methods).toContain('PATCH');
});

test('deletes a supplier via DELETE', async ({ page }) => {
  const { monitor, calls } = await installMocks(page, { list: SUPPLIERS });
  await openSuppliers(page);
  expectNoNetworkIssues(monitor);

  await page.getByRole('button', { name: 'Delete Acme Paper Co.' }).click();
  await expect(page.getByRole('alertdialog')).toBeVisible();
  await page.getByRole('button', { name: 'Delete supplier' }).click();

  await expect(
    page.getByRole('region', { name: 'Suppliers' }).getByText('Acme Paper Co.'),
  ).toHaveCount(0);
  expect(calls.methods).toContain('DELETE');
});