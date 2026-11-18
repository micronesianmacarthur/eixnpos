# Logo Asset — eixnPOS brand vector (dark-theme ready)

> **Task:** t_1d345a26 · **Asset:** `frontend/public/logo.svg`
> Consumed by downstream task **t_a7bb7e8c** (shared logo component).

## Asset

- **Path:** `frontend/public/logo.svg` — Vite static/public dir. Served at `/logo.svg` and
  copied verbatim into `dist/` on `npm run build`.
- **Format:** SVG vector (XML). Not rasterized, not redesigned.
- **Source extent:** `viewBox="0 0 24 24"`, native `width`/`height` 24.

## Theming contract (dark-theme visibility)

- The glyph is stroked with `stroke="currentColor"` (`fill="none"`, `stroke-width="2"`,
  round caps/joins), so it inherits the CSS `color` of its container.
- **Do not load via `<img src="/logo.svg">` inside the themed app.** `currentColor` in a
  standalone image resolves to the UA default `black`, which is invisible on dark surfaces.
- **Correct consumption:** inline the SVG element in a React component (same pattern as
  `src/Icon.tsx`) and set `color` to a theme token. The `--mm-*` light/dark pairs live in
  `src/theme.css` and flip with the app's `ThemeProvider`, so no per-theme markup is needed:
  - `var(--mm-text)` — default logo color, correct in both themes
  - `var(--mm-text-muted)` — quieter/de-emphasised brand mark
- SVG is resolution-independent, so any CSS `width`/`height` (20–28px, including the
  collapsed sidebar mark) stays crisp; no bitmap/retina variant required.

## Style tokens (defined in `src/theme.css`)

| Token | Meaning | Use |
|-------|---------|-----|
| `--mm-text` | primary foreground, both themes | default logo stroke colour |
| `--mm-text-muted` | secondary foreground, both themes | muted brand mark |

Apply the token as `color` on the wrapper element and rely on `currentColor`.

## Accessibility note (for the consuming component)

The raw file carries no ARIA attributes. The component that inlines it should mark the
mark decorative (`aria-hidden="true"` on the `<svg>`) when adjacent text identifies the
brand, or supply an accessible label (`role="img"` + `<title>`/`aria-label`) when the logo
is the sole brand identifier (auth card, collapsed sidebar).

## Validation

- Parsed as well-formed XML.
- Committed to branch `wt/t_1d345a26`.
