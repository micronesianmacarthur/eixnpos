import { describe, expect, it } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import { createElement } from 'react'

import { Logo } from '../../src/Logo'

describe('Logo', () => {
  it('renders a 24x24 svg with the storefront glyph', () => {
    const html = renderToStaticMarkup(createElement(Logo))
    expect(html).toMatch(/<svg/)
    expect(html).toMatch(/viewBox="0 0 24 24"/)
    expect(html).toMatch(/width="24"/)
    expect(html).toMatch(/height="24"/)
    expect(html).toMatch(/<path/)
  })

  it('is decorative by default so the wordmark stays the accessible name', () => {
    const html = renderToStaticMarkup(createElement(Logo))
    expect(html).toMatch(/aria-hidden="true"/)
  })

  it('inherits the theme color via currentColor, never hardcoding a stroke/fill', () => {
    const html = renderToStaticMarkup(createElement(Logo))
    // currentColor makes the glyph flip with --mm-text in light+dark; a
    // hardcoded color here would blow contrast on one of the two themes.
    expect(html).toMatch(/stroke="currentColor"/)
    expect(html).toMatch(/fill="none"/)
    expect(html).not.toMatch(/stroke="#/)
    expect(html).not.toMatch(/fill="#/)
  })

  it('respects an explicit size prop', () => {
    const html = renderToStaticMarkup(createElement(Logo, { size: 28 }))
    expect(html).toMatch(/width="28"/)
    expect(html).toMatch(/height="28"/)
    // viewBox stays fixed so the glyph scales crisply without layout shift.
    expect(html).toMatch(/viewBox="0 0 24 24"/)
  })

  it('merges a caller className onto the mm-logo base', () => {
    const html = renderToStaticMarkup(
      createElement(Logo, { className: 'sidebar-brand-mark' }),
    )
    expect(html).toMatch(/class="mm-logo sidebar-brand-mark"/)
  })
})
