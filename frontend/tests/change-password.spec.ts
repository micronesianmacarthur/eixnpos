import { expect, test, type Page, type Route } from '@playwright/test';

const BASE_URL = process.env.EIXNPOS_BASE_URL ?? 'http://127.0.0.1:4173';

function json(data: unknown, status = 200) {
  return {
    status,
    contentType: 'application/json',
    body: JSON.stringify(data),
  };
}

/**
 * Simulates the forced-password-change contract through a mutable DB in memory:
 *  - login with `username === 'admin'` returns `password_change_required: true`
 *    and flips a `forced` flag; any other user logs in normally.
 *  - `me/` echoes `must_change_password` while `forced && !changed`.
 *  - `change-password/` validates current + policy, and destroys the session on
 *    success (me starts returning 401).
 */
async function installMocks(page: Page) {
  let forced = false;
  let changed = false;
  const loginCalls: string[] = [];
  const changeCalls: string[] = [];

  await page.route('**/*', async (route: Route) => {
    const request = route.request();
    const url = new URL(request.url());

    if (url.protocol === 'data:' || url.protocol === 'blob:') {
      await route.continue();
      return;
    }
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

    const AUTH_ME = {
      user: { id: 1, username: 'admin', role: 'admin' },
      must_change_password: forced && !changed,
      business_info_complete: true,
    };

    if (path === '/api/auth/me/') {
      if (changed) {
        await route.fulfill(json({ detail: 'Not authenticated.' }, 401));
        return;
      }
      await route.fulfill(json(AUTH_ME));
      return;
    }

    if (path === '/api/auth/login/') {
      const body = JSON.parse(String(request.postData())) as {
        username?: string;
        password?: string;
      };
      loginCalls.push(body.username ?? '');
      if (body.username === 'admin' && body.password === 'admin') {
        forced = true;
        await route.fulfill(
          json({
            user: { id: 1, username: 'admin', role: 'admin' },
            password_change_required: true,
            business_info_complete: true,
          }),
        );
        return;
      }
      forced = false;
      await route.fulfill(
        json({
          user: { id: 2, username: 'maya', role: 'cashier' },
          password_change_required: false,
          business_info_complete: true,
        }),
      );
      return;
    }

    if (path === '/api/auth/change-password/') {
      const body = JSON.parse(String(request.postData())) as {
        current_password?: string;
        new_password?: string;
      };
      changeCalls.push(JSON.stringify(body));
      // Wrong current password -> 403, per contract.
      if (body.current_password !== 'admin') {
        await route.fulfill(
          json(
            {
              password_change_required: true,
              detail: 'Current password is incorrect.',
            },
            403,
          ),
        );
        return;
      }
      // Policy failure (reusing the seeded value) -> 400, per contract.
      if (body.new_password === 'admin') {
        await route.fulfill(
          json(
            {
              detail:
                "New password must not be the default or reserved value 'admin'.",
            },
            400,
          ),
        );
        return;
      }
      // Success: clear flags and invalidate the session.
      forced = false;
      changed = true;
      await route.fulfill(json({ detail: 'Password changed. Please log in again.' }));
      return;
    }

    await route.fulfill(json({ detail: `No mock for ${method} ${path}` }, 404));
  });

  return { loginCalls, changeCalls };
}

async function open(page: Page) {
  await page.goto(`${BASE_URL}/login`);
  await expect(page.locator('.auth-card')).toBeVisible();
}

test('admin login forces the change-password screen instead of the dashboard', async ({
  page,
}) => {
  await installMocks(page);
  await open(page);

  await page.getByLabel('Username:').fill('admin');
  await page.getByLabel('Password:').fill('admin');
  await page.getByRole('button', { name: 'Login' }).click();

  await expect(
    page.getByRole('heading', { level: 1, name: 'Change your password' }),
  ).toBeVisible();
  await expect(page.getByLabel('Current password:')).toBeVisible();
  await expect(page.getByLabel('New password:', { exact: true })).toBeVisible();
  await expect(page.getByLabel('Confirm new password:', { exact: true })).toBeVisible();
  await expect(
    page.getByRole('heading', { level: 1, name: 'Dashboard' }),
  ).toHaveCount(0);
});

test('normal login goes straight to the dashboard (no prompt)', async ({ page }) => {
  await installMocks(page);
  await open(page);

  await page.getByLabel('Username:').fill('maya');
  await page.getByLabel('Password:').fill('maya-secret');
  await page.getByRole('button', { name: 'Login' }).click();

  await expect(
    page.getByRole('heading', { level: 1, name: 'Dashboard' }),
  ).toBeVisible();
  await expect(page.locator('.auth-card')).toHaveCount(0);
});

test('navigating to the app while change is pending redirects to change-password', async ({
  page,
}) => {
  const { loginCalls } = await installMocks(page);

  // Log in as admin first so the forced flag is set in the mock, then leave the
  // forced screen to simulate a user trying to bypass it.
  await open(page);
  await page.getByLabel('Username:').fill('admin');
  await page.getByLabel('Password:').fill('admin');
  await page.getByRole('button', { name: 'Login' }).click();
  await expect(
    page.getByRole('heading', { level: 1, name: 'Change your password' }),
  ).toBeVisible();

  // Attempting to reach the dashboard keeps the user on the forced screen.
  await page.goto(`${BASE_URL}/`);
  await expect(
    page.getByRole('heading', { level: 1, name: 'Change your password' }),
  ).toBeVisible();
  expect(loginCalls).toContain('admin');
});

test('shows server and client validation errors on the change-password form', async ({
  page,
}) => {
  await installMocks(page);
  await open(page);

  await page.getByLabel('Username:').fill('admin');
  await page.getByLabel('Password:').fill('admin');
  await page.getByRole('button', { name: 'Login' }).click();
  await expect(
    page.getByRole('heading', { level: 1, name: 'Change your password' }),
  ).toBeVisible();

  // Server-side policy rejection (reusing 'admin').
  await page.getByLabel('Current password:').fill('admin');
  await page.getByLabel('New password:', { exact: true }).fill('admin');
  await page.getByLabel('Confirm new password:', { exact: true }).fill('admin');
  await page.getByRole('button', { name: 'Update password' }).click();
  await expect(page.getByRole('alert')).toContainText('must not be the default');

  // Wrong current password -> 403 error surfaced to the user.
  await page.getByLabel('Current password:').fill('wrong');
  await page.getByLabel('New password:', { exact: true }).fill('Str0ng!Passw0rd');
  await page.getByLabel('Confirm new password:', { exact: true }).fill('Str0ng!Passw0rd');
  await page.getByRole('button', { name: 'Update password' }).click();
  await expect(page.getByRole('alert')).toHaveText('Current password is incorrect.');

  // Client-side mismatch guards before any request is made.
  await page.getByLabel('Current password:').fill('admin');
  await page.getByLabel('New password:', { exact: true }).fill('Str0ng!Passw0rd');
  await page.getByLabel('Confirm new password:', { exact: true }).fill('Str0ng!Passw0rd-X');
  await page.getByRole('button', { name: 'Update password' }).click();
  await expect(page.getByRole('alert')).toHaveText(
    'New password and confirmation do not match.',
  );
});

test('successful change clears the forced flag and returns to login with a notice', async ({
  page,
}) => {
  const { changeCalls } = await installMocks(page);
  await open(page);

  await page.getByLabel('Username:').fill('admin');
  await page.getByLabel('Password:').fill('admin');
  await page.getByRole('button', { name: 'Login' }).click();
  await expect(
    page.getByRole('heading', { level: 1, name: 'Change your password' }),
  ).toBeVisible();

  await page.getByLabel('Current password:').fill('admin');
  await page.getByLabel('New password:', { exact: true }).fill('Str0ng!Passw0rd');
  await page.getByLabel('Confirm new password:', { exact: true }).fill('Str0ng!Passw0rd');
  await page.getByRole('button', { name: 'Update password' }).click();

  // Session invalidated, so the user is sent back to the login screen.
  await expect(page).toHaveURL(/\/login$/);
  await expect(
    page.getByText('Password updated. Please log in with your new password.'),
  ).toBeVisible();
  expect(changeCalls).toEqual([
    JSON.stringify({
      current_password: 'admin',
      new_password: 'Str0ng!Passw0rd',
    }),
  ]);
});
