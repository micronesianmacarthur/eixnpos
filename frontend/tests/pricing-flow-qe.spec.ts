import { expect, test, type Page, type Route } from '@playwright/test';

/**
 * QE cross-feature flow (t_bfe754c3): a margin saved on Settings → Pricing
 * must be the margin the product form actually uses.
 *
 * Neither existing spec covers the seam: `settings-pricing.spec.ts` only
 * exercises the settings page, `product.spec.ts` only the product form with
 * a fixed margin. These tests drive both pages in one session against a
 * single mutable pricing store.
 */
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
  website: '',
  backup_path: '/backup',
};

interface PricingStore {
  margin: number;
  /** When true every pricing GET/PATCH fails, as an unavailable backend would. */
  unavailable: boolean;
  requests: Array<{ method: string; path: string; body?: string }>;
}

function json(data: unknown, status = 200) {
  return { status, contentType: 'application/json', body: JSON.stringify(data) };
}

async function installPricingStore(
  page: Page,
  options: Partial<PricingStore> = {},
): Promise<PricingStore> {
  const store: PricingStore = {
    margin: 15,
    unavailable: false,
    requests: [],
    ...options,
  };

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
    store.requests.push({ method, path, body: request.postData() ?? undefined });

    if (path === '/api/settings/pricing/') {
      if (store.unavailable) {
        await route.fulfill(json({ detail: 'Internal server error.' }, 500));
        return;
      }
      if (method === 'PATCH') {
        store.margin = JSON.parse(request.postData() || '{}').default_target_margin_percent;
      }
      await route.fulfill(json({ default_target_margin_percent: store.margin }));
      return;
    }

    if (path === '/api/auth/me/') {
      await route.fulfill(json(AUTH_ME));
      return;
    }
    if (path === '/api/business/') {
      await route.fulfill(json(BUSINESS_INFO));
      return;
    }
    if (path === '/api/suppliers/' || path === '/api/categories/' || path === '/api/products/') {
      await route.fulfill(json([]));
      return;
    }

    await route.fulfill(json({ detail: `No mock for ${method} ${path}` }, 404));
  });

  return store;
}

const marginInput = (page: Page) => page.getByLabel('Default target margin (%)');
const costField = (page: Page) =>
  page.getByRole('spinbutton', { name: 'Cost', exact: true });
const suggestedField = (page: Page) =>
  page.getByRole('textbox', { name: 'Suggested price' });

async function openPricingTab(page: Page, store: PricingStore) {
  await page.goto(`${BASE_URL}/settings`);
  await expect(page.getByRole('heading', { level: 1, name: 'Settings' })).toBeVisible();
  await page.getByRole('tab', { name: 'Pricing' }).click();
  await expect(marginInput(page)).toHaveValue(String(store.margin));
}

test('a margin saved in Settings drives the product form suggestion', async ({ page }) => {
  const store = await installPricingStore(page);
  await openPricingTab(page, store);

  await marginInput(page).fill('25');
  await page.getByRole('button', { name: 'Save' }).click();
  await expect(page.getByRole('status')).toHaveText('Pricing settings saved.');
  expect(store.margin).toBe(25);

  await page.goto(`${BASE_URL}/inventory/new`);
  await costField(page).fill('100');
  await expect(suggestedField(page)).toHaveValue('125.00');
});

test('a saved margin of 0 is used, not mistaken for the fallback', async ({ page }) => {
  const store = await installPricingStore(page);
  await openPricingTab(page, store);

  await marginInput(page).fill('0');
  await page.getByRole('button', { name: 'Save' }).click();
  await expect(page.getByRole('status')).toHaveText('Pricing settings saved.');
  expect(store.margin).toBe(0);

  await page.goto(`${BASE_URL}/inventory/new`);
  await costField(page).fill('100');
  await expect(suggestedField(page)).toHaveValue('100.00');
});

test('an unavailable settings endpoint falls back to 15% in the product form', async ({ page }) => {
  await installPricingStore(page, { unavailable: true });

  await page.goto(`${BASE_URL}/inventory/new`);
  await costField(page).fill('100');
  await expect(suggestedField(page)).toHaveValue('115.00');
});

test('an empty unit cost leaves the suggestion empty', async ({ page }) => {
  const store = await installPricingStore(page, { margin: 25 });

  await page.goto(`${BASE_URL}/inventory/new`);
  // Cost is pre-filled with 0.00, so the suggestion starts at 0.00 too.
  await expect(suggestedField(page)).toHaveValue('0.00');
  // Clearing the cost is the empty-cost case: no suggestion at all.
  await costField(page).fill('');
  await expect(suggestedField(page)).toHaveValue('');
  await costField(page).fill('40');
  await expect(suggestedField(page)).toHaveValue('50.00');
  await costField(page).fill('');
  await expect(suggestedField(page)).toHaveValue('');
  expect(store.margin).toBe(25);
});
