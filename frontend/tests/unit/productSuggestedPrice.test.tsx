// @vitest-environment jsdom
/**
 * Behavioural tests for the product form's suggested price wiring.
 *
 * The pure formula is covered in `suggestedPrice.test.ts`; these tests pin the
 * page-level contract:
 *  - the configured margin is fetched from `/api/settings/pricing/` and used
 *    for the calculation (gross margin 15 on a cost of 100 => 117.65);
 *  - a changed saved margin is picked up by the next form load;
 *  - an unavailable or malformed setting falls back to 15%;
 *  - clearing the cost leaves the suggestion empty.
 *
 * `apiFetch` and the auth context are mocked; no network is touched.
 */
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import '@testing-library/jest-dom/vitest'
import { MemoryRouter, Route, Routes } from 'react-router-dom'

import { apiFetch } from '../../src/api'
import ProductPage from '../../src/ProductPage'

vi.mock('../../src/api', () => ({
  apiFetch: vi.fn(),
  ApiError: class ApiError extends Error {
    status = 0
    data: unknown = null
  },
}))
const mockedFetch = vi.mocked(apiFetch)

vi.mock('../../src/authContext', () => ({
  useAuth: () => ({
    me: {
      user: { role: 'admin', username: 'maya' },
      business_info_complete: true,
    },
    setMe: () => {},
    loading: false,
    refresh: vi.fn(),
  }),
}))

type Handler = (url: string, init?: RequestInit) => unknown | Promise<unknown>

/** Install an apiFetch stub; unmatched URLs fail loudly. */
function routeApi(handlers: Record<string, Handler>) {
  mockedFetch.mockImplementation(async (url: string) => {
    const match = Object.keys(handlers).find((key) => url.startsWith(key))
    if (!match) throw new Error(`Unexpected apiFetch in test: ${url}`)
    return handlers[match](url)
  })
}

/** Default handlers for rendering the New product form. */
function baseHandlers(pricing: Handler): Record<string, Handler> {
  return {
    '/api/settings/pricing/': pricing,
    '/api/suppliers/': () => [],
    '/api/categories/': () => [],
    '/api/products/': () => [],
  }
}

function renderCreateForm() {
  return render(
    <MemoryRouter initialEntries={['/inventory/new']}>
      <Routes>
        <Route path="/inventory/new" element={<ProductPage />} />
        <Route path="/inventory" element={<div>Inventory</div>} />
      </Routes>
    </MemoryRouter>,
  )
}

const suggestedPriceField = () =>
  screen.getByRole('textbox', { name: 'Suggested price' })

const costField = () => screen.getByRole('spinbutton', { name: 'Cost' })

describe('ProductPage suggested price', () => {
  beforeEach(() => {
    mockedFetch.mockReset()
  })

  afterEach(() => {
    cleanup()
  })

  it('uses the configured margin: 15% on a cost of 100 gives 117.65', async () => {
    routeApi(baseHandlers(() => ({ default_target_margin_percent: 15 })))
    renderCreateForm()

    await waitFor(() =>
      expect(screen.getByText(/Price for a 15% gross margin/)).toBeInTheDocument(),
    )

    await userEvent.clear(costField())
    await userEvent.type(costField(), '100')

    expect(suggestedPriceField()).toHaveValue('117.65')
  })

  it('applies a changed saved margin on the next form load', async () => {
    let margin = 15
    routeApi(
      baseHandlers(() => ({ default_target_margin_percent: margin })),
    )

    const first = renderCreateForm()
    await waitFor(() =>
      expect(screen.getByText(/Price for a 15% gross margin/)).toBeInTheDocument(),
    )
    await userEvent.clear(costField())
    await userEvent.type(costField(), '100')
    expect(suggestedPriceField()).toHaveValue('117.65')
    first.unmount()

    margin = 25
    renderCreateForm()
    await waitFor(() =>
      expect(screen.getByText(/Price for a 25% gross margin/)).toBeInTheDocument(),
    )
    await userEvent.clear(costField())
    await userEvent.type(costField(), '100')
    expect(suggestedPriceField()).toHaveValue('133.33')
  })

  it('falls back to 15% when the settings request fails', async () => {
    routeApi(
      baseHandlers(() => {
        throw new Error('settings unavailable')
      }),
    )
    renderCreateForm()

    await waitFor(() =>
      expect(screen.getByText(/Price for a 15% gross margin/)).toBeInTheDocument(),
    )

    await userEvent.clear(costField())
    await userEvent.type(costField(), '100')
    expect(suggestedPriceField()).toHaveValue('117.65')
  })

  it('falls back to 15% when the configured margin is invalid', async () => {
    routeApi(baseHandlers(() => ({ default_target_margin_percent: 'oops' })))
    renderCreateForm()

    await waitFor(() =>
      expect(screen.getByText(/Price for a 15% gross margin/)).toBeInTheDocument(),
    )

    await userEvent.clear(costField())
    await userEvent.type(costField(), '100')
    expect(suggestedPriceField()).toHaveValue('117.65')
  })

  it('falls back to 15% instead of showing Infinity for a saved 100% margin', async () => {
    // 100 is a storable Settings value, but a 100% gross margin divides by
    // zero. The read-only field must show a price or nothing, never Infinity.
    routeApi(baseHandlers(() => ({ default_target_margin_percent: 100 })))
    renderCreateForm()

    await waitFor(() =>
      expect(screen.getByText(/Price for a 15% gross margin/)).toBeInTheDocument(),
    )

    await userEvent.clear(costField())
    await userEvent.type(costField(), '100')
    expect(suggestedPriceField()).toHaveValue('117.65')
    expect(suggestedPriceField()).not.toHaveValue('Infinity')
  })

  it('leaves the suggestion empty when the cost is cleared', async () => {
    routeApi(baseHandlers(() => ({ default_target_margin_percent: 15 })))
    renderCreateForm()

    await waitFor(() =>
      expect(screen.getByText(/Price for a 15% gross margin/)).toBeInTheDocument(),
    )

    await userEvent.clear(costField())
    expect(suggestedPriceField()).toHaveValue('')
  })
})
