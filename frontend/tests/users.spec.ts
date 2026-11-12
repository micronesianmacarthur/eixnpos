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

const USERS = [
  { id: 1, email: 'maya@example.com', name: 'Maya Admin', role: 'admin', createdAt: '2026-01-01T10:00:00Z', updatedAt: '2026-01-01T10:00:00Z' },
  { id: 2, email: 'cashier@example.com', name: 'Casey Cashier', role: 'cashier', createdAt: '2026-01-01T10:00:00Z', updatedAt: '2026-01-01T10:00:00Z' },
];

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
    if (path === '/api/users/') {
      await route.fulfill(json(USERS));
      return;
    }
    if (path.startsWith('/api/users/') && method === 'POST') {
      await route.fulfill(json(USERS[1]));
      return;
    }
    if (path.startsWith('/api/users/') && method === 'DELETE') {
      await route.fulfill(json({}));
      return;
    }

    await route.fulfill(json({ detail: `No mock for ${method} ${path}` }, 404));
  });

  return monitor;
}

async function openUsers(page: Page): Promise<NetworkMonitor> {
  const monitor = await installApiMocks(page);
  await page.goto(`${BASE_URL}/users`);
  await expect(page.locator('.app-shell')).toBeVisible();
  await expect(page.getByRole('heading', { level: 1, name: 'Users' })).toBeVisible();
  await page.evaluate(async () => {
    await document.fonts.ready;
  });
  return monitor;
}

function expectNoNetworkIssues(monitor: NetworkMonitor) {
  expect(monitor.external, `external requests: ${monitor.external.join(', ')}`).toEqual([]);
  expect(monitor.failed, `failed requests: ${monitor.failed.join(', ')}`).toEqual([]);
}

test('renders Users on desktop', async ({ page }) => {
  const monitor = await openUsers(page);
  expectNoNetworkIssues(monitor);
  await expect(page).toHaveScreenshot('users-desktop.png', SNAPSHOT);
});

test('renders Users on mobile', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const monitor = await openUsers(page);
  expectNoNetworkIssues(monitor);
  await expect(page).toHaveScreenshot('users-mobile.png', SNAPSHOT);
});

test('opens the create user modal', async ({ page }) => {
  const monitor = await openUsers(page);
  await page.getByRole('button', { name: /\+ New user/i }).click();
  await expect(page.locator('.mm-modal')).toBeVisible();
  expectNoNetworkIssues(monitor);
  await expect(page).toHaveScreenshot('users-create-modal.png', SNAPSHOT);
});
