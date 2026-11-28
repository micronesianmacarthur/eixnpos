import { expect, test, type Page, type Route } from '@playwright/test';

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

interface PricingMock {
  /** Value the mocked API currently stores. */
  margin: number;
  failLoad: boolean;
  failSave: boolean;
  requests: Array<{ method: string; path: string; body?: string }>;
}

function json(data: unknown, status = 200) {
  return { status, contentType: 'application/json', body: JSON.stringify(data) };
}

async function installApiMocks(page: Page, options: Partial<PricingMock> = {}): Promise<PricingMock> {
  const mock: PricingMock = {
    margin: 15,
    failLoad: false,
    failSave: false,
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
    mock.requests.push({ method, path, body: request.postData() ?? undefined });

    if (path === '/api/auth/me/') {
      await route.fulfill(json(AUTH_ME));
      return;
    }
    if (path === '/api/business/') {
      await route.fulfill(json(BUSINESS_INFO));
      return;
    }
    if (path === '/api/settings/pricing/') {
      if (method === 'PATCH') {
        if (mock.failSave) {
          await route.fulfill(json({ detail: 'Internal server error.' }, 500));
          return;
        }
        const body = JSON.parse(request.postData() || '{}');
        mock.margin = body.default_target_margin_percent;
        await route.fulfill(json({ default_target_margin_percent: mock.margin }));
        return;
      }
      if (mock.failLoad) {
        await route.fulfill(json({ detail: 'Internal server error.' }, 500));
        return;
      }
      await route.fulfill(json({ default_target_margin_percent: mock.margin }));
      return;
    }

    await route.fulfill(json({ detail: `No mock for ${method} ${path}` }, 404));
  });

  return mock;
}

async function openPricingTab(page: Page, options: Partial<PricingMock> = {}): Promise<PricingMock> {
  const mock = await installApiMocks(page, options);
  await page.goto(`${BASE_URL}/settings`);
  await expect(page.getByRole('heading', { level: 1, name: 'Settings' })).toBeVisible();
  await page.getByRole('tab', { name: 'Pricing' }).click();
  await expect(page.getByRole('tab', { name: 'Pricing' })).toHaveAttribute('aria-selected', 'true');
  return mock;
}

const marginInput = (page: Page) => page.getByLabel('Default target margin (%)');
const saveButton = (page: Page) => page.getByRole('button', { name: 'Save' });

test('shows the Pricing tab with the configured margin', async ({ page }) => {
  const mock = await openPricingTab(page, { margin: 18.5 });

  await expect(page.getByRole('heading', { name: 'Pricing' })).toBeVisible();
  await expect(marginInput(page)).toHaveValue('18.5');
  await expect(page.getByText(/Percentage used to suggest a selling price/)).toBeVisible();
  expect(mock.requests.filter((r) => r.path === '/api/settings/pricing/')).toEqual([
    expect.objectContaining({ method: 'GET' }),
  ]);
});

test('defaults to 15 when no margin has been saved', async ({ page }) => {
  await openPricingTab(page, { margin: 15 });

  await expect(marginInput(page)).toHaveValue('15');
  await expect(saveButton(page)).toBeEnabled();
});

test('saves a new margin and reloads the persisted value', async ({ page }) => {
  const mock = await openPricingTab(page);

  await marginInput(page).fill('22.5');
  await saveButton(page).click();

  await expect(page.getByRole('status')).toHaveText('Pricing settings saved.');

  const patches = mock.requests.filter(
    (r) => r.method === 'PATCH' && r.path === '/api/settings/pricing/',
  );
  expect(patches).toHaveLength(1);
  expect(JSON.parse(patches[0].body ?? '{}')).toEqual({ default_target_margin_percent: 22.5 });

  // The saved value survives a full page reload (fresh GET).
  await page.reload();
  await page.getByRole('tab', { name: 'Pricing' }).click();
  await expect(marginInput(page)).toHaveValue('22.5');
  await expect(page.getByRole('status')).toHaveCount(0);
});

test('blocks an out-of-range margin before it reaches the server', async ({ page }) => {
  const mock = await openPricingTab(page);

  await marginInput(page).fill('150');
  await saveButton(page).click();

  await expect(page.getByRole('alert')).toHaveText('Enter a number between 0 and 100.');
  await expect(marginInput(page)).toHaveAttribute('aria-invalid', 'true');
  expect(mock.requests.filter((r) => r.method === 'PATCH')).toEqual([]);
});

test('blocks a margin with too many decimal places before it reaches the server', async ({ page }) => {
  const mock = await openPricingTab(page);

  await marginInput(page).fill('12.345');
  await saveButton(page).click();

  await expect(page.getByRole('alert')).toHaveText(
    'Enter a percentage with at most 2 decimal places.',
  );
  expect(mock.requests.filter((r) => r.method === 'PATCH')).toEqual([]);
});

test('blocks an empty margin before it reaches the server', async ({ page }) => {
  const mock = await openPricingTab(page);

  await marginInput(page).fill('');
  await saveButton(page).click();

  await expect(page.getByRole('alert')).toHaveText('Enter a number between 0 and 100.');
  expect(mock.requests.filter((r) => r.method === 'PATCH')).toEqual([]);
});

test('cancelling an edit restores the saved margin', async ({ page }) => {
  const mock = await openPricingTab(page, { margin: 18.5 });

  await marginInput(page).fill('30');
  await page.getByRole('button', { name: 'Cancel' }).click();

  await expect(marginInput(page)).toHaveValue('18.5');
  expect(mock.requests.filter((r) => r.method === 'PATCH')).toEqual([]);
});

test('shows an error and recovers when pricing settings fail to load', async ({ page }) => {
  const mock = await openPricingTab(page, { failLoad: true, margin: 15 });

  await expect(page.getByRole('alert')).toContainText('Failed to load pricing settings.');
  // No form is offered while the saved value is unknown.
  await expect(page.locator('#settings-panel-pricing input')).toHaveCount(0);

  mock.failLoad = false;
  await page.getByRole('button', { name: 'Try again' }).click();
  await expect(marginInput(page)).toHaveValue('15');
  await expect(page.getByRole('alert')).toHaveCount(0);
});

test('reports a save failure and keeps the typed value', async ({ page }) => {
  await openPricingTab(page, { failSave: true });

  await marginInput(page).fill('20');
  await saveButton(page).click();

  await expect(page.getByRole('alert')).toHaveText('Internal server error.');
  await expect(marginInput(page)).toHaveValue('20');
  await expect(page.getByRole('status')).toHaveCount(0);
});
