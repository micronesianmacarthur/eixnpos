/**
 * E2E regression: cashier invoice permissions (task t_f6353666).
 *
 * Logged in as a cashier with a registered customer in the cart:
 *  1. the payment panel shows no manager-approval prompt;
 *  2. the invoice tender POSTs customer + idempotency key with NO manager
 *     credentials;
 *  3. a walk-in (no customer selected) is blocked client-side with the
 *     routing error and never reaches the backend.
 *
 * Mocked at the API boundary (same strategy as sales.spec.ts) so the UI gate
 * itself is what is under test.
 */
import { expect, test, type Page } from '@playwright/test';

const BASE_URL = process.env.EIXNPOS_BASE_URL ?? 'http://127.0.0.1:4173';

const AUTH_ME = {
  user: { id: 7, username: 'casey', role: 'cashier' },
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

const CUSTOMER = { id: 9, name: 'Alice', balance: '0.00', credit_limit: '100.00' };

const SALE = {
  id: 1,
  number: 1001,
  cashier: 7,
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

function json(data: unknown, status = 200) {
  return { status, contentType: 'application/json', body: JSON.stringify(data) };
}

interface InvoiceMonitor {
  setPaymentBodies: Array<Record<string, unknown>>;
}

async function installInvoiceMocks(page: Page): Promise<InvoiceMonitor> {
  const monitor: InvoiceMonitor = { setPaymentBodies: [] };
  let recordedPayment: Record<string, unknown> | null = null;
  const saleNow = () => ({ ...SALE, payments: recordedPayment ? [recordedPayment] : [] });

  await page.route('**/*', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.origin !== BASE_URL || !url.pathname.startsWith('/api/')) {
      await route.continue();
      return;
    }
    const path = url.pathname;
    const method = request.method().toUpperCase();

    if (path === '/api/auth/me/') return void (await route.fulfill(json(AUTH_ME)));
    if (path === '/api/sessions/current/') return void (await route.fulfill(json({ open: true })));
    if (path === '/api/business/') return void (await route.fulfill(json(BUSINESS_INFO)));
    if (path === '/api/products/') return void (await route.fulfill(json([PRODUCT])));
    if (path === '/api/customers/') return void (await route.fulfill(json([CUSTOMER])));
    if (path === '/api/sales/' && method === 'POST') return void (await route.fulfill(json(SALE)));
    if (path === '/api/sales/') return void (await route.fulfill(json([SALE])));
    if (path === '/api/sales/1/set_payment/' && method === 'POST') {
      const payload = JSON.parse(request.postData() ?? '{}') as Record<string, unknown>;
      monitor.setPaymentBodies.push(payload);
      recordedPayment = {
        id: 99, method: 'invoice', amount: payload.amount,
        tendered: null, change_due: '0.00', manager_approved: false,
        policy_authorized: true,
      };
      await route.fulfill(json(recordedPayment));
      return;
    }
    if (path === '/api/sales/1/' || path === '/api/sales/1/add_line/') {
      await route.fulfill(json(saleNow()));
      return;
    }
    if (path.startsWith('/api/sales/1/')) {
      await route.fulfill(json(SALE));
      return;
    }
    await route.fulfill(json({ detail: `No mock for ${method} ${path}` }, 404));
  });
  return monitor;
}

/** Open /sales as a cashier and put the product in the cart. */
async function openCashierCheckout(page: Page): Promise<InvoiceMonitor> {
  const monitor = await installInvoiceMocks(page);
  await page.goto(`${BASE_URL}/sales`);
  await expect(page.locator('.app-shell')).toBeVisible();
  await page.locator('.product-card').first().click();
  await expect(page.locator('.cart-line')).toBeVisible();
  return monitor;
}

test('cashier checkout shows no manager approval prompt for invoice', async ({ page }) => {
  await openCashierCheckout(page);
  await page.getByLabel('Customer').selectOption(String(CUSTOMER.id));
  await page.getByRole('button', { name: 'Invoice' }).click();

  await expect(page.locator('input[name="manager_username"], input[placeholder*="Manager"]')).toHaveCount(0);
  await expect(page.getByText(/Manager approval required for invoice/)).toHaveCount(0);
});

test('cashier invoice tender posts customer + idempotency key, no manager creds', async ({ page }) => {
  const monitor = await openCashierCheckout(page);
  await page.getByLabel('Customer').selectOption(String(CUSTOMER.id));
  await page.getByRole('button', { name: 'Invoice' }).click();

  await expect(page.locator('.cart-line')).toBeVisible(); // still there after success
  await expect(monitor.setPaymentBodies).toHaveLength(1);
  const body = monitor.setPaymentBodies[0];
  expect(body.method).toBe('invoice');
  expect(body.customer).toBe(CUSTOMER.id);
  expect(String(body.idempotency_key ?? '')).toBeTruthy();
  expect('manager_username' in body).toBe(false);
  expect('manager_password' in body).toBe(false);
});

test('walk-in invoice (no customer) is blocked client-side with the routing error', async ({ page }) => {
  const monitor = await openCashierCheckout(page);
  // Leave the customer unselected: this is a walk-in.
  await page.getByRole('button', { name: 'Invoice' }).click();

  await expect(page.getByRole('alert')).toHaveText(/select a customer/i);
  expect(monitor.setPaymentBodies).toHaveLength(0);
});
