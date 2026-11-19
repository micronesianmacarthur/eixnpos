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
  /** Return ``password_change_required: true`` from the login endpoint. */
  passwordChangeRequired?: boolean;
  /** Abort the login POST at the network layer (simulates offline/backend down). */
  loginAbort?: boolean;
  /** Replace the successful login response body entirely (for contract edge cases). */
  loginResponseOverride?: Record<string, unknown>;
  /** Force ``me/`` to report ``must_change_password: true`` (drives the guard). */
  meMustChangePassword?: boolean;
  loginDelayMs?: number;
  businessPostDelayMs?: number;
}

interface LoginRequest {
  method: string;
  path: string;
  /** Parsed request body, or null when the body was empty. */
  body: Record<string, unknown> | null;
  contentType: string | null;
  csrfHeader: string | null;
}

interface NetworkMonitor {
  external: string[];
  failed: string[];
  loginRequests: LoginRequest[];
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
  const monitor: NetworkMonitor = { external: [], failed: [], loginRequests: [] };
  let businessComplete = options.businessInfoComplete ?? true;
  const meMustChange =
    options.meMustChangePassword ?? options.passwordChangeRequired ?? false;

  // Seed a session-auth CSRF cookie so login (an unsafe POST) actually carries
  // the X-CSRFToken header the real same-origin session flow would send.
  await page.context().addCookies([
    { name: 'csrftoken', value: 'test-csrf-token', url: BASE_URL },
  ]);

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
          must_change_password: meMustChange,
          business_info_complete: businessComplete,
        }),
      );
      return;
    }

    if (path === '/api/auth/login/') {
      // Record exactly what the UI submitted so a test can assert the payload
      // and endpoint are correct ("sends the correct authentication request").
      let parsed: Record<string, unknown> | null = null;
      const rawBody = request.postData();
      if (rawBody && rawBody.trim()) {
        try {
          parsed = JSON.parse(rawBody);
        } catch {
          parsed = null;
        }
      }
      monitor.loginRequests.push({
        method: request.method().toUpperCase(),
        path,
        body: parsed,
        contentType: request.headers()['content-type'] ?? null,
        csrfHeader: request.headers()['x-csrftoken'] ?? null,
      });

      // Simulate a network-level failure (server offline / request aborted).
      if (options.loginAbort) {
        await route.abort('connectionrefused');
        return;
      }

      if (options.loginDelayMs) {
        await delay(options.loginDelayMs);
      }
      if (options.loginFailure) {
        // Real backend rejects bad credentials with HTTP 400 + a `detail`
        // body (accounts/views.login_view); mirror that exactly.
        await route.fulfill(json({ detail: 'Invalid credentials' }, 400));
        return;
      }
      if (options.passwordChangeRequired) {
        await route.fulfill(
          json({
            detail: 'ok',
            ...AUTH_ME,
            password_change_required: true,
          }),
        );
        return;
      }
      if (options.loginResponseOverride) {
        await route.fulfill(
          json({ detail: 'ok', ...options.loginResponseOverride }),
        );
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

test('submits the correct credentials to the auth endpoint and redirects', async ({
  page,
}) => {
  const monitor = await openLogin(page);

  await page.getByLabel('Username:').fill('maya');
  await page.getByLabel('Password:').fill('secret');
  await page.getByRole('button', { name: 'Login' }).click();

  await expect(
    page.getByRole('heading', { level: 1, name: 'Dashboard' }),
  ).toBeVisible();

  // Exactly one login request, aimed at the right endpoint, carrying the right
  // fields as a JSON POST with the session-auth CSRF header attached.
  expect(monitor.loginRequests).toHaveLength(1);
  const login = monitor.loginRequests[0];
  expect(login.method).toBe('POST');
  expect(login.path).toBe('/api/auth/login/');
  expect(login.body).toEqual({ username: 'maya', password: 'secret' });
  expect(login.contentType).toContain('application/json');
  expect(login.csrfHeader).toBeTruthy();

  expectNoNetworkIssues(monitor);
});

test('trims whitespace from the username before authenticating', async ({
  page,
}) => {
  const monitor = await openLogin(page);

  // A valid username with accidental surrounding spaces must still authenticate:
  // the UI trims and only the canonical username is submitted.
  await page.getByLabel('Username:').fill('  maya  ');
  await page.getByLabel('Password:').fill('secret');
  await page.getByRole('button', { name: 'Login' }).click();

  await expect(
    page.getByRole('heading', { level: 1, name: 'Dashboard' }),
  ).toBeVisible();
  expect(monitor.loginRequests).toHaveLength(1);
  expect(monitor.loginRequests[0].body).toEqual({
    username: 'maya',
    password: 'secret',
  });

  expectNoNetworkIssues(monitor);
});

test('redirects to the forced password-change screen after login', async ({
  page,
}) => {
  const monitor = await openLogin(page, { passwordChangeRequired: true });

  await page.getByLabel('Username:').fill('maya');
  await page.getByLabel('Password:').fill('admin');
  await page.getByRole('button', { name: 'Login' }).click();

  // The post-login redirect targets the forced change screen, not the dashboard.
  await expect(
    page.getByRole('heading', { name: 'Change your password' }),
  ).toBeVisible();
  await expect(
    page.getByRole('heading', { level: 1, name: 'Dashboard' }),
  ).toHaveCount(0);

  expect(monitor.loginRequests).toHaveLength(1);
  expectNoNetworkIssues(monitor);
});

test('honours the transitional must_change_password login field for the redirect', async ({
  page,
}) => {
  // A server that still echoes the bare `must_change_password` field (instead of
  // the contracted `password_change_required`) must still route forced changes.
  const monitor = await openLogin(page, {
    loginResponseOverride: { ...AUTH_ME, must_change_password: true },
    meMustChangePassword: true, // me/ must report the flag as true too
  });

  await page.getByLabel('Username:').fill('maya');
  await page.getByLabel('Password:').fill('admin');
  await page.getByRole('button', { name: 'Login' }).click();

  await expect(
    page.getByRole('heading', { name: 'Change your password' }),
  ).toBeVisible();

  expect(monitor.loginRequests).toHaveLength(1);
  expectNoNetworkIssues(monitor);
});

test('shows a Network error and re-enables the form when login cannot reach the server', async ({
  page,
}) => {
  await openLogin(page, { loginAbort: true });

  await page.getByLabel('Username:').fill('maya');
  await page.getByLabel('Password:').fill('secret');
  await page.getByRole('button', { name: 'Login' }).click();

  const alert = page.getByRole('alert');
  await expect(alert).toHaveText('Network error');
  // The request never reached the server, so no misleading "invalid
  // credentials" is shown and the form is usable again.
  await expect(page.getByRole('button', { name: 'Login' })).toBeEnabled();
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
