import { expect, test, type Page, type Route } from '@playwright/test';

const BASE_URL = 'http://127.0.0.1:4173';

const SNAPSHOT = {
  animations: 'disabled' as const,
  caret: 'hide' as const,
  scale: 'css' as const,
  maxDiffPixelRatio: 0.01,
};

type Role = 'cashier' | 'manager' | 'admin';

interface MockOptions {
  businessInfoComplete?: boolean;
  role?: Role;
}

interface NetworkMonitor {
  external: string[];
  failed: string[];
}

const AUTH_ME = {
  user: {
    id: 1,
    username: 'maya',
    role: 'admin' as Role,
  },
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
  const role = options.role ?? 'admin';
  const authMe = {
    ...AUTH_ME,
    user: { ...AUTH_ME.user, role },
    business_info_complete: options.businessInfoComplete ?? true,
  };

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
      await route.fulfill(json(authMe));
      return;
    }

    if (path === '/api/auth/login/') {
      await route.fulfill(json({ detail: 'ok' }));
      return;
    }

    if (path === '/api/business/') {
      if (method === 'POST') {
        await route.fulfill(json(BUSINESS_INFO));
        return;
      }
      await route.fulfill(json(BUSINESS_INFO));
      return;
    }

    // Fixtures for the rest of the app so the shell can never fall through to
    // a real backend during this suite.
    const listFixtures: Record<string, unknown[]> = {
      '/api/products/': [],
      '/api/customers/': [],
      '/api/suppliers/': [],
      '/api/users/': [],
      '/api/inventory/': [],
      '/api/inventory/movements/': [],
      '/api/sales/': [],
    };

    if (path in listFixtures) {
      await route.fulfill(json(listFixtures[path]));
      return;
    }

    await route.fulfill(json({ detail: `No mock for ${method} ${path}` }, 404));
  });

  return monitor;
}

async function openApp(
  page: Page,
  path = '/',
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

test('bundles Inter locally and applies the MaterialM shell tokens', async ({
  page,
}) => {
  const monitor = await openApp(page);
  await expect(
    page.getByRole('heading', { level: 1, name: 'Dashboard' }),
  ).toBeVisible();

  const fontState = await page.evaluate(async () => {
    await document.fonts.ready;
    return {
      interAvailable: document.fonts.check('16px Inter'),
      bodyFont: getComputedStyle(document.body).fontFamily,
    };
  });

  expect(fontState.interAvailable).toBe(true);
  expect(fontState.bodyFont).toContain('Inter');

  const headingFont = await page
    .locator('.mm-page-title')
    .evaluate((element) => getComputedStyle(element).fontFamily);
  expect(headingFont).toContain('Inter');

  await expect(page.locator('body')).toHaveCSS(
    'background-color',
    'rgb(248, 250, 253)',
  );
  await expect(page.locator('body')).toHaveCSS('font-size', '14px');
  await expect(page.locator('body')).toHaveCSS('color', 'rgb(17, 28, 45)');

  const card = page.locator('.mm-card').first();
  await expect(card).toHaveCSS('background-color', 'rgb(255, 255, 255)');
  await expect(card).toHaveCSS('border-radius', '24px');
  const cardShadow = await card.evaluate(
    (element) => getComputedStyle(element).boxShadow,
  );
  expect(cardShadow).not.toBe('none');

  const sidebar = page.locator('.sidebar');
  await expect(sidebar).toHaveCSS('width', '265px');
  await expect(page.locator('.app-header')).toHaveCSS('height', '70px');
  await expect(page.locator('.app-page')).toHaveCSS('max-width', '1200px');
  await expect(page.locator('.app-page')).toHaveCSS('padding-left', '30px');

  const activeLink = page.locator('.sidebar-link.active');
  await expect(activeLink).toContainText('Dashboard');
  await expect(activeLink).toHaveCSS('height', '43px');
  await expect(activeLink).toHaveCSS('border-radius', '24px');
  await expect(activeLink).toHaveCSS(
    'background-color',
    'rgba(0, 161, 255, 0.15)',
  );

  await expect(page.locator('.app-user-name')).toHaveText('maya');
  await expect(page.locator('.app-user-role')).toHaveText('admin');
  await expect(
    page.getByRole('button', { name: /theme|dark mode|light mode/i }),
  ).toHaveCount(0);
  await expect(page.getByText(/dark mode/i)).toHaveCount(0);

  await page.getByRole('button', { name: 'Collapse menu' }).click();
  await expect(sidebar).toHaveCSS('width', '80px');

  await page.evaluate(() => {
    const button = document.createElement('button');
    button.id = 'primary-probe';
    button.className = 'mm-btn mm-btn--primary';
    button.textContent = 'Primary';
    document.body.appendChild(button);

    const input = document.createElement('input');
    input.id = 'input-probe';
    input.className = 'mm-input';
    document.body.appendChild(input);
  });

  await expect(page.locator('#primary-probe')).toHaveCSS(
    'background-color',
    'rgb(0, 161, 255)',
  );
  await expect(page.locator('#primary-probe')).toHaveCSS(
    'color',
    'rgb(255, 255, 255)',
  );
  await expect(page.locator('#primary-probe')).toHaveCSS(
    'border-radius',
    '8px',
  );
  await expect(page.locator('#primary-probe')).toHaveCSS('height', '40px');
  await expect(page.locator('#input-probe')).toHaveCSS(
    'border-radius',
    '8px',
  );
  await expect(page.locator('#input-probe')).toHaveCSS(
    'border-color',
    'rgb(228, 235, 240)',
  );
  await expect(page.locator('#input-probe')).toHaveCSS('height', '40px');

  expectNoNetworkIssues(monitor);
});

test('collapses the sidebar by default on tablet and allows manual expand', async ({
  page,
}) => {
  await page.setViewportSize({ width: 900, height: 900 });
  const monitor = await openApp(page);

  const sidebar = page.locator('.sidebar');
  await expect(sidebar).toHaveCSS('width', '80px');

  await page.getByRole('button', { name: 'Expand menu' }).click();
  await expect(sidebar).toHaveCSS('width', '265px');

  expectNoNetworkIssues(monitor);
});

test('opens the mobile navigation drawer and shows the shell snapshot', async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const monitor = await openApp(page);

  const sidebar = page.locator('.sidebar');
  const closedBox = await sidebar.boundingBox();
  expect(closedBox).not.toBeNull();
  expect(closedBox?.x ?? 0).toBeLessThan(0);

  await page.getByRole('button', { name: 'Open menu' }).click();
  await expect(sidebar).toHaveClass(/mobile-open/);
  await expect(page.locator('.sidebar-backdrop')).toBeVisible();
  const closeButton = page.getByRole('button', { name: 'Close menu' });
  await expect(closeButton).toBeVisible();

  const toggleBox = await closeButton.boundingBox();
  const brandBox = await page.locator('.sidebar-brand').boundingBox();
  expect(toggleBox).not.toBeNull();
  expect(brandBox).not.toBeNull();
  expect((toggleBox?.x ?? 0) + (toggleBox?.width ?? 0)).toBeLessThanOrEqual(
    brandBox?.x ?? 0,
  );

  await expect(page).toHaveScreenshot(
    'app-shell-mobile-nav-open.png',
    SNAPSHOT,
  );

  await page.mouse.click(350, 420);
  await expect(sidebar).not.toHaveClass(/mobile-open/);

  expectNoNetworkIssues(monitor);
});

test('captures the desktop app shell and Dashboard', async ({ page }) => {
  const monitor = await openApp(page);
  await expect(
    page.getByRole('heading', { level: 1, name: 'Dashboard' }),
  ).toBeVisible();

  await expect(page.getByRole('article')).toHaveCount(3);
  await expect(page.locator('.dashboard-card').nth(0)).toContainText('Sale');
  await expect(page.locator('.dashboard-card').nth(1)).toContainText(
    'Inventory',
  );
  await expect(page.locator('.dashboard-card').nth(2)).toContainText('Reports');

  await expect(page).toHaveScreenshot('app-shell-desktop.png', SNAPSHOT);
  await expect(page.locator('.app-page')).toHaveScreenshot(
    'dashboard.png',
    SNAPSHOT,
  );

  expectNoNetworkIssues(monitor);
});

test('keeps the Business Information prompt and form', async ({ page }) => {
  const monitor = await openApp(page, '/', { businessInfoComplete: false });

  await expect(
    page.getByText(
      'Complete your business information so it appears on receipts.',
    ),
  ).toBeVisible();
  await expect(
    page.getByRole('heading', { name: 'Business Information' }),
  ).toBeVisible();
  await expect(page.getByLabel('Company Name:')).toBeVisible();
  await expect(
    page.getByRole('button', { name: 'Save Business Info' }),
  ).toBeVisible();

  expectNoNetworkIssues(monitor);
});

const PLACEHOLDERS = [
  ['/sales-manager', 'Sales Manager'],
  ['/suppliers', 'Suppliers'],
  ['/close', 'Close'],
] as const;

for (const [path, title] of PLACEHOLDERS) {
  test(`renders the ${title} placeholder`, async ({ page }) => {
    const monitor = await openApp(page, path);

    await expect(
      page.getByRole('heading', { level: 1, name: title }),
    ).toBeVisible();
    await expect(page.locator('.mm-page-subtitle')).not.toBeEmpty();
    const emptyCard = page.locator('.mm-card.mm-empty');
    await expect(emptyCard).toBeVisible();
    await expect(emptyCard).toContainText('Nothing here yet');

    await expect(page.locator('.app-page')).toHaveScreenshot(
      `placeholder-${path.slice(1)}.png`,
      SNAPSHOT,
    );

    expectNoNetworkIssues(monitor);
  });
}
