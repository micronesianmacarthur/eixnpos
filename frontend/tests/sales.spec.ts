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

const CUSTOMER = { id: 1, name: 'Alice', balance: '0.00', credit_limit: '100.00' };

const SALE = {
  id: 1,
  number: 1001,
  cashier: 1,
  customer: null,
  status: 'open',
  subtotal: '10.00',
  discount_total: '0.00',
  total: '10.00',
  lines: [
    {
      id: 1,
      product: 1,
      product_name: 'Widget',
      quantity: 1,
      unit_price: '10.00',
      discount_type: 'none',
      discount_value: '0',
      discount_amount: '0.00',
      line_total: '10.00',
    },
  ],
  payments: [],
  created_at: '2026-01-01T10:00:00Z',
};

const SALE_WITH_PAYMENT = {
  ...SALE,
  payments: [{ id: 1, method: 'cash', amount: '10.00', tendered: '10.00', change_due: '0.00', manager_approved: true }],
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
    if (path === '/api/business/') {
      await route.fulfill(json(BUSINESS_INFO));
      return;
    }
    if (path === '/api/products/') {
      await route.fulfill(json([PRODUCT]));
      return;
    }
    if (path === '/api/customers/') {
      await route.fulfill(json([CUSTOMER]));
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
      await route.fulfill(json(SALE_WITH_PAYMENT));
      return;
    }
    if (path === '/api/sales/1/set_payment/') {
      await route.fulfill(json({ id: 1, method: 'cash', amount: '10.00', tendered: '10.00', change_due: '0.00', manager_approved: true }));
      return;
    }
    if (
      path.startsWith('/api/sales/1/lines/') ||
      path === '/api/sales/1/complete/' ||
      path === '/api/sales/1/payments/' ||
      path === '/api/sales/1/set_payment/'
    ) {
      await route.fulfill(json(SALE));
      return;
    }

    await route.fulfill(json({ detail: `No mock for ${method} ${path}` }, 404));
  });

  return monitor;
}

async function openSales(page: Page): Promise<NetworkMonitor> {
  const monitor = await installApiMocks(page);
  await page.goto(`${BASE_URL}/sales`);
  await expect(page.locator('.app-shell')).toBeVisible();
  await page.evaluate(async () => {
    await document.fonts.ready;
  });
  return monitor;
}

function expectNoNetworkIssues(monitor: NetworkMonitor) {
  expect(monitor.external, `external requests: ${monitor.external.join(', ')}`).toEqual([]);
  expect(monitor.failed, `failed requests: ${monitor.failed.join(', ')}`).toEqual([]);
}

test('renders Sales on desktop', async ({ page }) => {
  const monitor = await openSales(page);
  await expect(page.getByRole('heading', { level: 1, name: 'Sale' })).toBeVisible();
  expectNoNetworkIssues(monitor);
  await expect(page).toHaveScreenshot('sales-desktop.png', SNAPSHOT);
});

test('renders Sales on mobile', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const monitor = await openSales(page);
  expectNoNetworkIssues(monitor);
  await expect(page).toHaveScreenshot('sales-mobile.png', SNAPSHOT);
});

test('opens the discount modal', async ({ page }) => {
  const monitor = await openSales(page);
  await page.locator('.product-card').first().click();
  await expect(page.locator('.cart-line')).toBeVisible();
  await page.locator('button[title="Discount"]').click();
  await expect(page.locator('.mm-modal')).toBeVisible();
  expectNoNetworkIssues(monitor);
  await expect(page).toHaveScreenshot('sales-discount-modal.png', SNAPSHOT);
});

test('opens the receipt modal', async ({ page }) => {
  const monitor = await openSales(page);
  await page.locator('.product-card').first().click();
  await expect(page.locator('.cart-line')).toBeVisible();
  await page.getByRole('spinbutton', { name: 'Tendered' }).fill('10');
  await page.getByRole('button', { name: /Record payment/i }).click();
  await page.getByRole('button', { name: /Complete sale/i }).click();
  await expect(page.locator('.receipt-modal')).toBeVisible();
  expectNoNetworkIssues(monitor);
  await expect(page).toHaveScreenshot('sales-receipt-modal.png', SNAPSHOT);
});
