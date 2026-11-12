import { expect, test, type Page, type Route } from '@playwright/test';

const BASE_URL = 'http://127.0.0.1:4173';

const SNAPSHOT = {
  animations: 'disabled' as const,
  caret: 'hide' as const,
  scale: 'css' as const,
  maxDiffPixelRatio: 0.01,
};

interface ProductFixture {
  id: number;
  name: string;
  sku: string | null;
  description: string;
  price: string;
  cost: string;
  min_stock: number;
  supplier: number | null;
  supplier_name: string | null;
  active: boolean;
  created_at: string;
  updated_at: string;
  quantity: number;
  is_low_stock: boolean;
  non_stock: boolean;
  category_name: string | null;
}

const TIMESTAMP = '2026-01-15T10:00:00Z';

function product(
  overrides: Partial<ProductFixture> & Pick<ProductFixture, 'id' | 'name'>,
): ProductFixture {
  return {
    sku: null,
    description: '',
    price: '1.00',
    cost: '0.50',
    min_stock: 0,
    supplier: null,
    supplier_name: null,
    active: true,
    created_at: TIMESTAMP,
    updated_at: TIMESTAMP,
    quantity: 0,
    is_low_stock: false,
    non_stock: false,
    category_name: null,
    ...overrides,
  };
}

const BASE_PRODUCTS: ProductFixture[] = [
  product({
    id: 1,
    name: 'Espresso Beans',
    sku: 'BEAN-001',
    description: 'Drinks',
    category_name: 'Drinks',
    price: '12.50',
    cost: '7.00',
    min_stock: 10,
    supplier: 1,
    supplier_name: 'North Roasters',
    quantity: 24,
  }),
  product({
    id: 2,
    name: 'Oat Milk',
    sku: 'OATM-002',
    description: 'Drinks',
    category_name: 'Drinks',
    price: '3.25',
    cost: '1.80',
    min_stock: 12,
    supplier: 1,
    supplier_name: 'North Roasters',
    quantity: 3,
    is_low_stock: true,
  }),
  product({
    id: 3,
    name: 'Butter Croissant',
    sku: 'CROS-003',
    description: 'Food',
    category_name: 'Food',
    price: '2.75',
    cost: '1.10',
    min_stock: 8,
    quantity: 0,
    is_low_stock: true,
  }),
  product({
    id: 4,
    name: 'Dot Grid Notebook',
    sku: 'NOTE-004',
    description: 'Stationery',
    category_name: 'Stationery',
    price: '6.00',
    cost: '2.40',
    min_stock: 5,
    supplier: 2,
    supplier_name: 'Paper & Co',
    quantity: 50,
  }),
];

type Role = 'cashier' | 'manager' | 'admin';

interface MockOptions {
  role?: Role;
  receiveError?: string;
}

interface MockState {
  external: string[];
  failed: string[];
  products: ProductFixture[];
  productsRequests: number;
  receiveRequests: Array<{ quantity: number; note: string }>;
}

function json(data: unknown, status = 200) {
  return {
    status,
    contentType: 'application/json',
    body: JSON.stringify(data),
  };
}

async function installApiMocks(
  page: Page,
  options: MockOptions = {},
): Promise<MockState> {
  const state: MockState = {
    external: [],
    failed: [],
    products: BASE_PRODUCTS.map((item) => ({ ...item })),
    productsRequests: 0,
    receiveRequests: [],
  };

  const authMe = {
    user: {
      id: 1,
      username: 'maya',
      role: options.role ?? 'admin',
    },
    must_change_password: false,
    business_info_complete: true,
  };

  page.on('requestfailed', (request) => {
    const url = new URL(request.url());
    if (url.origin === BASE_URL) {
      state.failed.push(
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

    // The offline guarantee: anything outside the preview origin is blocked.
    if (url.origin !== BASE_URL) {
      state.external.push(url.href);
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
      await route.fulfill(json(authMe));
      return;
    }

    if (path === '/api/auth/login/') {
      await route.fulfill(json({ detail: 'ok' }));
      return;
    }

    if (path === '/api/business/') {
      await route.fulfill(
        json({
          company_name: 'eixn Corner Store',
          phone: '555-0100',
          email: 'hello@eixn.test',
          address: '12 Market Street',
          website: '',
        }),
      );
      return;
    }

    if (path === '/api/products/' && method === 'GET') {
      state.productsRequests += 1;
      await route.fulfill(json(state.products));
      return;
    }

    const receiveMatch = path.match(/^\/api\/products\/(\d+)\/receive\/$/);
    if (receiveMatch && method === 'POST') {
      const body = (request.postDataJSON() ?? {}) as {
        quantity?: number;
        note?: string;
      };
      const quantity = Number(body.quantity ?? 0);
      const note = String(body.note ?? '');
      state.receiveRequests.push({ quantity, note });

      if (options.receiveError) {
        await route.fulfill(json({ detail: options.receiveError }, 400));
        return;
      }

      const id = Number(receiveMatch[1]);
      const target = state.products.find((item) => item.id === id);
      if (!target) {
        await route.fulfill(json({ detail: 'Product not found' }, 404));
        return;
      }

      target.quantity += quantity;
      target.is_low_stock = target.quantity <= target.min_stock;
      await route.fulfill(json(target));
      return;
    }

    await route.fulfill(json({ detail: `No mock for ${method} ${path}` }, 404));
  });

  return state;
}

async function openInventory(
  page: Page,
  options: MockOptions = {},
): Promise<MockState> {
  const state = await installApiMocks(page, options);
  await page.goto(`${BASE_URL}/inventory`);
  await expect(page.locator('.app-shell')).toBeVisible();
  await expect(
    page.getByRole('heading', { level: 1, name: 'Inventory' }),
  ).toBeVisible();
  await page.evaluate(async () => {
    await document.fonts.ready;
  });
  return state;
}

function expectNoNetworkIssues(state: MockState) {
  expect(
    state.external,
    `external requests were attempted: ${state.external.join(', ')}`,
  ).toEqual([]);
  expect(
    state.failed,
    `same-origin requests failed: ${state.failed.join(', ')}`,
  ).toEqual([]);
}

function inventoryRow(page: Page, name: string) {
  return page.locator('.inventory-row').filter({ hasText: name });
}

test('renders the themed Inventory list, filters, and status badges', async ({
  page,
}) => {
  const state = await openInventory(page);

  await expect(page.locator('.mm-page-subtitle')).toHaveText(
    '4 products · 1 low stock · 1 out of stock',
  );
  await expect(
    page.getByRole('button', { name: '+ New product' }),
  ).toBeVisible();

  const table = page.locator('.inventory-table');
  await expect(table.locator('thead th')).toHaveText([
    'SKU',
    'Product',
    'Retail price',
    'Stock',
    'Min',
    'Status',
    'Supplier',
    'Actions',
  ]);
  await expect(page.locator('.inventory-row')).toHaveCount(4);

  const okBadge = inventoryRow(page, 'Espresso Beans').locator('.mm-badge');
  await expect(okBadge).toHaveText('OK');
  await expect(okBadge).toHaveCSS(
    'background-color',
    'rgba(0, 206, 182, 0.15)',
  );
  await expect(okBadge).toHaveCSS('color', 'rgb(0, 127, 112)');

  const lowBadge = inventoryRow(page, 'Oat Milk').locator('.mm-badge');
  await expect(lowBadge).toHaveText('Low');
  await expect(lowBadge).toHaveCSS(
    'background-color',
    'rgba(255, 174, 31, 0.15)',
  );
  await expect(lowBadge).toHaveCSS('color', 'rgb(138, 90, 0)');

  const outBadge = inventoryRow(page, 'Butter Croissant').locator('.mm-badge');
  await expect(outBadge).toHaveText('Out');
  await expect(outBadge).toHaveCSS(
    'background-color',
    'rgba(255, 102, 146, 0.15)',
  );
  await expect(outBadge).toHaveCSS('color', 'rgb(194, 24, 91)');

  const rowBox = await inventoryRow(page, 'Espresso Beans').boundingBox();
  expect(rowBox?.height ?? 0).toBeGreaterThanOrEqual(48);

  // Existing filtering behavior is preserved.
  const search = page.getByLabel('Search products');
  await search.fill('oat');
  await expect(page.locator('.inventory-row')).toHaveCount(1);
  await search.fill('');

  await page.getByLabel('Filter by category').selectOption('Drinks');
  await expect(page.locator('.inventory-row')).toHaveCount(2);
  await page.getByLabel('Filter by category').selectOption('All');

  await page.getByLabel('Filter by stock status').selectOption('Out');
  await expect(page.locator('.inventory-row')).toHaveCount(1);
  await expect(
    inventoryRow(page, 'Butter Croissant'),
  ).toBeVisible();
  await page.getByLabel('Filter by stock status').selectOption('All');

  await expect(page.locator('.inventory-row')).toHaveCount(4);
  await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur());

  await expect(page.locator('.app-page')).toHaveScreenshot(
    'inventory-desktop.png',
    SNAPSHOT,
  );

  expectNoNetworkIssues(state);
});

test('captures the mobile Inventory layout without page overflow', async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const state = await openInventory(page);

  await expect(page.locator('.mm-page-subtitle')).toContainText('4 products');
  await expect(page.locator('.inventory-table th.col-supplier')).toBeHidden();
  await expect(
    page.locator('.inventory-table td.col-supplier').first(),
  ).toBeHidden();

  const pageOverflow = await page.evaluate(
    () =>
      document.documentElement.scrollWidth -
      document.documentElement.clientWidth,
  );
  expect(pageOverflow).toBeLessThanOrEqual(1);

  const tableOverflow = await page
    .locator('.inventory-table-wrap')
    .evaluate((element) => element.scrollWidth > element.clientWidth);
  expect(tableOverflow).toBe(true);

  await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur());
  await expect(page.locator('.app-page')).toHaveScreenshot(
    'inventory-mobile.png',
    SNAPSHOT,
  );

  expectNoNetworkIssues(state);
});

test('receive-stock modal posts the same payload and refreshes the list', async ({
  page,
}) => {
  const state = await openInventory(page);

  await page
    .getByRole('button', { name: 'Receive stock for Espresso Beans' })
    .click();

  const dialog = page.getByRole('dialog');
  await expect(dialog).toBeVisible();
  await expect(
    dialog.getByRole('heading', { name: 'Receive stock' }),
  ).toBeVisible();
  await expect(dialog.getByLabel('Quantity to receive')).toHaveValue('1');

  await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur());
  await expect(dialog).toHaveScreenshot(
    'inventory-receive-modal.png',
    SNAPSHOT,
  );

  await dialog.getByLabel('Quantity to receive').fill('6');
  await dialog.getByLabel('Note').fill('PO 42');
  await dialog.getByRole('button', { name: 'Confirm receive' }).click();

  await expect(dialog).toBeHidden();
  await expect.poll(() => state.receiveRequests.length).toBe(1);
  expect(state.receiveRequests[0]).toEqual({ quantity: 6, note: 'PO 42' });

  // The list is reloaded after a successful receive.
  expect(state.productsRequests).toBeGreaterThanOrEqual(2);
  await expect(
    inventoryRow(page, 'Espresso Beans').locator('.col-stock'),
  ).toHaveText('30');

  expectNoNetworkIssues(state);
});

test('receive-stock modal shows errors and closes with Escape', async ({
  page,
}) => {
  const state = await openInventory(page, {
    receiveError: 'Shelf is full',
  });

  await page
    .getByRole('button', { name: 'Receive stock for Oat Milk' })
    .click();

  const dialog = page.getByRole('dialog');
  await expect(dialog).toBeVisible();

  await dialog.getByLabel('Quantity to receive').fill('2');
  await dialog.getByRole('button', { name: 'Confirm receive' }).click();

  await expect(dialog.getByRole('alert')).toHaveText('Shelf is full');
  await expect(dialog).toBeVisible();
  await expect.poll(() => state.receiveRequests.length).toBe(1);

  await page.keyboard.press('Escape');
  await expect(dialog).toBeHidden();

  expectNoNetworkIssues(state);
});

test('hides manager-only inventory actions from cashiers', async ({ page }) => {
  const state = await openInventory(page, { role: 'cashier' });

  await expect(page.locator('.inventory-row')).toHaveCount(4);
  await expect(
    page.getByRole('button', { name: '+ New product' }),
  ).toHaveCount(0);
  await expect(
    page.getByRole('button', { name: /Receive stock for/i }),
  ).toHaveCount(0);

  expectNoNetworkIssues(state);
});
