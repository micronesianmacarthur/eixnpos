import { expect, test, type Page, type Route } from '@playwright/test';

// Defaults to the port in playwright.config.ts. The override keeps this spec
// runnable when a preview server from another worktree already owns 4173.
const BASE_URL =
  process.env.PRODUCT_E2E_BASE_URL ?? 'http://127.0.0.1:4173';

const SNAPSHOT = {
  animations: 'disabled' as const,
  caret: 'hide' as const,
  scale: 'css' as const,
  maxDiffPixelRatio: 0.01,
};

// Keep date rendering deterministic across machines.
test.use({ timezoneId: 'UTC', locale: 'en-US' });

interface NetworkMonitor {
  external: string[];
  failed: string[];
}

interface MockOptions {
  movements?: 'data' | 'empty' | 'error';
  movementsGate?: Promise<void>;
}

const AUTH_ME = {
  user: {
    id: 1,
    username: 'maya',
    role: 'admin',
  },
  must_change_password: false,
  business_info_complete: true,
};

const PRODUCT = {
  id: 7,
  name: 'Ethiopia Yirgacheffe',
  sku: 'COF-ETH-001',
  category: 1,
  category_name: 'Drinks',
  description: 'Drinks',
  price: '18.50',
  cost: '9.25',
  markup_margin: '10.00',
  min_stock: 10,
  supplier: 2,
  supplier_name: 'Highland Roasters',
  active: true,
  created_at: '2026-01-05T09:00:00Z',
  updated_at: '2026-01-06T09:00:00Z',
  quantity: 24,
  is_low_stock: false,
};

const SUPPLIERS = [
  {
    id: 1,
    name: 'Harbor Dairy',
    contact_name: 'Nadia Osman',
    email: 'orders@harbor.test',
    phone: '555-0110',
    address: '4 Dockside Lane',
    notes: '',
    active: true,
    created_at: '2025-11-01T08:00:00Z',
  },
  {
    id: 2,
    name: 'Highland Roasters',
    contact_name: 'Tomas Reyes',
    email: 'beans@highland.test',
    phone: '555-0120',
    address: '9 Roastery Row',
    notes: '',
    active: true,
    created_at: '2025-11-02T08:00:00Z',
  },
  {
    id: 3,
    name: 'Old Mill Supply',
    contact_name: 'Iris Chen',
    email: 'sales@oldmill.test',
    phone: '555-0130',
    address: '88 Mill Road',
    notes: '',
    active: false,
    created_at: '2025-11-03T08:00:00Z',
  },
];

const MOVEMENTS = [
  {
    id: 41,
    product: 7,
    product_name: PRODUCT.name,
    movement_type: 'adjustment',
    quantity: 3,
    reference: 'ADJ-1042',
    note: 'Cycle count correction',
    created_at: '2026-01-08T10:30:00Z',
  },
  {
    id: 40,
    product: 7,
    product_name: PRODUCT.name,
    movement_type: 'damage',
    quantity: -1,
    reference: 'DMG-0088',
    note: 'Torn bag',
    created_at: '2026-01-07T16:05:00Z',
  },
  {
    id: 39,
    product: 7,
    product_name: PRODUCT.name,
    movement_type: 'sale',
    quantity: -2,
    reference: 'SALE-2091',
    note: '',
    created_at: '2026-01-07T09:12:00Z',
  },
  {
    id: 38,
    product: 7,
    product_name: PRODUCT.name,
    movement_type: 'receive',
    quantity: 24,
    reference: 'PO-5510',
    note: 'Initial stock',
    created_at: '2026-01-06T08:00:00Z',
  },
];

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
): Promise<NetworkMonitor> {
  const monitor: NetworkMonitor = { external: [], failed: [] };

  page.on('requestfailed', (request) => {
    const url = new URL(request.url());
    if (url.origin === BASE_URL) {
      monitor.failed.push(
        `${request.method()} ${url.pathname}: ${request.failure()?.errorText ?? 'failed'}`,
      );
    }
  });

  await page.route('**/*', async (route: Route) => {
    const request = route.request();
    const url = new URL(request.url());

    // Local, in-memory resources are always fine.
    if (url.protocol === 'data:' || url.protocol === 'blob:') {
      await route.continue();
      return;
    }

    // The offline guarantee: anything outside the preview origin is blocked.
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

    if (path === '/api/suppliers/') {
      await route.fulfill(json(SUPPLIERS));
      return;
    }

    if (path === '/api/categories/') {
      if (method === 'POST') {
        await route.fulfill(json({ id: 1, name: 'Drinks', active: true }, 201));
        return;
      }
      await route.fulfill(json([{ id: 1, name: 'Drinks', active: true }]));
      return;
    }

    if (path === '/api/inventory/') {
      if (options.movementsGate) {
        await options.movementsGate;
      }
      if (options.movements === 'error') {
        await route.fulfill(
          json({ detail: 'Failed to load stock movements' }, 500),
        );
        return;
      }
      await route.fulfill(
        json(options.movements === 'empty' ? [] : MOVEMENTS),
      );
      return;
    }

    if (path === '/api/products/') {
      if (method === 'POST') {
        await route.fulfill(json(PRODUCT, 201));
        return;
      }
      await route.fulfill(json([PRODUCT]));
      return;
    }

    if (path === '/api/products/7/') {
      if (method === 'PATCH') {
        await route.fulfill(json(PRODUCT));
        return;
      }
      if (method === 'DELETE') {
        await route.fulfill(json({}, 204));
        return;
      }
      await route.fulfill(json(PRODUCT));
      return;
    }

    if (path === '/api/products/7/receive/') {
      await route.fulfill(json({ movement_id: 99, quantity: 29 }, 201));
      return;
    }

    await route.fulfill(json({ detail: `No mock for ${method} ${path}` }, 404));
  });

  return monitor;
}

async function openApp(
  page: Page,
  path: string,
  options: MockOptions = {},
): Promise<NetworkMonitor> {
  const monitor = await installApiMocks(page, options);
  await page.goto(`${BASE_URL}${path}`);
  await expect(page.locator('.app-shell')).toBeVisible();
  await page.evaluate(async () => {
    await document.fonts.ready;
  });
  return monitor;
}

function expectNoNetworkIssues(monitor: NetworkMonitor) {
  expect(
    monitor.external,
    `external requests were attempted: ${monitor.external.join(', ')}`,
  ).toEqual([]);
  expect(
    monitor.failed,
    `same-origin requests failed: ${monitor.failed.join(', ')}`,
  ).toEqual([]);
}

function expectStacked(
  first: { x: number; y: number; width: number; height: number },
  second: { x: number; y: number; width: number; height: number },
) {
  expect(second.y).toBeGreaterThanOrEqual(first.y + first.height - 1);
  expect(Math.abs(second.x - first.x)).toBeLessThanOrEqual(1);
  expect(Math.abs(second.width - first.width)).toBeLessThanOrEqual(1);
}

test('renders the desktop create form with MaterialM primitives', async ({ page }) => {
  const monitor = await openApp(page, '/inventory/new');

  await expect(
    page.getByRole('heading', { level: 1, name: 'New product' }),
  ).toBeVisible();
  await expect(
    page.getByRole('button', { name: 'Back to inventory' }),
  ).toBeVisible();

  const nameInput = page.getByRole('textbox', { name: 'Name', exact: true });
  await expect(nameInput).toBeVisible();
  await expect(nameInput).toHaveCSS('border-radius', '8px');
  await expect(nameInput).toHaveCSS('height', '40px');

  const nameLabel = page.locator('.mm-label').first();
  await expect(nameLabel).toHaveCSS('font-size', '13px');
  await expect(nameLabel).toHaveCSS('font-weight', '600');
  await expect(page.locator('.required').first()).toHaveCSS(
    'color',
    'rgb(194, 24, 91)',
  );

  const supplierSelect = page.getByRole('combobox', { name: 'Supplier' });
  await expect(supplierSelect).toHaveValue('');
  await expect(supplierSelect.locator('option')).toHaveCount(3);
  await expect(supplierSelect).toContainText('Harbor Dairy');
  await expect(supplierSelect).toContainText('Highland Roasters');
  // Inactive suppliers are filtered out, as before.
  await expect(supplierSelect).not.toContainText('Old Mill Supply');

  await expect(
    page.getByRole('checkbox', { name: 'Active (visible in sale screen)' }),
  ).toBeChecked();
  await expect(
    page.getByRole('spinbutton', { name: 'Initial stock', exact: true }),
  ).toHaveValue('0');
  await expect(
    page.getByRole('spinbutton', { name: 'Initial stock', exact: true }),
  ).toHaveCSS('border-radius', '8px');

  const primary = page.getByRole('button', { name: 'Create product' });
  await expect(primary).toHaveCSS('background-color', 'rgb(0, 114, 196)');
  await expect(primary).toHaveCSS('color', 'rgb(255, 255, 255)');
  await expect(primary).toHaveCSS('border-radius', '8px');
  await expect(primary).toHaveCSS('height', '40px');

  const card = page.locator('.product-form');
  await expect(card).toHaveCSS('background-color', 'rgb(255, 255, 255)');
  await expect(card).toHaveCSS('border-radius', '24px');

  await expect(page.locator('.app-page')).toHaveScreenshot(
    'product-create.png',
    SNAPSHOT,
  );

  expectNoNetworkIssues(monitor);
});

test('renders the desktop edit form side-by-side with movement badges', async ({
  page,
}) => {
  const monitor = await openApp(page, '/inventory/product/7');

  await expect(
    page.getByRole('heading', { level: 1, name: 'Edit product' }),
  ).toBeVisible();
  await expect(
    page.getByRole('button', { name: 'Back to inventory' }),
  ).toBeVisible();
  await expect(page.getByRole('button', { name: 'Delete product' })).toBeVisible();

  await expect(
    page.getByRole('textbox', { name: 'Name', exact: true }),
  ).toHaveValue(PRODUCT.name);
  await expect(page.getByRole('combobox', { name: 'Supplier' })).toHaveValue(
    '2',
  );
  await expect(
    page.getByRole('spinbutton', { name: 'Retail price', exact: true }),
  ).toHaveValue('18.50');
  await expect(
    page.getByRole('spinbutton', { name: 'Cost', exact: true }),
  ).toHaveValue('9.25');
  await expect(
    page.getByRole('spinbutton', { name: 'Min stock', exact: true }),
  ).toHaveValue('10');
  // Initial stock only exists on create.
  await expect(
    page.getByRole('spinbutton', { name: 'Initial stock', exact: true }),
  ).toHaveCount(0);

  const formBox = await page.locator('.product-form').boundingBox();
  const movementsBox = await page.locator('.movements-section').boundingBox();
  expect(formBox).not.toBeNull();
  expect(movementsBox).not.toBeNull();
  // Desktop keeps the split layout: movements to the right of the form.
  expect(movementsBox!.x).toBeGreaterThan(formBox!.x + formBox!.width);
  expect(Math.abs(movementsBox!.y - formBox!.y)).toBeLessThanOrEqual(1);

  const table = page.locator('.product-movements');
  await expect(table.locator('tbody tr')).toHaveCount(4);
  await expect(table.locator('th').first()).toHaveCSS(
    'background-color',
    'rgb(242, 246, 250)',
  );
  await expect(table.locator('th').first()).toHaveCSS('color', 'rgb(100, 112, 133)');
  await expect(table.locator('th').first()).toHaveCSS('font-size', '13px');

  const badge = (label: string) =>
    table.locator('.mm-badge', { hasText: label });

  await expect(badge('Sale')).toHaveCSS('color', 'rgb(83, 155, 255)');
  await expect(badge('Sale')).toHaveCSS(
    'background-color',
    'rgba(83, 155, 255, 0.15)',
  );
  await expect(badge('Receive')).toHaveCSS('color', 'rgb(0, 127, 112)');
  await expect(badge('Receive')).toHaveCSS(
    'background-color',
    'rgba(0, 206, 182, 0.15)',
  );
  await expect(badge('Damage')).toHaveCSS('color', 'rgb(194, 24, 91)');
  await expect(badge('Damage')).toHaveCSS(
    'background-color',
    'rgba(255, 102, 146, 0.15)',
  );
  await expect(badge('Adjustment')).toHaveCSS('color', 'rgb(138, 90, 0)');
  await expect(badge('Adjustment')).toHaveCSS(
    'background-color',
    'rgba(255, 174, 31, 0.15)',
  );
  await expect(badge('Sale')).toHaveCSS('border-radius', '999px');

  await expect(table).toContainText('1/8/2026, 10:30:00');
  await expect(table).toContainText('PO-5510');
  await expect(table.locator('tbody tr').nth(2).locator('.num')).toHaveText(
    '-2',
  );
  await expect(table.locator('tbody tr').nth(3).locator('.num')).toHaveText(
    '+24',
  );

  await expect(page.locator('.app-page')).toHaveScreenshot(
    'product-edit.png',
    SNAPSHOT,
  );

  expectNoNetworkIssues(monitor);
});

test('stacks the edit form on mobile and captures the layout', async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const monitor = await openApp(page, '/inventory/product/7');

  await expect(
    page.getByRole('heading', { level: 1, name: 'Edit product' }),
  ).toBeVisible();

  const formBox = await page.locator('.product-form').boundingBox();
  const movementsBox = await page.locator('.movements-section').boundingBox();
  expect(formBox).not.toBeNull();
  expect(movementsBox).not.toBeNull();
  expectStacked(formBox!, movementsBox!);

  const fieldRowColumns = await page.locator('.field-row').last().evaluate((element) => {
    return getComputedStyle(element).gridTemplateColumns.split(' ').length;
  });
  expect(fieldRowColumns).toBe(1);

  await expect(page.locator('.mm-badge', { hasText: 'Receive' })).toBeVisible();
  await expect(page.locator('.product-movements')).toBeVisible();

  await expect(page.locator('.app-page')).toHaveScreenshot(
    'product-edit-mobile.png',
    SNAPSHOT,
  );

  expectNoNetworkIssues(monitor);
});

test('shows shared loading, empty, and error treatments for movements', async ({
  page,
}) => {
  let releaseMovements!: () => void;
  const movementsGate = new Promise<void>((resolve) => {
    releaseMovements = resolve;
  });

  const monitor = await openApp(page, '/inventory/product/7', {
    movementsGate,
  });

  await expect(page.locator('.movements-section .mm-loading')).toBeVisible();
  await expect(
    page.locator('.movements-section .mm-spinner'),
  ).toBeVisible();

  releaseMovements();
  await expect(page.locator('.product-movements')).toBeVisible();
  await expect(page.locator('.product-movements tbody tr')).toHaveCount(4);

  expectNoNetworkIssues(monitor);
});

test('renders the movements empty state with the shared empty primitive', async ({
  page,
}) => {
  const monitor = await openApp(page, '/inventory/product/7', {
    movements: 'empty',
  });

  await expect(page.locator('.movements-empty')).toBeVisible();
  await expect(page.locator('.movements-empty')).toContainText(
    'No stock movements yet',
  );
  await expect(page.locator('.product-movements')).toHaveCount(0);

  expectNoNetworkIssues(monitor);
});

test('renders movement errors with the shared alert primitive', async ({
  page,
}) => {
  const monitor = await openApp(page, '/inventory/product/7', {
    movements: 'error',
  });

  const alert = page.locator('.movements-section .mm-alert--danger');
  await expect(alert).toBeVisible();
  await expect(alert).toContainText('Failed to load stock movements');
  await expect(page.locator('.product-movements')).toHaveCount(0);

  expectNoNetworkIssues(monitor);
});

test('creates a product and posts initial stock before returning to inventory', async ({
  page,
}) => {
  const monitor = await openApp(page, '/inventory/new');

  await page
    .getByRole('textbox', { name: 'Name', exact: true })
    .fill('House Blend');
  await page.getByRole('combobox', { name: 'Category' }).selectOption('1');
  await page.getByRole('combobox', { name: 'Supplier' }).selectOption('1');
  await page
    .getByRole('spinbutton', { name: 'Retail price', exact: true })
    .fill('12.00');
  await page
    .getByRole('spinbutton', { name: 'Cost', exact: true })
    .fill('6.50');
  await page
    .getByRole('spinbutton', { name: 'Min stock', exact: true })
    .fill('8');
  await page
    .getByRole('spinbutton', { name: 'Initial stock', exact: true })
    .fill('5');
  await page
    .getByRole('checkbox', { name: 'Active (visible in sale screen)' })
    .uncheck();

  const createRequest = page.waitForRequest(
    (request) =>
      request.url() === `${BASE_URL}/api/products/` &&
      request.method() === 'POST',
  );
  const receiveRequest = page.waitForRequest(
    (request) =>
      request.url().endsWith('/api/products/7/receive/') &&
      request.method() === 'POST',
  );

  await page.getByRole('button', { name: 'Create product' }).click();

  const created = await createRequest;
  expect(created.postDataJSON()).toEqual({
    name: 'House Blend',
    category: 1,
    description: '',
    price: '12.00',
    cost: '6.50',
    min_stock: 8,
    markup_margin: '10',
    supplier: 1,
    active: false,
    non_stock: false,
  });

  const received = await receiveRequest;
  expect(received.postDataJSON()).toEqual({
    quantity: 5,
    note: 'Initial stock',
  });

  await expect(page).toHaveURL(`${BASE_URL}/inventory`);
  expectNoNetworkIssues(monitor);
});
