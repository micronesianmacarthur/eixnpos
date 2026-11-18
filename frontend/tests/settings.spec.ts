import { expect, test, type Page, type Route } from '@playwright/test';

const BASE_URL = 'http://127.0.0.1:4173';

const SNAPSHOT = {
  animations: 'disabled' as const,
  caret: 'hide' as const,
  scale: 'css' as const,
  maxDiffPixelRatio: 0.01,
};

interface NetworkMonitor {
  external: string[];
  failed: string[];
  requests: Array<{ method: string; path: string; body?: string }>;
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
  website: 'https://eixn.example',
  backup_path: '/backup',
};

function json(data: unknown, status = 200) {
  return { status, contentType: 'application/json', body: JSON.stringify(data) };
}

async function installApiMocks(page: Page): Promise<NetworkMonitor> {
  const monitor: NetworkMonitor = { external: [], failed: [], requests: [] };

  page.on('requestfailed', (request) => {
    const url = new URL(request.url());
    if (url.origin === BASE_URL) {
      monitor.failed.push(`${request.method()} ${url.pathname}: ${request.failure()?.errorText ?? 'failed'}`);
    }
  });

  await page.route('**/*', async (route: Route) => {
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

    monitor.requests.push({ method, path, body: request.postData() ?? undefined });

    if (path === '/api/auth/me/') {
      await route.fulfill(json(AUTH_ME));
      return;
    }
    if (path === '/api/business/') {
      if (method === 'PATCH') {
        const body = JSON.parse(request.postData() || '{}');
        await route.fulfill(json({ ...BUSINESS_INFO, ...body }));
        return;
      }
      await route.fulfill(json(BUSINESS_INFO));
      return;
    }
    if (path === '/api/backups/trigger/') {
      await route.fulfill(json({ path: '/backup/eixnpos-20260101.sqlite3' }));
      return;
    }

    await route.fulfill(json({ detail: `No mock for ${method} ${path}` }, 404));
  });

  return monitor;
}

async function openSettings(page: Page): Promise<NetworkMonitor> {
  const monitor = await installApiMocks(page);
  await page.goto(`${BASE_URL}/settings`);
  await expect(page.locator('.app-shell')).toBeVisible();
  await expect(page.getByRole('heading', { level: 1, name: 'Settings' })).toBeVisible();
  await page.evaluate(async () => {
    await document.fonts.ready;
  });
  return monitor;
}

function expectNoNetworkIssues(monitor: NetworkMonitor) {
  expect(monitor.external, `external requests: ${monitor.external.join(', ')}`).toEqual([]);
  expect(monitor.failed, `failed requests: ${monitor.failed.join(', ')}`).toEqual([]);
}

test('renders Settings on desktop with Store tab active', async ({ page }) => {
  const monitor = await openSettings(page);

  const tabs = page.getByRole('tab');
  await expect(tabs).toHaveCount(3);
  await expect(tabs.nth(0)).toHaveText('Store');
  await expect(tabs.nth(1)).toHaveText('Backup');
  await expect(tabs.nth(2)).toHaveText('Appearance');

  // The Users tab was removed from Settings; user management lives on /users.
  await expect(page.getByRole('tab', { name: 'Users' })).toHaveCount(0);
  // No link inside the Settings page content points at the removed surface
  // (scoped to the page container: the app sidebar legitimately links to /users).
  await expect(page.locator('.app-page a[href="/users"]')).toHaveCount(0);

  // Store tab is active and pre-fills the API data.
  await expect(page.getByRole('tab', { name: 'Store' })).toHaveAttribute('aria-selected', 'true');
  await expect(page.getByLabel('Company name')).toHaveValue('eixn Corner Store');
  await expect(page.getByLabel('Phone')).toHaveValue('555-0100');
  await expect(page.getByLabel('Email')).toHaveValue('hello@eixn.test');
  await expect(page.getByLabel('Address')).toHaveValue('12 Market Street');
  await expect(page.getByLabel('Website')).toHaveValue('https://eixn.example');
  await expect(page.getByRole('button', { name: 'Save' })).toBeVisible();

  expectNoNetworkIssues(monitor);
  await expect(page.locator('.app-page')).toHaveScreenshot('settings-store.png', SNAPSHOT);
});

test('saves store details and shows inline success', async ({ page }) => {
  const monitor = await openSettings(page);

  await page.getByLabel('Company name').fill('New Corner Co');
  await page.getByRole('button', { name: 'Save' }).click();

  await expect(page.getByRole('status')).toHaveText('Store details saved.');
  expectNoNetworkIssues(monitor);
});

test('blocks saving store details when a required field is empty', async ({ page }) => {
  const monitor = await openSettings(page);

  await page.getByLabel('Company name').fill('');
  await page.getByRole('button', { name: 'Save' }).click();

  await expect(page.getByRole('alert')).toHaveText('Company name is required.');
  // No PATCH should fire for an invalid submission.
  expect(
    monitor.requests.filter((r) => r.method === 'PATCH' && r.path === '/api/business/'),
  ).toEqual([]);

  await page.getByLabel('Company name').fill('eixn Corner Store');
  await page.getByLabel('Phone').fill('');
  await page.getByRole('button', { name: 'Save' }).click();
  await expect(page.getByRole('alert')).toHaveText('Phone is required.');

  await page.getByLabel('Phone').fill('555-0100');
  await page.getByLabel('Email').fill('');
  await page.getByRole('button', { name: 'Save' }).click();
  await expect(page.getByRole('alert')).toHaveText('Email is required.');

  expectNoNetworkIssues(monitor);
});

test('cancelling unsaved store edits restores the persisted values', async ({ page }) => {
  const monitor = await openSettings(page);

  await page.getByLabel('Company name').fill('Should Not Persist');
  await page.getByLabel('Address').fill('99 Ghost Road');
  await page.getByRole('button', { name: 'Cancel' }).click();

  await expect(page.getByLabel('Company name')).toHaveValue('eixn Corner Store');
  await expect(page.getByLabel('Address')).toHaveValue('12 Market Street');

  // Cancelling did not touch the server.
  expect(
    monitor.requests.filter((r) => r.method === 'PATCH' && r.path === '/api/business/'),
  ).toEqual([]);
  expectNoNetworkIssues(monitor);
});

test('cancelling an unsaved backup path edit restores the persisted path', async ({ page }) => {
  const monitor = await openSettings(page);
  await page.getByRole('tab', { name: 'Backup' }).click();

  await page.getByLabel('Backup path').fill('/usb/should-not-save');
  await page.getByRole('button', { name: 'Cancel' }).click();

  await expect(page.getByLabel('Backup path')).toHaveValue('/backup');
  // No PATCH and no trigger fired on cancel.
  expect(
    monitor.requests.filter((r) => r.method === 'PATCH' && r.path === '/api/business/'),
  ).toEqual([]);
  expect(
    monitor.requests.filter((r) => r.method === 'POST' && r.path === '/api/backups/trigger/'),
  ).toEqual([]);
  expectNoNetworkIssues(monitor);
});

test('triggers a backup and shows the resulting path', async ({ page }) => {
  const monitor = await openSettings(page);
  await page.getByRole('tab', { name: 'Backup' }).click();

  await expect(page.getByLabel('Backup path')).toHaveValue('/backup');
  await expect(page.getByText(/Keeps last 7/)).toBeVisible();

  await page.getByRole('button', { name: 'Back up now' }).click();
  await expect(page.getByRole('status')).toHaveText(
    'Backup completed: /backup/eixnpos-20260101.sqlite3',
  );

  expectNoNetworkIssues(monitor);
});

test('toggles between light and dark theme from Appearance', async ({ page }) => {
  const monitor = await openSettings(page);
  await page.getByRole('tab', { name: 'Appearance' }).click();

  await expect(page.getByText('Theme: Light')).toBeVisible();
  await expect(page.locator('html')).not.toHaveClass(/dark/);

  await page.getByRole('button', { name: 'Switch to dark mode' }).click();
  await expect(page.getByText('Theme: Dark')).toBeVisible();
  await expect(page.locator('html')).toHaveClass(/dark/);

  // The dark theme applies the MaterialM dark token set. The background
  // repaints one frame after html.dark swaps the tokens (reduced-motion frame
  // lag), so poll for the settled value instead of reading mid-transition.
  await expect
    .poll(
      () => page.evaluate(() => getComputedStyle(document.body).backgroundColor),
      { timeout: 2000, message: 'dark theme applies dark background' },
    )
    .toBe('rgb(17, 24, 39)'); // --mm-background dark

  // Persists across reload.
  await page.reload();
  await expect(page.locator('html')).toHaveClass(/dark/);

  await page.getByRole('tab', { name: 'Appearance' }).click();
  await expect(page.getByText('Theme: Dark')).toBeVisible();

  await page.getByRole('button', { name: 'Switch to light mode' }).click();
  await expect(page.locator('html')).not.toHaveClass(/dark/);
  await expect(page.getByText('Theme: Light')).toBeVisible();

  expectNoNetworkIssues(monitor);
});

test('renders Settings on mobile without overflow', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const monitor = await openSettings(page);

  // Store fields stack into a single column on small screens.
  await expect(page.getByLabel('Company name')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Save' })).toBeVisible();

  const overflow = await page.evaluate(() =>
    document.documentElement.scrollWidth > document.documentElement.clientWidth,
  );
  expect(overflow).toBe(false);

  expectNoNetworkIssues(monitor);
  await expect(page.locator('.app-page')).toHaveScreenshot('settings-mobile.png', SNAPSHOT);
});

test('navigates tabs with the arrow keys', async ({ page }) => {
  const monitor = await openSettings(page);

  // Focus the active tab to anchor keyboard navigation.
  await page.getByRole('tab', { name: 'Store' }).focus();
  await expect(page.getByRole('tab', { name: 'Store' })).toBeFocused();
  await page.keyboard.press('ArrowRight');
  await expect(page.getByRole('tab', { name: 'Backup' })).toBeFocused();
  await expect(page.getByRole('tab', { name: 'Backup' })).toHaveAttribute('aria-selected', 'true');
  await expect(page.getByRole('heading', { name: 'Backup' })).toBeVisible();

  await page.keyboard.press('ArrowRight');
  await expect(page.getByRole('tab', { name: 'Appearance' })).toHaveAttribute('aria-selected', 'true');

  await page.keyboard.press('End');
  await expect(page.getByRole('tab', { name: 'Appearance' })).toHaveAttribute('aria-selected', 'true');

  await page.keyboard.press('ArrowLeft');
  await expect(page.getByRole('tab', { name: 'Backup' })).toHaveAttribute('aria-selected', 'true');

  expectNoNetworkIssues(monitor);
});

test('persists an unsaved backup path before triggering a backup', async ({ page }) => {
  const monitor = await openSettings(page);
  await page.getByRole('tab', { name: 'Backup' }).click();

  await page.getByLabel('Backup path').fill('/usb/new-target');
  await page.getByRole('button', { name: 'Back up now' }).click();

  await expect(page.getByRole('status')).toHaveText(/Backup completed/);

  const businessPatches = monitor.requests.filter(
    (r) => r.method === 'PATCH' && r.path === '/api/business/',
  );
  const triggers = monitor.requests.filter(
    (r) => r.method === 'POST' && r.path === '/api/backups/trigger/',
  );

  // The unsaved path is persisted (PATCH) before the manual trigger, so the
  // session-close backup writes to the same destination the user saw.
  expect(businessPatches).toHaveLength(1);
  expect(JSON.parse(businessPatches[0].body ?? '{}')).toEqual({
    backup_path: '/usb/new-target',
  });
  expect(triggers).toHaveLength(1);
  const patchIndex = monitor.requests.indexOf(businessPatches[0]);
  const triggerIndex = monitor.requests.indexOf(triggers[0]);
  expect(patchIndex).toBeGreaterThanOrEqual(0);
  expect(triggerIndex).toBeGreaterThan(patchIndex);

  expectNoNetworkIssues(monitor);
});

test('clearing the backup path disables "Back up now" and never PATCHes an empty path', async ({ page }) => {
  const monitor = await openSettings(page);
  await page.getByRole('tab', { name: 'Backup' }).click();

  const backupButton = page.getByRole('button', { name: 'Back up now' });
  await expect(backupButton).toBeEnabled();

  await page.getByLabel('Backup path').fill('');
  await expect(backupButton).toBeDisabled();

  // No PATCH and no trigger fired: the configured destination is never erased.
  expect(
    monitor.requests.filter((r) => r.method === 'PATCH' && r.path === '/api/business/'),
  ).toEqual([]);
  expect(
    monitor.requests.filter((r) => r.method === 'POST' && r.path === '/api/backups/trigger/'),
  ).toEqual([]);
  expectNoNetworkIssues(monitor);
});

test('rejects an invalid email address before it reaches the server', async ({ page }) => {
  const monitor = await openSettings(page);

  await page.getByLabel('Email').fill('not-an-email');
  await page.getByRole('button', { name: 'Save' }).click();

  await expect(page.getByRole('alert')).toHaveText('Enter a valid email address.');
  expect(
    monitor.requests.filter((r) => r.method === 'PATCH' && r.path === '/api/business/'),
  ).toEqual([]);
  expectNoNetworkIssues(monitor);
});

test('rejects an invalid website URL before it reaches the server', async ({ page }) => {
  const monitor = await openSettings(page);

  await page.getByLabel('Website').fill('not-a-url');
  await page.getByRole('button', { name: 'Save' }).click();

  await expect(page.getByRole('alert')).toHaveText(
    'Enter a valid website URL starting with http:// or https://.',
  );
  expect(
    monitor.requests.filter((r) => r.method === 'PATCH' && r.path === '/api/business/'),
  ).toEqual([]);
  expectNoNetworkIssues(monitor);
});

// User management (list, create/edit, delete) is covered on the canonical
// /users page in users.spec.ts; the Settings Users tab was removed.
