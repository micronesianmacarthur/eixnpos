import { expect, test, type Page, type Route } from '@playwright/test';

const BASE_URL =
  process.env.EIXNPOS_BASE_URL ?? 'http://127.0.0.1:4173';

const SNAPSHOT = {
  animations: 'disabled' as const,
  caret: 'hide' as const,
  scale: 'css' as const,
  maxDiffPixelRatio: 0.01,
};

interface NetworkMonitor {
  external: string[];
  failed: string[];
}

const AUTH_ME = {
  user: { id: 1, username: 'maya', role: 'admin' },
  must_change_password: false,
  business_info_complete: true,
};

const CUSTOMERS = [
  { id: 1, name: 'Alice', phone: '555-0100', balance: '10.00', credit_limit: '100.00', notes: '', created_at: '2026-01-01T10:00:00Z', updated_at: '2026-01-01T10:00:00Z' },
  { id: 2, name: 'Bob', phone: '555-0101', balance: '0.00', credit_limit: '50.00', notes: '', created_at: '2026-01-01T10:00:00Z', updated_at: '2026-01-01T10:00:00Z' },
];

const TRANSACTIONS = {
  sales: [{ id: 1, number: 1001, total: '10.00', status: 'completed', created_at: '2026-01-01T10:00:00Z' }],
  pay_on_account: [{ id: 1, customer: 1, amount: '5.00', note: 'Payment', created_at: '2026-01-02T10:00:00Z' }],
};

function json(data: unknown, status = 200) {
  return { status, contentType: 'application/json', body: JSON.stringify(data) };
}

async function installApiMocks(page: Page): Promise<NetworkMonitor> {
  const monitor: NetworkMonitor = { external: [], failed: [] };

  page.on('requestfailed', (request) => {
    const url = new URL(request.url());
    if (url.origin === BASE_URL) {
      monitor.failed.push(`${request.method()} ${url.pathname}: ${request.failure()?.errorText ?? 'failed'}`);
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

    if (path === '/api/auth/me/') {
      await route.fulfill(json(AUTH_ME));
      return;
    }
    if (path === '/api/customers/') {
      await route.fulfill(json(CUSTOMERS));
      return;
    }
    if (path === '/api/customers/1/transactions/') {
      await route.fulfill(json(TRANSACTIONS));
      return;
    }
    if (path.startsWith('/api/customers/') && method === 'POST') {
      await route.fulfill(json(CUSTOMERS[0]));
      return;
    }
    if (path.startsWith('/api/customers/') && method === 'DELETE') {
      await route.fulfill(json({}));
      return;
    }

    await route.fulfill(json({ detail: `No mock for ${method} ${path}` }, 404));
  });

  return monitor;
}

async function openCustomers(page: Page): Promise<NetworkMonitor> {
  const monitor = await installApiMocks(page);
  await page.goto(`${BASE_URL}/customers`);
  await expect(page.locator('.app-shell')).toBeVisible();
  await expect(page.getByRole('heading', { level: 1, name: 'Customers' })).toBeVisible();
  await page.evaluate(async () => {
    await document.fonts.ready;
  });
  return monitor;
}

function expectNoNetworkIssues(monitor: NetworkMonitor) {
  expect(monitor.external, `external requests: ${monitor.external.join(', ')}`).toEqual([]);
  expect(monitor.failed, `failed requests: ${monitor.failed.join(', ')}`).toEqual([]);
}

test('renders Customers on desktop', async ({ page }) => {
  const monitor = await openCustomers(page);
  expectNoNetworkIssues(monitor);
  await expect(page).toHaveScreenshot('customers-desktop.png', SNAPSHOT);
});

test('renders Customers on mobile', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const monitor = await openCustomers(page);
  expectNoNetworkIssues(monitor);
  await expect(page).toHaveScreenshot('customers-mobile.png', SNAPSHOT);
});

test('opens the create customer modal', async ({ page }) => {
  const monitor = await openCustomers(page);
  await page.getByRole('button', { name: /\+ New customer/i }).click();
  await expect(page.locator('.mm-modal')).toBeVisible();
  expectNoNetworkIssues(monitor);
  await expect(page).toHaveScreenshot('customers-create-modal.png', SNAPSHOT);
});

test('opens the pay on account modal', async ({ page }) => {
  const monitor = await openCustomers(page);
  await page.getByRole('button', { name: /Pay on account/i }).click();
  await expect(page.locator('.mm-modal')).toBeVisible();
  expectNoNetworkIssues(monitor);
  await expect(page).toHaveScreenshot('customers-pay-modal.png', SNAPSHOT);
});
