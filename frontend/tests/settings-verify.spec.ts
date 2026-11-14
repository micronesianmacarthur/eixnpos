import { expect, test, type Page, type Route } from '@playwright/test';

const BASE_URL = 'http://127.0.0.1:4173';

type Role = 'cashier' | 'manager' | 'admin';

interface NetworkMonitor {
  external: string[];
  failed: string[];
}

const AUTH_ME = {
  user: {
    id: 1,
    username: 'maya',
    role: 'admin' as Role,
  },
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

function json(data: unknown, status = 200) {
  return {
    status,
    contentType: 'application/json',
    body: JSON.stringify(data),
  };
}

async function installApiMocks(page: Page): Promise<NetworkMonitor> {
  const monitor: NetworkMonitor = { external: [], failed: [] };

  page.on('requestfailed', (request) => {
    const url = new URL(request.url());
    if (url.origin === BASE_URL) {
      monitor.failed.push(
        `${request.method()} ${url.pathname}: ${request.failure()?.errorText ?? 'failed'}`,
      );
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

    if (path === '/api/auth/me/') {
      await route.fulfill(json(AUTH_ME));
      return;
    }

    if (path === '/api/business/') {
      await route.fulfill(json(BUSINESS_INFO));
      return;
    }

    await route.fulfill(json({ detail: `No mock for ${method} ${path}` }, 404));
  });

  return monitor;
}

function luminance(r: number, g: number, b: number) {
  const f = (c: number) => {
    const v = c / 255;
    return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
  };
  return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
}

function contrast(a: string, b: string) {
  const parse = (s: string) => s.match(/\d+(\.\d+)?/g)!.slice(0, 3).map(Number);
  const [l1, l2] = [parse(a), parse(b)].map(([r, g, bl]) => luminance(r, g, bl));
  return (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05);
}

// --- qe theme/responsive verification (t_fc5de1b3) ---

const SIZES = [
  ['mobile', 390, 844],
  ['tablet', 768, 1024],
  ['desktop', 1440, 900],
] as const;

for (const [name, width, height] of SIZES) {
  test(`no horizontal overflow at ${name} (${width}px), light and dark`, async ({ page }) => {
    for (const dark of [false, true]) {
      await installApiMocks(page);
      await page.addInitScript((d) => {
        if (d) window.localStorage.setItem('eixnpos-theme', 'dark');
      }, dark);
      await page.setViewportSize({ width, height });
      await page.goto(`${BASE_URL}/settings`);
      await expect(page.getByRole('heading', { level: 1, name: 'Settings' })).toBeVisible();
      for (const tab of ['Store', 'Users', 'Backup', 'Appearance']) {
        await page.getByRole('tab', { name: tab }).click();
        const overflow = await page.evaluate(
          () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
        );
        expect(overflow, `${name} dark=${dark} tab=${tab}`).toBeLessThanOrEqual(0);
      }
    }
  });
}

test('key text tokens meet WCAG AA contrast in both themes', async ({ page }) => {
  await installApiMocks(page);
  await page.goto(`${BASE_URL}/settings`);
  await expect(page.getByRole('heading', { level: 1, name: 'Settings' })).toBeVisible();

  for (const dark of [false, true]) {
    await page.evaluate((d) => document.documentElement.classList.toggle('dark', d), dark);
    await page.waitForTimeout(300); // let token transitions settle

    const report = await page.evaluate(() => {
      const cs = getComputedStyle(document.documentElement);
      const get = (n: string) => cs.getPropertyValue(n).trim();
      const resolve = (color: string) => {
        const tmp = document.createElement('div');
        tmp.style.color = color;
        document.body.appendChild(tmp);
        const rgb = getComputedStyle(tmp).color;
        tmp.remove();
        return rgb;
      };
      return {
        text: resolve(get('--mm-text')),
        muted: resolve(get('--mm-text-muted')),
        body: getComputedStyle(document.body).backgroundColor,
        card: getComputedStyle(document.querySelector('.mm-card') ?? document.body).backgroundColor,
      };
    });

    // WCAG AA for normal (14px) text: 4.5:1. Both themes hold 4.5 for every
    // pair (light-mode --mm-text-muted fixed in t_7e7e2a06; wobbles below this
    // are a regression).
    expect(contrast(report.text, report.body), `text/bg dark=${dark}`).toBeGreaterThanOrEqual(4.5);
    expect(contrast(report.text, report.card), `text/surface dark=${dark}`).toBeGreaterThanOrEqual(4.5);
    expect(contrast(report.muted, report.body), `muted/bg dark=${dark}`).toBeGreaterThanOrEqual(4.5);
    expect(contrast(report.muted, report.card), `muted/surface dark=${dark}`).toBeGreaterThanOrEqual(4.5);
  }
});

// Primary button (white on --mm-primary) hit the WCAG AA 4.5:1 floor once the
// light-mode token was fixed in t_7e7e2a06. Kept as a focused check so a
// future palette change cannot silently drop it below AA.
test('light-mode primary button meets WCAG AA contrast', async ({ page }) => {
  await installApiMocks(page);
  await page.goto(`${BASE_URL}/settings`);
  await expect(page.getByRole('heading', { level: 1, name: 'Settings' })).toBeVisible();

  const pairs = await page.evaluate(() => {
    const cs = getComputedStyle(document.documentElement);
    const get = (n: string) => cs.getPropertyValue(n).trim();
    const resolve = (color: string) => {
      const tmp = document.createElement('div');
      tmp.style.color = color;
      document.body.appendChild(tmp);
      const rgb = getComputedStyle(tmp).color;
      tmp.remove();
      return rgb;
    };
    const btn = [...document.querySelectorAll('button')].find(
      (b) => b.textContent?.trim() === 'Save',
    );
    return {
      muted: resolve(get('--mm-text-muted')),
      body: getComputedStyle(document.body).backgroundColor,
      card: getComputedStyle(document.querySelector('.mm-card') ?? document.body).backgroundColor,
      btnColor: btn ? getComputedStyle(btn).color : null,
      btnBg: btn ? getComputedStyle(btn).backgroundColor : null,
    };
  });

  expect(contrast(pairs.btnColor!, pairs.btnBg!), 'primary button AA').toBeGreaterThanOrEqual(4.5);
});

test('tab hover and focus states are styled', async ({ page }) => {
  await installApiMocks(page);
  await page.goto(`${BASE_URL}/settings`);
  await expect(page.getByRole('heading', { level: 1, name: 'Settings' })).toBeVisible();
  const tab = page.getByRole('tab', { name: 'Users' });
  const base = await tab.evaluate((el) => getComputedStyle(el).color);
  // The tab color transitions (160ms, or the reduced-motion 0.01ms frame) on
  // hover, so poll for the settled value instead of reading mid-transition.
  await tab.hover();
  await expect
    .poll(
      () => tab.evaluate((el) => getComputedStyle(el).color),
      { timeout: 2000, message: 'hover changes tab color' },
    )
    .not.toBe(base);
  await tab.focus();
  const outline = await tab.evaluate((el) => getComputedStyle(el).outlineStyle);
  const shadow = await tab.evaluate((el) => getComputedStyle(el).boxShadow);
  expect(outline !== 'none' || shadow !== 'none', 'focused tab has a visible outline or shadow').toBe(true);
});
