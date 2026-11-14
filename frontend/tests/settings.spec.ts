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

const USERS = [
  { id: 1, email: 'admin@eixn.test', name: 'Alex Admin', username: 'alex', first_name: 'Alex', last_name: 'Admin', role: 'admin', active: true, createdAt: '2026-01-01T10:00:00Z', updatedAt: '2026-01-01T10:00:00Z' },
  { id: 2, email: 'sam@eixn.test', name: 'Sam Manager', username: 'sam', first_name: 'Sam', last_name: 'Manager', role: 'manager', active: true, createdAt: '2026-01-01T10:00:00Z', updatedAt: '2026-01-01T10:00:00Z' },
  { id: 3, email: 'riley@eixn.test', name: 'Riley Cashier', username: 'riley', first_name: 'Riley', last_name: 'Cashier', role: 'cashier', active: false, createdAt: '2026-01-01T10:00:00Z', updatedAt: '2026-01-01T10:00:00Z' },
];

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
    if (path === '/api/users/') {
      if (method === 'POST') {
        await route.fulfill(json({ ...USERS[0], ...JSON.parse(request.postData() || '{}') }, 201));
        return;
      }
      await route.fulfill(json(USERS));
      return;
    }
    const userMatch = /^\/api\/users\/(\d+)\/$/.exec(path);
    if (userMatch) {
      if (method === 'PATCH') {
        await route.fulfill(json({ ...USERS[0], ...JSON.parse(request.postData() || '{}') }));
        return;
      }
      if (method === 'DELETE') {
        await route.fulfill({ status: 204 });
        return;
      }
      await route.fulfill(json({ detail: `No mock for ${method} ${path}` }, 404));
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
  await expect(tabs).toHaveCount(4);
  await expect(tabs.nth(0)).toHaveText('Store');
  await expect(tabs.nth(1)).toHaveText('Users');
  await expect(tabs.nth(2)).toHaveText('Backup');
  await expect(tabs.nth(3)).toHaveText('Appearance');

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

test('shows inactive users with a muted badge', async ({ page }) => {
  const monitor = await openSettings(page);
  await page.getByRole('tab', { name: 'Users' }).click();

  await expect(page.getByRole('tab', { name: 'Users' })).toHaveAttribute('aria-selected', 'true');
  await expect(page.getByRole('cell', { name: 'Alex Admin', exact: true })).toBeVisible();
  await expect(page.getByRole('cell', { name: 'Riley Cashier', exact: true })).toBeVisible();
  await expect(page.locator('.mm-badge--success', { hasText: 'Active' })).toHaveCount(2);
  await expect(page.locator('.mm-badge--muted', { hasText: 'Inactive' })).toHaveCount(1);
  await expect(page.getByRole('button', { name: '+ Add user' })).toBeVisible();

  expectNoNetworkIssues(monitor);
  await expect(page.locator('.app-page')).toHaveScreenshot('settings-users.png', SNAPSHOT);
});

test('saves store details and shows inline success', async ({ page }) => {
  const monitor = await openSettings(page);

  await page.getByLabel('Company name').fill('New Corner Co');
  await page.getByRole('button', { name: 'Save' }).click();

  await expect(page.getByRole('status')).toHaveText('Store details saved.');
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

test('opens the add user modal and creates a user', async ({ page }) => {
  const monitor = await openSettings(page);
  await page.getByRole('tab', { name: 'Users' }).click();
  await page.getByRole('button', { name: '+ Add user' }).click();

  await expect(page.locator('.mm-modal')).toBeVisible();
  await expect(page.getByRole('dialog')).toBeVisible();
  await expect(page.getByRole('heading', { name: 'New user' })).toBeVisible();

  const modal = page.getByRole('dialog');
  await modal.getByLabel('Username').fill('newworker');
  await modal.getByLabel('Email').fill('worker@eixn.test');
  await modal.getByLabel('First name').fill('New');
  await modal.getByLabel('Last name').fill('Worker');
  await modal.getByLabel('Password').fill('password123');
  await modal.getByRole('button', { name: 'Create user' }).click();

  await expect(page.locator('.mm-modal')).toBeHidden();
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
  await expect(page.getByRole('tab', { name: 'Users' })).toBeFocused();
  await expect(page.getByRole('tab', { name: 'Users' })).toHaveAttribute('aria-selected', 'true');
  await expect(page.getByRole('heading', { name: 'Users' })).toBeVisible();

  await page.keyboard.press('ArrowRight');
  await expect(page.getByRole('tab', { name: 'Backup' })).toHaveAttribute('aria-selected', 'true');

  await page.keyboard.press('End');
  await expect(page.getByRole('tab', { name: 'Appearance' })).toHaveAttribute('aria-selected', 'true');

  await page.keyboard.press('ArrowLeft');
  await expect(page.getByRole('tab', { name: 'Backup' })).toHaveAttribute('aria-selected', 'true');

  expectNoNetworkIssues(monitor);
});

test('closes the user modal with Escape and restores focus', async ({ page }) => {
  const monitor = await openSettings(page);
  await page.getByRole('tab', { name: 'Users' }).click();
  await page.getByRole('button', { name: '+ Add user' }).click();
  await expect(page.locator('.mm-modal')).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.locator('.mm-modal')).toBeHidden();
  await expect(page.getByRole('button', { name: '+ Add user' })).toBeFocused();
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

test('editing a name-less user does not rewrite name fields', async ({ page }) => {
  const monitor = await openSettings(page);
  const nameLess = {
    id: 95,
    email: 'nameless@eixn.test',
    name: 'sam',
    username: 'sam',
    first_name: '',
    last_name: '',
    role: 'cashier',
    active: true,
    createdAt: '2026-01-01T10:00:00Z',
    updatedAt: '2026-01-01T10:00:00Z',
  };
  // This test needs a user who has no first/last name (server returns only the
  // username-derived display name), so override just the users list.
  await page.route('**/api/users/**', async (route) => {
    const url = new URL(route.request().url());
    if (
      url.pathname === '/api/users/' &&
      route.request().method().toUpperCase() === 'GET'
    ) {
      await route.fulfill(json([nameLess]));
      return;
    }
    await route.fallback();
  });

  await page.getByRole('tab', { name: 'Users' }).click();
  await expect(page.getByRole('cell', { name: 'sam', exact: true })).toBeVisible();

  await page.getByRole('button', { name: /^Edit / }).first().click();
  const modal = page.getByRole('dialog');
  await expect(modal).toBeVisible();
  await expect(modal.getByLabel('Username')).toHaveValue('sam');
  await modal.getByLabel('Role').selectOption('manager');
  await modal.getByRole('button', { name: 'Save changes' }).click();

  await expect(page.locator('.mm-modal')).toBeHidden();

  const patch = monitor.requests.find(
    (r) => r.method === 'PATCH' && r.path === '/api/users/95/',
  );
  expect(patch).toBeTruthy();
  // Only the changed field ships; the empty first/last names are not rewritten.
  expect(JSON.parse(patch!.body ?? '{}')).toEqual({ role: 'manager' });
  expectNoNetworkIssues(monitor);
});
