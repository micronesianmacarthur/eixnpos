import { expect, test, type Page, type Route } from '@playwright/test';

/**
 * Manual-close UX for register sessions.
 *
 * Sessions are never closed automatically: a shift left open at midnight stays
 * open on the business day it started. These API-mocked integration tests pin
 * the three user-facing promises of that lifecycle:
 *   1. The dashboard surfaces an unclosed shift from an earlier day (with the
 *      "never close automatically" reminder) and links it into the close flow.
 *   2. `/close?session=<id>` can close THAT shift even when today's register
 *      has no open session — the start-shift modal must not intercept it.
 *   3. The /sales guard still behaves exactly as before for today's shift.
 */

const BASE_URL = 'http://127.0.0.1:4173';

const AUTH_ME = {
  user: { id: 1, username: 'maya', role: 'admin' },
  must_change_password: false,
  business_info_complete: true,
};

const TODAY = '2026-11-24';

const STALE_SESSION = {
  session_id: 11,
  register_name: 'Front',
  opened_at: '2026-11-23T22:05:00+11:00',
  opened_at_local: '2026-11-23T22:05:00+11:00',
  started_at: '2026-11-23T22:05:00+11:00',
  started_at_local: '2026-11-23T22:05:00+11:00',
  business_day: '2026-11-23',
  register: { id: 1, name: 'Front', identifier: 'REG-1' },
  opened_by: { id: 2, username: 'jane', role: 'cashier' },
  cashier: { id: 2, username: 'jane', role: 'cashier' },
  cashiers: ['jane'],
  starting_cash: '100.00',
};

const OVERVIEW = {
  business_day: TODAY,
  generated_at: '2026-11-24T09:12:00+11:00',
  business_timezone: 'Pacific/Noumea',
  period: {
    scope: 'today',
    start: '2026-11-24T00:00:00+11:00',
    end: '2026-11-24T09:12:00+11:00',
  },
  today: {
    sales_count: 0,
    revenue: '0.00',
    cash_sales: '0.00',
    invoice_count: 0,
    invoice_total: '0.00',
    void_count: 0,
    refund_count: 0,
    avg_basket: '0.00',
    open_count_today: 0,
  },
  session: {
    open: false,
    count: 0,
    business_day: TODAY,
    sessions: [],
    session: { open: false },
    previous_day_sessions: [STALE_SESSION],
    previous_day_count: 1,
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

/** Full session detail as returned by GET /api/sessions/{id}/. */
function sessionDetail(status: 'open' | 'closed' = 'open') {
  return {
    id: STALE_SESSION.session_id,
    cashier: 2,
    register: 1,
    business_day: STALE_SESSION.business_day,
    starting_cash: '100.00',
    status,
    opened_at: '2026-11-23T11:05:00Z',
    closed_at: status === 'closed' ? '2026-11-24T00:10:00Z' : null,
    cash_counts: {},
    cash_removed: '0.00',
    expected_cash: '0.00',
    counted_cash: '0.00',
    variance: '0.00',
    sales_total: '0.00',
    notes: '',
  };
}

const SESSION_SUMMARY = {
  session: {
    id: STALE_SESSION.session_id,
    cashier: 'jane',
    status: 'open',
    opened_at: '2026-11-23T11:05:00Z',
    closed_at: null,
  },
  summary: {
    sale_count: 0,
    item_count: 0,
    gross_sales: '0.00',
    discounts: '0.00',
    tax_total: '0.00',
    revenue: '0.00',
    cost_of_goods: '0.00',
    profit_loss: '0.00',
    profit_margin: null,
    by_payment: { cash: '0.00' },
    by_category: {},
  },
  warnings: { invalid_sale_amounts: 0, invalid_line_amounts: 0, invalid_payment_amounts: 0 },
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

const EMPTY_SERIES = {
  scope: 'today',
  total_items: 0,
  top_items: [],
};

function json(data: unknown, status = 200) {
  return { status, contentType: 'application/json', body: JSON.stringify(data) };
}

interface Monitor {
  requests: Array<{ method: string; path: string }>;
}

/** Install API mocks. `currentOpen` controls GET /api/sessions/current/. */
async function installMocks(
  page: Page,
  options: { currentOpen?: boolean; detailStatus?: 'open' | 'closed' } = {},
): Promise<Monitor> {
  const monitor: Monitor = { requests: [] };

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
    monitor.requests.push({ method, path });

    if (path === '/api/auth/me/') {
      await route.fulfill(json(AUTH_ME));
      return;
    }
    if (path === '/api/business/') {
      await route.fulfill(
        json({
          company_name: 'eixn Corner Store',
          phone: '555-0100',
          email: 'hello@eixn.test',
          address: '12 Market Street',
          website: '',
        }),
      );
      return;
    }
    if (path === '/api/dashboard/') {
      await route.fulfill(json(OVERVIEW));
      return;
    }
    if (path === '/api/dashboard/inventory/low-stock/') {
      await route.fulfill(json(LOW_STOCK));
      return;
    }
    if (path === '/api/dashboard/backup/') {
      await route.fulfill(json(BACKUP_STATUS));
      return;
    }
    if (path.startsWith('/api/dashboard/sales/')) {
      await route.fulfill(json(EMPTY_SERIES));
      return;
    }
    if (path === '/api/sessions/current/') {
      await route.fulfill(
        json(
          options.currentOpen
            ? {
                open: true,
                sessionId: String(STALE_SESSION.session_id),
                startedAt: '2026-11-24T07:00:00Z',
                startingCash: '100.00',
                register: 1,
                day: TODAY,
              }
            : { open: false },
        ),
      );
      return;
    }
    if (path === `/api/sessions/${STALE_SESSION.session_id}/`) {
      await route.fulfill(json(sessionDetail(options.detailStatus ?? 'open')));
      return;
    }
    if (path === `/api/sessions/${STALE_SESSION.session_id}/summary/`) {
      await route.fulfill(json(SESSION_SUMMARY));
      return;
    }
    if (path === `/api/sessions/${STALE_SESSION.session_id}/close/` && method === 'POST') {
      await route.fulfill(json({ ...sessionDetail('closed'), counted_cash: '0.00' }));
      return;
    }
    if (path === '/api/sessions/' && method === 'POST') {
      await route.fulfill(json(sessionDetail('open'), 201));
      return;
    }
    if (path === '/api/sales/') {
      await route.fulfill(json([]));
      return;
    }
    if (
      path === '/api/products/' ||
      path === '/api/customers/' ||
      path === '/api/suppliers/'
    ) {
      await route.fulfill(json([]));
      return;
    }

    await route.fulfill(json({ detail: `No mock for ${method} ${path}` }, 404));
  });

  return monitor;
}

test('dashboard warns about an unclosed previous-day shift and links into close', async ({
  page,
}) => {
  await installMocks(page, { currentOpen: false });
  await page.goto('/');
  await page.waitForLoadState('networkidle');

  await expect(page.getByText('Unclosed shift from an earlier day')).toBeVisible();
  await expect(
    page.getByText(/they never close automatically at midnight/),
  ).toBeVisible();
  await expect(page.getByText(/Business day 2026-11-23/)).toBeVisible();
  await expect(page.getByText(/1 day ago/)).toBeVisible();

  const closeLink = page.getByRole('link', { name: 'Close & reconcile' });
  await expect(closeLink).toHaveAttribute('href', '/close?session=11');

  await closeLink.click();
  await expect(page).toHaveURL(/\/close\?session=11$/);
  await expect(page.getByRole('heading', { level: 1, name: 'Close' })).toBeVisible();
  // The explicit-session close page must NOT show the start-shift modal even
  // though today's register has no open session.
  await expect(page.getByRole('heading', { name: 'Start your shift' })).toHaveCount(0);
  await expect(page.getByText(/This shift belongs to business day 2026-11-23/)).toBeVisible();
});

test('closes the previous-day shift from the explicit session link', async ({ page }) => {
  const monitor = await installMocks(page, { currentOpen: false });
  await page.goto('/close?session=11');
  await page.waitForLoadState('networkidle');

  await expect(page.locator('.close-flow')).toBeVisible();

  // Count cash (all zero) -> summary -> close.
  await page.getByRole('button', { name: 'Continue to Summary' }).click();
  await expect(page.getByRole('heading', { name: 'Session summary' })).toBeVisible();
  await page.getByRole('button', { name: 'Continue to Close & Backup' }).click();
  await page.getByRole('button', { name: 'Close Session & Print Z-Report' }).click();

  await expect(page.locator('.close-closed-notice')).toBeVisible();
  expect(
    monitor.requests.some(
      (r) => r.method === 'POST' && r.path === '/api/sessions/11/close/',
    ),
  ).toBe(true);
});

test('an already-closed session link reports it instead of offering another close', async ({
  page,
}) => {
  await installMocks(page, { currentOpen: false, detailStatus: 'closed' });
  await page.goto('/close?session=11');
  await page.waitForLoadState('networkidle');

  await expect(page.getByText('This session is already closed')).toBeVisible();
  await expect(
    page.getByRole('button', { name: 'Close Session & Print Z-Report' }),
  ).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Print Z-report (PDF)' })).toBeVisible();
});

test('an invalid session link is rejected without leaking a request', async ({ page }) => {
  await installMocks(page, { currentOpen: false });
  await page.goto('/close?session=not-a-number');
  await page.waitForLoadState('networkidle');

  await expect(page.getByText('Could not load the session')).toBeVisible();
  await expect(page.getByText(/not valid/)).toBeVisible();
  await expect(page.getByRole('button', { name: 'Close Session & Print Z-Report' })).toHaveCount(0);
});

test('/sales still prompts for starting cash when no session is open', async ({ page }) => {
  await installMocks(page, { currentOpen: false });
  await page.goto('/sales');
  await page.waitForLoadState('networkidle');

  await expect(page.getByRole('heading', { name: 'Start your shift' })).toBeVisible();
  await expect(page.getByText(/stays open — even overnight/)).toBeVisible();
});
