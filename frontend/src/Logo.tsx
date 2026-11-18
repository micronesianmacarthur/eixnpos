import type { ReactNode } from 'react'

export interface LogoProps {
  className?: string
  /** Mark edge length in px. Default 24, matching the 24×24 viewBox. */
  size?: number
}

/**
 * eixnPOS brand mark (24×24 vector storefront glyph).
 *
 * Decorative by default (aria-hidden): pair it with visible "eixnPOS"
 * wordmark text, which is the accessible name (see container aria-label).
 *
 * The glyph is stroked with `currentColor` and inherits `color` from its
 * container, so it flips with the `--mm-text` / `--mm-text-muted` theme tokens
 * via ThemeProvider — no per-theme markup needed. Glyph data must stay inline
 * here (do not swap for `<img src="/logo.svg">`): a standalone <img> resolves
 * `currentColor` to the UA-default black, which vanishes on dark surfaces.
 * Source: frontend/public/logo.svg (task t_1d345a26).
 */
export function Logo({ className, size = 24 }: LogoProps): ReactNode {
  return (
    <svg
      aria-hidden="true"
      className={className ? `mm-logo ${className}` : 'mm-logo'}
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d='M6 12c.263 0 .524-.06.767-.175a2 2 0 0 0 .65-.491c.186-.21.333-.46.433-.734.1-.274.15-.568.15-.864a2.4 2.4 0 0 0 .586 1.591c.375.422.884.659 1.414.659.53 0 1.04-.237 1.414-.659A2.4 2.4 0 0 0 12 9.736a2.4 2.4 0 0 0 .586 1.591c.375.422.884.659 1.414.659.53 0 1.04-.237 1.414-.659A2.4 2.4 0 0 0 16 9.736c0 .295.052.588.152.861s.248.521.434.73a2 2 0 0 0 .649.488 1.809 1.809 0 0 0 1.53 0 2.03 2.03 0 0 0 .65-.488c.185-.209.332-.457.433-.73.1-.273.152-.566.152-.861 0-.974-1.108-3.85-1.618-5.121A.983.983 0 0 0 17.466 4H6.456a.986.986 0 0 0-.93.645C5.045 5.962 4 8.905 4 9.736c.023.59.241 1.148.611 1.567.37.418.865.667 1.389.697Zm0 0c.328 0 .651-.091.94-.266A2.1 2.1 0 0 0 7.66 11h.681a2.1 2.1 0 0 0 .718.734c.29.175.613.266.942.266.328 0 .651-.091.94-.266.29-.174.537-.427.719-.734h.681a2.1 2.1 0 0 0 .719.734c.289.175.612.266.94.266.329 0 .652-.091.942-.266.29-.174.536-.427.718-.734h.681c.183.307.43.56.719.734.29.174.613.266.941.266a1.819 1.819 0 0 0 1.06-.351M6 12a1.766 1.766 0 0 1-1.163-.476M5 12v7a1 1 0 0 0 1 1h2v-5h3v5h7a1 1 0 0 0 1-1v-7m-5 3v2h2v-2h-2Z' />
    </svg>
  )
}
