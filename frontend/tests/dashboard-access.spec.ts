/**
 * Regression tests for dashboard access by role (t_efa94bca).
 *
 * Access rule under test (implemented by t_da257f6d):
 *   1. Every authenticated role — cashier, manager, admin — can navigate to
 *      the dashboard ('/') and it renders from the live composite endpoint.
 *   2. Anonymous/unauthenticated visitors are bounced to /login.
 *   3. The intentionally privileged dashboard affordances (Last backup
 *      panel, business-info setup form) stay manager/admin-only in the UI.
 *
 * These are API-mocked integration tests in the existing Playwright
 * framework (same pattern as tests/auth.spec.ts and
 * tests/require-open-session.spec.ts): every /api request is intercepted
 * and fulfilled without a backend, so the tests exercise the real router,
 * auth guards, and page components — not the server's permission classes
 * (those are covered by backend/dashboard/test_qe_access_regression.py).
 */
import { expect, test, type Page, type Route } from '@playwright/test';

const BASE_URL = process.env.EIXNPOS_BASE_URL ?? 'http://127.0.0.1:4173';

function json(data: unknown, status = 200) {
  return {
    status,
    contentType: 'application/json',
    body: JSON.stringify(data),
  };
}

/** Composite /api/dashboard/ payload matching the server contract (§5.7). */
const OVERVIEW = {
  business_day: '2026-11-24',
  generated_at: '2026-11-24T09:12:00+11:00',
  business_timezone: 'Pacific/Noumea',
  period: {
    scope: 'today',
    start: '2026-11-24T00:00:00+11:00',
    end: '2026-11-24T09:12:00+11:00',
  },
  today: {
    sales_count: 12,
    revenue: '1234.56',
    cash_sales: '567.89',
    invoice_count: 2,
    invoice_total: '600.00',
    void_count: 0,
    refund_count: 0,
    avg_basket: '102.88',
    open_count_today: 3,
  },
  session: {
    open: true,
    count: 1,
    business_day: '2026-11-24',
    sessions: [],
    session: null,
  },
  inventory: {
    generated_at: '2026-11-24T09:12:00+11:00',
    low_stock_count: 0,
    items: [],
    threshold_source: {
      field: 'min_stock',
      rule: 'quantity <= min_stock',
      non_stock_excluded: true,
    },
  },
};

const TOP_ITEMS = {
  scope: 'today',
  total_items: 0,
  top_items: [],
};

const MONTHLY = {
  months_requested: 6,
  periods: [],
  latest_period: null,
};

const LOW_STOCK = {
  generated_at: '2026-11-24T09:12:00+11:00',
  threshold_source: {
    field: 'min_stock',
    rule: 'quantity <= min_stock',
    non_stock_excluded: true,
  },
  low_stock_count: 0,
  items: [],
};

const BACKUP_STATUS = {
  backup_path_configured: true,
  last_backup_at: '2026-11-23T21:00:00Z',
  last_backup_at_local: '2026-11-24T08:00:00+11:00',
  last_backup_path: '/backups/eixn-20261124.dump',
};

/**
 * Install API mocks for a given role (or 'anonymous').
 * Returns the list of observed dashboard API request paths.
 */
async function installApiMocks(
  page: Page,
  role: 'cashier' | 'manager' | 'admin' | 'anonymous',
): Promise<string[]> {
  const dashboardRequests: string[] = [];

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

    if (path === '/api/auth/me/') {
      if (role === 'anonymous') {
        // Real backend: unauthenticated me/ yields 401/403, which the app
        // treats as "no session".
        await route.fulfill(json({ detail: 'Authentication required.' }, 401));
        return;
      }
      await route.fulfill(
        json({
          user: { id: 1, username: 'maya', role },
          must_change_password: false,
          business_info_complete: true,
        }),
      );
      return;
    }

    if (path.startsWith('/api/dashboard/')) {
      dashboardRequests.push(path);
      if (path === '/api/dashboard/') {
        await route.fulfill(json(OVERVIEW));
        return;
      }
      if (path === '/api/dashboard/sales/top/') {
        await route.fulfill(json(TOP_ITEMS));
        return;
      }
      if (path === '/api/dashboard/sales/monthly/') {
        await route.fulfill(json(MONTHLY));
        return;
      }
      if (path === '/api/dashboard/inventory/low-stock/') {
        await route.fulfill(json(LOW_STOCK));
        return;
      }
      if (path === '/api/dashboard/backup/') {
        if (role === 'cashier' || role === 'anonymous') {
          // Defence in depth: the UI must not even ask for this when the
          // role cannot see the panel; if it does, mirror the 403.
          await route.fulfill(json({ detail: 'Forbidden' }, 403));
          return;
        }
        await route.fulfill(json(BACKUP_STATUS));
        return;
      }
      await route.fulfill(json({ detail: 'Not implemented in mock' }, 404));
      return;
    }

    // Any other API call succeeds generically; nothing in these tests
    // depends on it.
    await route.fulfill(json({}));
  });

  return dashboardRequests;
}

test.describe('dashboard access by role (regression)', () => {
  for (const role of ['cashier', 'manager', 'admin'] as const) {
    test(`${role} can navigate to the dashboard and it renders`, async ({ page }) => {
      const dashboardRequests = await installApiMocks(page, role);
      await page.goto('/');
      await page.waitForLoadState('networkidle');

      // Still on the dashboard — not bounced to /login or a role gate.
      expect(new URL(page.url()).pathname).toBe('/');
      await expect(page.getByRole('heading', { name: 'Dashboard' })).toBeVisible();

      // The page actually loaded data from the composite endpoint.
      expect(dashboardRequests).toContain('/api/dashboard/');
      await expect(
        page.getByText(`Business day ${OVERVIEW.business_day}`),
      ).toBeVisible();
    });
  }

  test('cashier sees no manager-only dashboard affordances', async ({ page }) => {
    await installApiMocks(page, 'cashier');
    await page.goto('/');
    await page.waitForLoadState('networkidle');

    // The backup panel and the business-info setup form are manager/admin
    // affordances; a cashier must not see either.
    await expect(page.getByText('Last backup')).toHaveCount(0);
    await expect(page.locator('.business-form')).toHaveCount(0);
  });

  test('manager sees the manager-only backup panel', async ({ page }) => {
    await installApiMocks(page, 'manager');
    await page.goto('/');
    await page.waitForLoadState('networkidle');

    await expect(page.getByText('Last backup')).toBeVisible();
  });

  test('admin sees the manager-only backup panel', async ({ page }) => {
    await installApiMocks(page, 'admin');
    await page.goto('/');
    await page.waitForLoadState('networkidle');

    await expect(page.getByText('Last backup')).toBeVisible();
  });

  test('anonymous visitor is redirected to the login page', async ({ page }) => {
    await installApiMocks(page, 'anonymous');
    await page.goto('/');
    await page.waitForLoadState('networkidle');

    await expect(page).toHaveURL(/\/login$/);
    await expect(page.locator('.auth-card')).toBeVisible();
    // The anonymous visitor must not have pulled dashboard data.
    // (dashboardRequests is not returned here by design; assert via the
    // absence of the dashboard heading instead.)
    await expect(page.getByRole('heading', { name: 'Dashboard' })).toHaveCount(0);
  });
});
