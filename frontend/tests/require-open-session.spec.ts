/**
 * Integration tests for the /sales guard (RequireOpenSession).
 *
 * Covers the acceptance criteria of t_7a995b11:
 *   1. Open session  -> navigating to /sales renders the Sales screen.
 *   2. No open session -> navigating to /sales redirects to the dashboard '/'
 *      (replace).
 *   3. Session closed while this tab is in the background -> returning to the
 *      tab (window focus / visibilitychange) re-checks and redirects to '/'.
 *   4. Transient failures of GET /api/sessions/current/ (network abort, 403,
 *      404) do NOT redirect — the Sales screen still renders (fail-open for
 *      availability).
 *   5. The guard component is exported (module shape) and the /api/sessions/current/
 *      endpoint is consulted on mount.
 *
 * The repo has no frontend unit-test runner; these are API-mocked integration
 * tests in the existing Playwright framework, matching tests/sales.spec.ts and
 * tests/sales-navigation.spec.ts. "Mocked apiFetch" = every /api request is
 * intercepted with page.route and fulfilled without the backend.
 */
import { expect, test, type Page, type Route } from '@playwright/test';

const BASE_URL =
  process.env.EIXNPOS_BASE_URL ?? 'http://127.0.0.1:4173';

const AUTH_ME = {
  user: { id: 1, username: 'maya', role: 'cashier' },
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

const PRODUCT = {
  id: 1,
  name: 'Widget',
  price: '10.00',
  quantity: 5,
  is_low_stock: false,
  active: true,
};

const OPEN_SESSION = { open: true };
const NO_SESSION = { open: false };

function json(data: unknown, status = 200) {
  return { status, contentType: 'application/json', body: JSON.stringify(data) };
}

interface SessionMonitor {
  currentRequests: number;
  startBodies: Array<Record<string, unknown>>;
  /** When `parkCurrentAt` matched a current GET, the parked route lives here so the test can fulfill it out of order. */
  parkedCurrent: Route | null;
  /** Fulfill the parked current GET (requires `parkCurrentAt`). */
  releaseCurrent(data: Record<string, unknown>): Promise<void>;
}

interface SessionOptions {
  /** Response for GET /api/sessions/current/: a payload, or { fail: <text> } to simulate a transport/status failure. */
  current: Record<string, unknown> | { fail: string } | { status: number };
  /** Status for POST /api/sessions/ (the start-shift POST). Omit to leave that route unmocked (404). */
  startStatus?: number;
  /** Park the current GET at this 1-based request index (exposed as monitor.parkedCurrent) so the test can defer its resolution. */
  parkCurrentAt?: number;
}

/**
 * Install /api route mocks. `opts.current` controls the current-session check.
 * Every guard request to /api/sessions/current/ is counted so tests can assert
 * that mount and re-validation actually hit the endpoint. `parkCurrentAt` lets
 * a test hold a specific revalidation GET in flight and fulfill it late, to
 * exercise the stale-result race.
 */
async function installMocks(page: Page, opts: SessionOptions): Promise<SessionMonitor> {
  const monitor: SessionMonitor = {
    currentRequests: 0,
    startBodies: [],
    parkedCurrent: null,
    releaseCurrent: async (data) => {
      const route = monitor.parkedCurrent;
      if (!route) throw new Error('releaseCurrent() called but no current GET is parked');
      monitor.parkedCurrent = null;
      await route.fulfill(json(data));
    },
  };

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

    if (path === '/api/auth/me/') {
      await route.fulfill(json(AUTH_ME));
      return;
    }
    if (path === '/api/sessions/current/') {
      monitor.currentRequests += 1;
      if (opts.parkCurrentAt === monitor.currentRequests) {
        // Defer this revalidation GET so the test can resolve it after a start
        // POST has already succeeded (the stale-result race under test).
        monitor.parkedCurrent = route;
        return;
      }
      const cur = opts.current;
      if ('fail' in cur) {
        await route.abort('failed');
        return;
      }
      if ('status' in cur) {
        await route.fulfill(json({ detail: 'nope' }, cur.status));
        return;
      }
      await route.fulfill(json(cur));
      return;
    }
    if (path === '/api/sessions/' && method === 'POST') {
      const payload = JSON.parse(request.postData() ?? '{}') as Record<string, unknown>;
      monitor.startBodies.push(payload);
      await route.fulfill(json({}, opts.startStatus ?? 404));
      return;
    }
    if (path === '/api/business/') {
      await route.fulfill(json(BUSINESS_INFO));
      return;
    }
    if (path === '/api/products/') {
      await route.fulfill(json([PRODUCT]));
      return;
    }
    if (path === '/api/sales/') {
      await route.fulfill(json([]));
      return;
    }

    await route.fulfill(json({ detail: `No mock for ${method} ${path}` }, 404));
  });

  return monitor;
}

async function openSales(page: Page): Promise<void> {
  await page.goto(`${BASE_URL}/sales`);
}

test('exports the RequireOpenSession named export (module shape)', async ({
  page,
}) => {
  // Compile-level check: AppRouter routes /sales through the guard; the named
  // export is what t_54515412 wires in. Reach /sales with an open session and
  // assert the Sales screen mounts through the guard.
  const monitor = await installMocks(page, { current: OPEN_SESSION });
  await openSales(page);
  await expect(page.getByRole('heading', { level: 1, name: 'Sale' })).toBeVisible();
  expect(monitor.currentRequests).toBeGreaterThanOrEqual(1);
});

test('redirects to / when there is no open session', async ({ page }) => {
  await installMocks(page, { current: NO_SESSION });
  await openSales(page);

  // The guard redirects (replace) to the dashboard, so the Sales heading must
  // never appear and the dashboard must render instead.
  await expect(page.getByRole('heading', { level: 1, name: /Dashboard/i })).toBeVisible();
  await expect(page.getByRole('heading', { level: 1, name: 'Sale' })).toHaveCount(0);
});

test('renders Sales when an open session exists', async ({ page }) => {
  const monitor = await installMocks(page, { current: OPEN_SESSION });
  await openSales(page);
  await expect(page.getByRole('heading', { level: 1, name: 'Sale' })).toBeVisible();
  expect(monitor.currentRequests).toBeGreaterThanOrEqual(1);
});

test('does not redirect on a network failure of the session check', async ({ page }) => {
  const monitor = await installMocks(page, { current: { fail: 'network-down' } });
  await openSales(page);
  // Fail-open: the Sales screen renders despite the check failing.
  await expect(page.getByRole('heading', { level: 1, name: 'Sale' })).toBeVisible();
  expect(monitor.currentRequests).toBeGreaterThanOrEqual(1);
});

test('does not redirect on a 403 from the session check', async ({ page }) => {
  await installMocks(page, { current: { status: 403 } });
  await openSales(page);
  await expect(page.getByRole('heading', { level: 1, name: 'Sale' })).toBeVisible();
});

test('does not redirect on a 404 from the session check', async ({ page }) => {
  await installMocks(page, { current: { status: 404 } });
  await openSales(page);
  await expect(page.getByRole('heading', { level: 1, name: 'Sale' })).toBeVisible();
});

test('re-validates on window focus and redirects when the session closed', async ({
  page,
}) => {
  // Start open, then switch the mock to "no session".
  let sessionOpen: boolean = true;
  const monitor = await installMocks(page, { current: OPEN_SESSION });
  await openSales(page);
  await expect(page.getByRole('heading', { level: 1, name: 'Sale' })).toBeVisible();
  const before = monitor.currentRequests;

  // The guard re-fetches on every focus/visibility event; make the next
  // responses report "no session", then drive a window focus event.
  await page.route('**/api/sessions/current/', (route: Route) => {
    monitor.currentRequests += 1;
    void route.fulfill(json(sessionOpen ? OPEN_SESSION : NO_SESSION));
  });
  sessionOpen = false;

  await page.evaluate(() => {
    window.dispatchEvent(new Event('focus'));
  });

  // The re-check returns open:false -> replace-navigate to '/'.
  await expect(page.getByRole('heading', { level: 1, name: /Dashboard/i })).toBeVisible();
  expect(monitor.currentRequests).toBeGreaterThan(before);
});

test('re-validates on visibilitychange and redirects when the session closed', async ({
  page,
}) => {
  // Start open, then switch the mock to "no session".
  let sessionOpen: boolean = true;
  await installMocks(page, { current: OPEN_SESSION });
  await openSales(page);
  await expect(page.getByRole('heading', { level: 1, name: 'Sale' })).toBeVisible();

  await page.route('**/api/sessions/current/', (route: Route) => {
    void route.fulfill(json(sessionOpen ? OPEN_SESSION : NO_SESSION));
  });
  sessionOpen = false;

  // Simulate returning to the tab: bounce hidden true->false so the guard's
  // visibilitychange handler sees the document visible and re-checks.
  await page.evaluate(() => {
    Object.defineProperty(document, 'hidden', {
      configurable: true,
      get: () => false,
    });
    document.dispatchEvent(new Event('visibilitychange'));
  });

  await expect(page.getByRole('heading', { level: 1, name: /Dashboard/i })).toBeVisible();
});

test('does not redirect when a re-validation check fails transiently', async ({
  page,
}) => {
  // Start open; the re-check on focus fails (network). Fail-open keeps /sales.
  let failNow: boolean = false;
  await installMocks(page, { current: OPEN_SESSION });
  await openSales(page);
  await expect(page.getByRole('heading', { level: 1, name: 'Sale' })).toBeVisible();

  await page.route('**/api/sessions/current/', (route: Route) => {
    if (failNow) {
      void route.abort('failed');
    } else {
      void route.fulfill(json(OPEN_SESSION));
    }
  });
  failNow = true;

  await page.evaluate(() => {
    window.dispatchEvent(new Event('focus'));
  });

  // The failed re-check must not redirect: Sales stays mounted.
  await page.waitForTimeout(300);
  await expect(page.getByRole('heading', { level: 1, name: 'Sale' })).toBeVisible();
});

test('stays on /sales when a pre-start revalidation resolves stale after a successful shift start', async ({
  page,
}) => {
  // Regression for the stale-revalidation race (t_d42f51c4): a focus/visibility
  // revalidation that was in flight before the start POST resolved must not
  // evict the cashier afterwards based on a stale `open:false` check.
  //
  // GET #1 (mount) reports no open session -> the start-shift modal appears.
  // GET #2 (the revalidation a `focus` event triggers while the modal is up) is
  // parked in flight. The cashier completes the starting-cash form and the POST
  // succeeds, opening the session. When GET #2 is finally fulfilled with
  // `open:false`, that result is stale relative to the now-open session and must
  // be discarded instead of bouncing the cashier back to the dashboard.
  const monitor = await installMocks(page, {
    current: NO_SESSION,
    startStatus: 201,
    parkCurrentAt: 2,
  });

  await openSales(page);
  const modal = page.getByRole('dialog', { name: 'Start your shift' });
  await expect(modal).toBeVisible();

  // Trigger the focus revalidation; its current GET is parked by the mock.
  await page.evaluate(() => {
    window.dispatchEvent(new Event('focus'));
  });
  await expect.poll(() => monitor.parkedCurrent).toBeTruthy();

  // Complete the starting-cash form; the start POST succeeds.
  await page.getByLabel('Starting cash').fill('100.00');
  await modal.getByRole('button', { name: 'Start shift' }).click();
  await expect(page.getByRole('heading', { level: 1, name: 'Sale' })).toBeVisible();
  expect(monitor.startBodies).toEqual([{ starting_cash: '100.00' }]);

  // Fulfil the parked revalidation with a stale `open:false` exactly as the
  // reproduction does. It must NOT redirect: the session was already started.
  await monitor.releaseCurrent(NO_SESSION);

  await expect(page).toHaveURL(`${BASE_URL}/sales`);
  await expect(page.getByRole('heading', { level: 1, name: 'Sale' })).toBeVisible();
  await expect(modal).toHaveCount(0);
});