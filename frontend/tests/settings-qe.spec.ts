import { expect, test, type Page, type Route } from '@playwright/test';

// QE gap coverage for the Settings page (task t_e671a51d). These cases close
// holes the existing suites leave open: the beforeunload dirty guard, server
// failure paths, persistence round-trip, backup validation, and user-table
// boundary states.

const BASE_URL = 'http://127.0.0.1:4173';

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
  { id: 1, email: 'admin@eixn.test', name: 'Alex Admin', username: 'maya', first_name: 'Alex', last_name: 'Admin', role: 'admin', active: true, createdAt: '2026-01-01T10:00:00Z', updatedAt: '2026-01-01T10:00:00Z' },
  { id: 2, email: 'sam@eixn.test', name: 'Sam Manager', username: 'sam', first_name: 'Sam', last_name: 'Manager', role: 'manager', active: true, createdAt: '2026-01-01T10:00:00Z', updatedAt: '2026-01-01T10:00:00Z' },
];

interface NetworkMonitor {
  requests: Array<{ method: string; path: string; body?: string }>;
}

function json(data: unknown, status = 200) {
  return { status, contentType: 'application/json', body: JSON.stringify(data) };
}

interface MockOverrides {
  businessPatchStatus?: number;
  businessPatchBody?: Record<string, unknown>;
  triggerStatus?: number;
  usersStatus?: number;
  usersBody?: unknown[];
  userPatchStatus?: number;
  userPatchBody?: Record<string, unknown>;
}

async function installApiMocks(
  page: Page,
  overrides: MockOverrides = {},
): Promise<NetworkMonitor> {
  const monitor: NetworkMonitor = { requests: [] };
  // Stateful mirror so a save followed by a reload round-trips through the
  // mock the way a real backend would.
  let stored: Record<string, unknown> = { ...BUSINESS_INFO };

  await page.route('**/*', async (route: Route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.origin !== BASE_URL) {
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
        if (overrides.businessPatchStatus) {
          await route.fulfill(
            json(overrides.businessPatchBody ?? { detail: 'Server exploded' }, overrides.businessPatchStatus),
          );
          return;
        }
        stored = { ...stored, ...JSON.parse(request.postData() || '{}') };
        await route.fulfill(json(stored));
        return;
      }
      await route.fulfill(json(stored));
      return;
    }
    if (path === '/api/users/') {
      if (overrides.usersStatus) {
        await route.fulfill(json({ detail: 'Users unavailable' }, overrides.usersStatus));
        return;
      }
      if (method === 'POST') {
        await route.fulfill(json({ ...USERS[0], ...JSON.parse(request.postData() || '{}') }, 201));
        return;
      }
      await route.fulfill(json(overrides.usersBody ?? USERS));
      return;
    }
    const userMatch = /^\/api\/users\/(\d+)\/$/.exec(path);
    if (userMatch) {
      if (method === 'PATCH') {
        if (overrides.userPatchStatus) {
          await route.fulfill(
            json(overrides.userPatchBody ?? { role: ['Invalid role.'] }, overrides.userPatchStatus),
          );
          return;
        }
        await route.fulfill(json({ ...USERS[0], ...JSON.parse(request.postData() || '{}') }));
        return;
      }
      if (method === 'DELETE') {
        await route.fulfill({ status: 204 });
        return;
      }
    }
    if (path === '/api/backups/trigger/') {
      if (overrides.triggerStatus) {
        await route.fulfill(json({ detail: 'Backup failed: device not mounted' }, overrides.triggerStatus));
        return;
      }
      await route.fulfill(json({ path: '/backup/eixnpos-20260101.sqlite3' }));
      return;
    }
    await route.fulfill(json({ detail: `No mock for ${method} ${path}` }, 404));
  });

  return monitor;
}

async function openSettings(
  page: Page,
  overrides: MockOverrides = {},
): Promise<NetworkMonitor> {
  const monitor = await installApiMocks(page, overrides);
  await page.goto(`${BASE_URL}/settings`);
  await expect(page.getByRole('heading', { level: 1, name: 'Settings' })).toBeVisible();
  return monitor;
}

/** Ends the test by closing the page through the beforeunload phase and
 *  reports whether the browser raised the unload confirmation dialog. The
 *  page is gone after this — call it exactly once, as the final action. */
async function closeWithBeforeUnload(page: Page): Promise<boolean> {
  // page.close() resolves before the dialog event is delivered, so wait on
  // the event promise rather than reading a flag right after close().
  const dialogPromise = page
    .waitForEvent('dialog', { timeout: 5_000 })
    .catch(() => null);
  try {
    await page.close({ runBeforeUnload: true });
  } catch {
    // A hanging unload dialog can make close() itself reject; the event
    // promise below still tells us whether the dialog was raised.
  }
  const dialog = await dialogPromise;
  return dialog?.type() === 'beforeunload';
}

async function expectNoBeforeUnload(page: Page) {
  const sawDialog = await closeWithBeforeUnload(page);
  expect(sawDialog, 'no unsaved-changes dialog should be raised').toBe(false);
}

async function expectBeforeUnload(page: Page) {
  const sawDialog = await closeWithBeforeUnload(page);
  expect(sawDialog, 'unsaved-changes dialog should be raised').toBe(true);
}

// --- beforeunload dirty guard -------------------------------------------
// Each test below closes its page exactly once, at the very end.

test('a freshly loaded page with no edits raises no unload dialog', async ({ page }) => {
  await openSettings(page);
  await expectNoBeforeUnload(page);
});

test('warns on close when the store section has unsaved edits', async ({ page }) => {
  await openSettings(page);

  await page.getByLabel('Company name').fill('Dirty Value');
  await expectBeforeUnload(page);
});

test('disarms the guard after cancelling unsaved store edits', async ({ page }) => {
  await openSettings(page);

  await page.getByLabel('Company name').fill('Dirty Value');
  await page.getByRole('button', { name: 'Cancel' }).click();
  await expect(page.getByLabel('Company name')).toHaveValue('eixn Corner Store');

  await expectNoBeforeUnload(page);
});

test('disarms the guard after saving the store section', async ({ page }) => {
  const monitor = await openSettings(page);

  await page.getByLabel('Company name').fill('Saved Value');
  await page.getByRole('button', { name: 'Save' }).click();
  await expect(page.getByRole('status')).toHaveText('Store details saved.');

  // The save went through, so the store section matches the server state.
  expect(
    monitor.requests.filter((r) => r.method === 'PATCH' && r.path === '/api/business/'),
  ).toHaveLength(1);
  await expectNoBeforeUnload(page);
});

test('an edit reverted back to the persisted value leaves the guard disarmed', async ({ page }) => {
  await openSettings(page);

  await page.getByLabel('Company name').fill('eixn Corner Store'); // same value, no net change
  await page.getByLabel('Phone').fill('555-9999');
  await page.getByLabel('Phone').fill('555-0100'); // reverted
  await expectNoBeforeUnload(page);
});

test('warns on close when the backup path has unsaved edits', async ({ page }) => {
  await openSettings(page);
  await page.getByRole('tab', { name: 'Backup' }).click();

  await page.getByLabel('Backup path').fill('/usb/unsaved');
  await expectBeforeUnload(page);
});

test('disarms the guard after saving the backup path', async ({ page }) => {
  await openSettings(page);
  await page.getByRole('tab', { name: 'Backup' }).click();

  await page.getByLabel('Backup path').fill('/usb/saved-target');
  await page.getByRole('button', { name: 'Save path' }).click();
  await expect(page.getByRole('status')).toHaveText('Backup path saved.');
  await expectNoBeforeUnload(page);
});

// --- store save: server failure and persistence -------------------------

test('a failed store save shows the server error and no false success', async ({ page }) => {
  const monitor = await openSettings(page, { businessPatchStatus: 500 });

  await page.getByLabel('Company name').fill('New Corner Co');
  await page.getByRole('button', { name: 'Save' }).click();

  await expect(page.getByRole('alert')).toContainText('Server exploded');
  await expect(page.getByRole('status')).toHaveCount(0);
  // The field keeps the user's input so the edit is not lost.
  await expect(page.getByLabel('Company name')).toHaveValue('New Corner Co');
  expect(
    monitor.requests.filter((r) => r.method === 'PATCH' && r.path === '/api/business/'),
  ).toHaveLength(1);
});

test('saved store details survive a reload (round-trip through the API)', async ({ page }) => {
  const monitor = await openSettings(page);

  await page.getByLabel('Company name').fill('Persisted Co');
  await page.getByRole('button', { name: 'Save' }).click();
  await expect(page.getByRole('status')).toHaveText('Store details saved.');

  const patch = monitor.requests.find((r) => r.method === 'PATCH' && r.path === '/api/business/');
  expect(patch).toBeTruthy();
  expect(JSON.parse(patch!.body ?? '{}')).toMatchObject({
    company_name: 'Persisted Co',
    phone: '555-0100',
    email: 'hello@eixn.test',
    address: '12 Market Street',
    website: 'https://eixn.example',
  });

  // Reload: the mock backend now serves the saved state, and the page must
  // render it (not the initial fixture).
  await page.reload();
  await expect(page.getByRole('heading', { level: 1, name: 'Settings' })).toBeVisible();
  await expect(page.getByLabel('Company name')).toHaveValue('Persisted Co');
});

// --- backup validation and trigger failure ------------------------------

test('an empty backup path is rejected with an inline error and no request', async ({ page }) => {
  const monitor = await openSettings(page);
  await page.getByRole('tab', { name: 'Backup' }).click();

  await page.getByLabel('Backup path').fill('');
  await page.getByRole('button', { name: 'Save path' }).click();

  await expect(page.getByRole('alert')).toHaveText('Backup path is required.');
  expect(
    monitor.requests.filter((r) => r.method === 'PATCH' && r.path === '/api/business/'),
  ).toEqual([]);
});

test('a whitespace-only backup path is rejected', async ({ page }) => {
  const monitor = await openSettings(page);
  await page.getByRole('tab', { name: 'Backup' }).click();

  await page.getByLabel('Backup path').fill('   ');
  await page.getByRole('button', { name: 'Save path' }).click();

  await expect(page.getByRole('alert')).toHaveText('Backup path is required.');
  expect(
    monitor.requests.filter((r) => r.method === 'PATCH' && r.path === '/api/business/'),
  ).toEqual([]);
});

test('a failed backup trigger shows the error without writing the path', async ({ page }) => {
  const monitor = await openSettings(page, { triggerStatus: 500 });
  await page.getByRole('tab', { name: 'Backup' }).click();

  await page.getByRole('button', { name: 'Back up now' }).click();

  await expect(page.getByRole('alert')).toContainText('Backup failed');
  await expect(page.getByRole('status')).toHaveCount(0);
  expect(
    monitor.requests.filter((r) => r.method === 'POST' && r.path === '/api/backups/trigger/'),
  ).toHaveLength(1);
  // The path was unedited here, so a trigger must not silently PATCH it.
  expect(
    monitor.requests.filter((r) => r.method === 'PATCH' && r.path === '/api/business/'),
  ).toEqual([]);
});

// --- users section: loading, empty, errors, self-delete -----------------

test('an empty users list shows the empty state', async ({ page }) => {
  await openSettings(page, { usersBody: [] });
  await page.getByRole('tab', { name: 'Users' }).click();

  await expect(page.getByText('No users yet. Create the first one.')).toBeVisible();
  await expect(page.getByRole('button', { name: '+ Add user' })).toBeVisible();
});

test('a failed users load shows an inline error and no table', async ({ page }) => {
  await openSettings(page, { usersStatus: 500 });
  await page.getByRole('tab', { name: 'Users' }).click();

  await expect(page.getByRole('alert')).toContainText('Users unavailable');
  await expect(page.getByRole('table')).toHaveCount(0);
});

test('the current user cannot be deleted from the table', async ({ page }) => {
  const monitor = await openSettings(page);
  await page.getByRole('tab', { name: 'Users' }).click();

  const ownRow = page.getByRole('row', { name: /Alex Admin/ });
  // The button carries aria-label "Delete Alex Admin"; its visible label is "You".
  const deleteButton = ownRow.getByRole('button', { name: 'Delete Alex Admin' });
  await expect(deleteButton).toBeDisabled();
  await expect(deleteButton).toHaveText('You');

  await deleteButton.click({ force: true });
  // Guard holds: no DELETE request, no confirm dialog, row still present.
  expect(
    monitor.requests.filter((r) => r.method === 'DELETE' && /^\/api\/users\/\d+\/$/.test(r.path)),
  ).toEqual([]);
  await expect(page.getByRole('cell', { name: 'Alex Admin', exact: true })).toBeVisible();
});

test('a failed user edit keeps the modal open with the server error', async ({ page }) => {
  await openSettings(page, { userPatchStatus: 400, userPatchBody: { role: ['Invalid role.'] } });
  await page.getByRole('tab', { name: 'Users' }).click();

  await page.getByRole('button', { name: 'Edit Sam Manager' }).click();
  const modal = page.getByRole('dialog');
  await expect(modal).toBeVisible();
  await modal.getByRole('button', { name: 'Save changes' }).click();

  await expect(modal).toBeVisible(); // stays open for correction
  // App-wide behavior: ApiError surfaces the HTTP status for validation
  // payloads without a `detail` key (see frontend/src/api.ts).
  await expect(modal.getByRole('alert')).toContainText('400');
});
