import { expect, test, type Page, type Route } from '@playwright/test';

const BASE_URL =
  process.env.EIXNPOS_BASE_URL ?? 'http://127.0.0.1:4173';

const SNAPSHOT = {
  animations: 'disabled' as const,
  caret: 'hide' as const,
  scale: 'css' as const,
  maxDiffPixelRatio: 0.01,
};

interface MockOptions {
  businessInfoComplete?: boolean;
  loginFailure?: boolean;
  loginDelayMs?: number;
  businessPostDelayMs?: number;
}

interface NetworkMonitor {
  external: string[];
  failed: string[];
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

function delay(ms: number) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function installApiMocks(
  page: Page,
  options: MockOptions = {},
): Promise<NetworkMonitor> {
  const monitor: NetworkMonitor = { external: [], failed: [] };
  let businessComplete = options.businessInfoComplete ?? true;

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
      await route.fulfill(
        json({
          ...AUTH_ME,
          business_info_complete: businessComplete,
        }),
      );
      return;
    }

    if (path === '/api/auth/login/') {
      if (options.loginDelayMs) {
        await delay(options.loginDelayMs);
      }
      if (options.loginFailure) {
        await route.fulfill(json({ detail: 'Invalid credentials' }, 401));
        return;
      }
      await route.fulfill(json({ detail: 'ok' }));
      return;
    }

    if (path === '/api/business/') {
      if (method === 'POST') {
        if (options.businessPostDelayMs) {
          await delay(options.businessPostDelayMs);
        }
        businessComplete = true;
        await route.fulfill(json(BUSINESS_INFO));
        return;
      }
      await route.fulfill(json(BUSINESS_INFO));
      return;
    }

    // Fixtures for the shell so this spec can never fall through to a real
    // backend. Only the auth screens are exercised here.
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

async function waitForFonts(page: Page) {
  await page.evaluate(async () => {
    await document.fonts.ready;
  });
}

async function openLogin(
  page: Page,
  options: MockOptions = {},
): Promise<NetworkMonitor> {
  const monitor = await installApiMocks(page, options);
  await page.goto(`${BASE_URL}/login`);
  await expect(page.locator('.auth-card')).toBeVisible();
  await waitForFonts(page);
  return monitor;
}

async function openBusinessInfo(
  page: Page,
  options: MockOptions = {},
): Promise<NetworkMonitor> {
  const monitor = await installApiMocks(page, {
    ...options,
    businessInfoComplete: false,
  });
  await page.goto(`${BASE_URL}/`);
  await expect(page.locator('.app-shell')).toBeVisible();
  await expect(page.locator('.business-form')).toBeVisible();
  await expect(page.getByLabel('Company Name:')).toHaveValue(
    BUSINESS_INFO.company_name,
  );
  await waitForFonts(page);
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

async function expectNoInlineStyles(page: Page, selector: string) {
  const styledNodes = await page.locator(selector).evaluateAll((nodes) =>
    nodes
      .filter((node) => node.hasAttribute('style'))
      .map((node) => node.outerHTML.slice(0, 120)),
  );
  expect(styledNodes, `inline styles found under ${selector}`).toEqual([]);
}

test('renders the Login auth card on desktop', async ({ page }) => {
  const monitor = await openLogin(page);

  await expect(
    page.getByRole('heading', { level: 1, name: 'Welcome back' }),
  ).toBeVisible();
  await expect(page.getByLabel('Username:')).toBeVisible();
  await expect(page.getByLabel('Password:')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Login' })).toBeEnabled();

  const card = page.locator('.auth-card');
  await expect(card).toHaveCSS('background-color', 'rgb(255, 255, 255)');
  await expect(card).toHaveCSS('border-radius', '24px');
  await expect(page.getByLabel('Username:')).toHaveCSS('border-radius', '8px');
  await expect(
    page.getByRole('button', { name: 'Login' }),
  ).toHaveCSS('background-color', 'rgb(0, 114, 196)');

  await expectNoInlineStyles(page, '.auth-page, .auth-page *');

  const fontState = await page.evaluate(async () => {
    await document.fonts.ready;
    return {
      interAvailable: document.fonts.check('16px Inter'),
      bodyFont: getComputedStyle(document.body).fontFamily,
    };
  });
  expect(fontState.interAvailable).toBe(true);
  expect(fontState.bodyFont).toContain('Inter');

  await expect(page).toHaveScreenshot('auth-login-desktop.png', SNAPSHOT);
  expectNoNetworkIssues(monitor);
});

test('renders the Login auth card on mobile', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const monitor = await openLogin(page);

  const card = page.locator('.auth-card');
  const box = await card.boundingBox();
  expect(box).not.toBeNull();
  expect(box?.x ?? 0).toBeGreaterThanOrEqual(16);
  expect((box?.x ?? 0) + (box?.width ?? 0)).toBeLessThanOrEqual(374);

  await expect(page).toHaveScreenshot('auth-login-mobile.png', SNAPSHOT);
  expectNoNetworkIssues(monitor);
});

test('shows the themed Login error alert and re-enables the form', async ({
  page,
}) => {
  const monitor = await openLogin(page, { loginFailure: true });

  await page.getByLabel('Username:').fill('maya');
  await page.getByLabel('Password:').fill('wrong-password');
  await page.getByRole('button', { name: 'Login' }).click();

  const alert = page.getByRole('alert');
  await expect(alert).toHaveText('Invalid credentials');
  await expect(alert).toHaveCSS('color', 'rgb(194, 24, 91)');
  await expect(page.getByRole('button', { name: 'Login' })).toBeEnabled();

  expectNoNetworkIssues(monitor);
});

test('shows the Login loading state and navigates on success', async ({
  page,
}) => {
  const monitor = await openLogin(page, { loginDelayMs: 300 });

  await page.getByLabel('Username:').fill('maya');
  await page.getByLabel('Password:').fill('secret');
  await page.getByRole('button', { name: 'Login' }).click();

  const submitting = page.getByRole('button', { name: 'Logging in...' });
  await expect(submitting).toBeVisible();
  await expect(submitting).toBeDisabled();

  await expect(
    page.getByRole('heading', { level: 1, name: 'Dashboard' }),
  ).toBeVisible();
  expectNoNetworkIssues(monitor);
});

test('renders Business Information on desktop', async ({ page }) => {
  const monitor = await openBusinessInfo(page);

  await expect(
    page.getByRole('heading', { name: 'Business Information' }),
  ).toBeVisible();
  await expect(page.getByLabel('Phone:')).toHaveValue(BUSINESS_INFO.phone);
  await expect(page.getByLabel('Email:')).toHaveValue(BUSINESS_INFO.email);
  await expect(
    page.getByRole('button', { name: 'Save Business Info' }),
  ).toBeEnabled();

  const card = page.locator('.business-form');
  await expect(card).toHaveCSS('background-color', 'rgb(255, 255, 255)');
  await expect(card).toHaveCSS('border-radius', '24px');
  await expect(
    page.getByRole('button', { name: 'Save Business Info' }),
  ).toHaveCSS('background-color', 'rgb(0, 114, 196)');

  await expectNoInlineStyles(page, '.business-form, .business-form *');

  await expect(page.locator('.app-page')).toHaveScreenshot(
    'business-info-desktop.png',
    SNAPSHOT,
  );
  expectNoNetworkIssues(monitor);
});

test('renders Business Information on mobile', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const monitor = await openBusinessInfo(page);

  await expect(page.getByLabel('Company Name:')).toBeVisible();
  await expect(page.getByLabel('Phone:')).toBeVisible();
  await expect(page.getByLabel('Email:')).toBeVisible();

  await expect(page.locator('.app-page')).toHaveScreenshot(
    'business-info-mobile.png',
    SNAPSHOT,
  );
  expectNoNetworkIssues(monitor);
});

test('saves Business Information and clears the first-run prompt', async ({
  page,
}) => {
  const monitor = await openBusinessInfo(page, { businessPostDelayMs: 300 });

  await page.getByLabel('Company Name:').fill('eixn New Store');
  await page.getByRole('button', { name: 'Save Business Info' }).click();

  const submitting = page.getByRole('button', { name: 'Saving...' });
  await expect(submitting).toBeVisible();
  await expect(submitting).toBeDisabled();

  await expect(
    page.getByText(
      'Complete your business information so it appears on receipts.',
    ),
  ).toHaveCount(0);
  await expect(page.locator('.business-form')).toHaveCount(0);
  expectNoNetworkIssues(monitor);
});
