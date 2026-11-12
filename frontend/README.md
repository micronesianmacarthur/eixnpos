# eixnPOS frontend

React 19 + Vite + TypeScript client for eixnPOS.

## Commands

```bash
npm install
npm run dev        # local dev server (proxies /api to 127.0.0.1:8000)
npm run build      # type-check and production build
npm run lint       # oxlint
npm run test:e2e   # Playwright against the production build + vite preview
npm run test:e2e:update  # refresh visual snapshots
```

The Playwright suite builds the app, starts `vite preview` on port 4173, mocks
every API call in the browser, and aborts any request outside the preview
origin. It also verifies that Inter is bundled and active. Install the browser
once with `npx playwright install chromium` if needed.

## Theme

`src/theme.css` is the single global theme layer. It bundles the Latin Inter
variable font from `@fontsource-variable/inter`, defines the MaterialM light
tokens (`--mm-*`), aliases the older CSS variables for un-migrated screens, and
provides the shared `mm-` UI primitives (cards, buttons, fields, tables,
badges, modals, alerts, loading and empty states).

Icons stay inline in `src/Icon.tsx`; there are no icon fonts or CDNs. The
production build makes no requests outside its own origin, so the POS keeps
working offline.
