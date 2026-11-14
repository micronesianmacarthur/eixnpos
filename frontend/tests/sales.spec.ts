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
  /** Parsed JSON bodies of POST /api/sales/1/set_payment/ requests, in order. */
  setPaymentBodies: Array<Record<string, unknown>>;
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

function toMoney(value: unknown): string {
  return Number(value ?? 0).toFixed(2);
}

/** Build the payment record the backend would persist from a set_payment payload. */
function paymentFromPayload(payload: Record<string, unknown>) {
  const tendered = toMoney(payload.tendered ?? payload.amount);
  const amount = toMoney(payload.amount);
  return {
    id: 1,
    method: String(payload.method ?? 'cash'),
    amount,
    tendered,
    change_due: (Math.max(0, Number(tendered) - Number(amount))).toFixed(2),
    manager_approved: true,
  };
}


function json(data: unknown, status = 200) {
  return { status, contentType: 'application/json', body: JSON.stringify(data) };
}

async function installApiMocks(
  page: Page,
  opts: { initialPayments?: typeof SALE_WITH_PAYMENT.payments } = {},
): Promise<NetworkMonitor> {
  const monitor: NetworkMonitor = { external: [], failed: [], setPaymentBodies: [] };
  // The payment the backend would have persisted; updated by set_payment POSTs.
  let recordedPayment: typeof SALE_WITH_PAYMENT.payments[number] | null = null;
  const saleWithPayments = () => ({
    ...SALE,
    payments: recordedPayment ? [recordedPayment] : (opts.initialPayments ?? SALE_WITH_PAYMENT.payments),
  });

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
    if (path === '/api/sales/1/set_payment/' && method === 'POST') {
      const payload = JSON.parse(request.postData() ?? '{}') as Record<string, unknown>;
      monitor.setPaymentBodies.push(payload);
      recordedPayment = paymentFromPayload(payload);
      await route.fulfill(json(recordedPayment));
      return;
    }
    if (path === '/api/sales/1/' || path === '/api/sales/1/add_line/') {
      await route.fulfill(json(saleWithPayments()));
      return;
    }
    if (
      path.startsWith('/api/sales/1/lines/') ||
      path === '/api/sales/1/complete/' ||
      path === '/api/sales/1/payments/'
    ) {
      await route.fulfill(json(SALE));
      return;
    }

    await route.fulfill(json({ detail: `No mock for ${method} ${path}` }, 404));
  });

  return monitor;
}

async function openSales(
  page: Page,
  opts: { initialPayments?: typeof SALE_WITH_PAYMENT.payments } = {},
): Promise<NetworkMonitor> {
  const monitor = await installApiMocks(page, opts);
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

test('records over-tender cash payment at the tendered amount', async ({ page }) => {
  const monitor = await openSales(page, { initialPayments: [] });
  await page.locator('.product-card').first().click();
  await expect(page.locator('.cart-line')).toBeVisible();
  await page.getByRole('spinbutton', { name: 'Tendered' }).fill('25');
  await page.getByRole('button', { name: /^Cash$/i }).click();
  await expect(page.locator('.recorded-payment')).toHaveCount(1);

  // The recorded amount must equal the amount tendered, not the cart total.
  expect(monitor.setPaymentBodies, `set_payment bodies: ${JSON.stringify(monitor.setPaymentBodies)}`).toEqual([
    { method: 'cash', amount: '25.00', tendered: '25' },
  ]);
  const recorded = page.locator('.recorded-payment').first();
  await expect(recorded).toContainText('Cash');
  await expect(recorded).toContainText('$25.00');
  expectNoNetworkIssues(monitor);
});

test('records exact-tender cash payment at the cart total', async ({ page }) => {
  const monitor = await openSales(page, { initialPayments: [] });
  await page.locator('.product-card').first().click();
  await expect(page.locator('.cart-line')).toBeVisible();
  await page.getByRole('spinbutton', { name: 'Tendered' }).fill('10');
  await page.getByRole('button', { name: /^Cash$/i }).click();
  await expect(page.locator('.recorded-payment')).toHaveCount(1);

  expect(monitor.setPaymentBodies, `set_payment bodies: ${JSON.stringify(monitor.setPaymentBodies)}`).toEqual([
    { method: 'cash', amount: '10.00', tendered: '10' },
  ]);
  const recorded = page.locator('.recorded-payment').first();
  await expect(recorded).toContainText('Cash');
  await expect(recorded).toContainText('$10.00');
  expectNoNetworkIssues(monitor);
});

test('records under-tender cash payment as a partial payment', async ({ page }) => {
  const monitor = await openSales(page, { initialPayments: [] });
  await page.locator('.product-card').first().click();
  await expect(page.locator('.cart-line')).toBeVisible();
  await page.getByRole('spinbutton', { name: 'Tendered' }).fill('5');
  await page.getByRole('button', { name: /^Cash$/i }).click();
  await expect(page.locator('.recorded-payment')).toHaveCount(1);

  expect(monitor.setPaymentBodies, `set_payment bodies: ${JSON.stringify(monitor.setPaymentBodies)}`).toEqual([
    { method: 'cash', amount: '5.00', tendered: '5' },
  ]);
  const recorded = page.locator('.recorded-payment').first();
  await expect(recorded).toContainText('Cash');
  await expect(recorded).toContainText('$5.00');
  expectNoNetworkIssues(monitor);
});

test('opens the receipt modal', async ({ page }) => {
  const monitor = await openSales(page);
  await page.locator('.product-card').first().click();
  await expect(page.locator('.cart-line')).toBeVisible();
  await page.getByRole('spinbutton', { name: 'Tendered' }).fill('10');
  await page.getByRole('button', { name: /^Cash$/i }).click();
  await page.getByRole('button', { name: /Complete sale/i }).click();
  await expect(page.locator('.receipt-modal')).toBeVisible();
  expectNoNetworkIssues(monitor);
  await expect(page).toHaveScreenshot('sales-receipt-modal.png', SNAPSHOT);
});
