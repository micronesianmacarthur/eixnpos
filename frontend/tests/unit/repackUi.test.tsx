// @vitest-environment jsdom
/**
 * Behavioural UI tests for the repack acceptance criteria (AC-42, AC-43,
 * AC-45, AC-46, AC-47) that were previously verified only by code inspection.
 *
 * Rendering pages under different roles pins the authorisation surface:
 *  - AC-42: the Repack action is available to cashier/manager/admin on stock
 *    products and disabled only for non-stock products (InventoryPage).
 *  - AC-43: a successful repack closes the dialog, shows the returned
 *    reference, and refreshes products + repack history.
 *  - AC-45: the product movement table renders `Repack out` / `Repack in`
 *    badges, the shared reference and the generated note, and has an
 *    explicit empty state (ProductPage).
 *  - AC-46: the repack history card is strictly read-only — it shows date,
 *    reference, direction, source/target SKU × qty, operator and location,
 *    with no edit or delete controls.
 *  - AC-47: pack-mapping controls on ProductPage are hidden from cashiers
 *    and editable for managers.
 *
 * `apiFetch` and the auth context are mocked; role switching goes through a
 * mutable holder. No network, no wall-clock dependence.
 */
import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import '@testing-library/jest-dom/vitest'
import { MemoryRouter, Route, Routes } from 'react-router-dom'

import { apiFetch } from '../../src/api'
import InventoryPage from '../../src/InventoryPage'
import ProductPage from '../../src/ProductPage'

vi.mock('../../src/api', () => ({ apiFetch: vi.fn() }))
const mockedFetch = vi.mocked(apiFetch)

const currentRole = vi.hoisted(() => ({ value: 'admin' as string }))
vi.mock('../../src/authContext', () => ({
  useAuth: () => ({
    me: {
      user: { role: currentRole.value, username: 'maya' },
      business_info_complete: true,
    },
    setMe: () => {},
    loading: false,
    refresh: vi.fn(),
  }),
}))

type Handler = (url: string, init?: RequestInit) => unknown | Promise<unknown>

/** Install an apiFetch stub that dispatches on the request URL. */
function routeApi(handlers: Record<string, Handler>) {
  mockedFetch.mockImplementation(async (url: string, init?: RequestInit) => {
    const match = Object.keys(handlers).find((k) => url.startsWith(k))
    if (!match) throw new Error(`Unexpected apiFetch in test: ${url}`)
    return handlers[match](url, init)
  })
}

const STOCK = {
  id: 7,
  name: 'Six pack',
  sku: 'PK-6',
  description: '',
  price: '12.00',
  cost: '6.00',
  min_stock: 0,
  supplier: null,
  supplier_name: null,
  active: true,
  created_at: '2026-11-01T09:00:00+11:00',
  updated_at: '2026-11-01T09:00:00+11:00',
  quantity: 5,
  is_low_stock: false,
  non_stock: false,
  category_name: null,
  unit_product: 9,
  units_per_pack: 6,
  unit_product_sku: 'UNIT-1',
  unit_product_name: 'Single bottle',
  pack_parents: [],
}

const NON_STOCK = {
  ...STOCK,
  id: 8,
  name: 'Delivery fee',
  sku: 'SVC-1',
  quantity: 0,
  non_stock: true,
  unit_product: null,
  units_per_pack: null,
}

const UNIT = {
  ...STOCK,
  id: 9,
  name: 'Single bottle',
  sku: 'UNIT-1',
  quantity: 30,
  unit_product: null,
  units_per_pack: null,
  pack_parents: [{ id: 7, sku: 'PK-6', name: 'Six pack', units_per_pack: 6 }],
}

const REPACK_ROW = {
  id: 3,
  reference: 'RP-2026-0007',
  direction: 'split' as const,
  location: 'Main',
  source_sku: 'PK-6',
  source_quantity: 2,
  target_sku: 'UNIT-1',
  target_quantity: 12,
  performed_by_name: 'maya',
  created_at: '2026-11-24T14:05:00+11:00',
}

beforeEach(() => {
  mockedFetch.mockReset()
})

afterEach(() => {
  cleanup()
  currentRole.value = 'admin'
})

function renderInventory() {
  return render(
    <MemoryRouter>
      <InventoryPage />
    </MemoryRouter>,
  )
}

function inventoryHandlers(overrides: {
  repacks?: unknown[]
  postRepack?: Handler
} = {}) {
  return {
    '/api/products/': () => [STOCK, NON_STOCK, UNIT],
    '/api/inventory/repacks/': () => overrides.repacks ?? [REPACK_ROW],
    '/api/inventory/repack/': overrides.postRepack ?? (() => { throw new Error('no POST expected') }),
  }
}

function renderProductPage() {
  return render(
    <MemoryRouter initialEntries={['/inventory/product/7']}>
      <Routes>
        <Route path="/inventory/product/:id" element={<ProductPage />} />
      </Routes>
    </MemoryRouter>,
  )
}

function productHandlers(movements: unknown[]) {
  return {
    '/api/products/7/': () => STOCK,
    '/api/products/': () => [STOCK, UNIT],
    '/api/suppliers/': () => [],
    '/api/categories/': () => [],
    '/api/inventory/?product=7': () => movements,
  }
}

describe('InventoryPage repack action (AC-42)', () => {
  it.each(['cashier', 'manager', 'admin'])(
    'shows an enabled Repack action on stock products for %s',
    async (role) => {
      currentRole.value = role
      routeApi(inventoryHandlers())
      renderInventory()

      await screen.findByText('Six pack')
      const stockButton = screen.getByRole('button', { name: 'Repack Six pack' })
      expect(stockButton).toBeEnabled()
    },
  )

  it('disables Repack only for non-stock products, for every role', async () => {
    for (const role of ['cashier', 'manager', 'admin']) {
      currentRole.value = role
      routeApi(inventoryHandlers())
      renderInventory()

      await screen.findByText('Six pack')
      expect(screen.getByRole('button', { name: 'Repack Six pack' })).toBeEnabled()
      expect(
        screen.getByRole('button', { name: 'Repack Delivery fee' }),
      ).toBeDisabled()
      cleanup()
    }
  })
})

describe('InventoryPage repack history card (AC-46)', () => {
  it('renders date, reference, direction, source/target SKU × qty, operator and location', async () => {
    currentRole.value = 'cashier'
    routeApi(inventoryHandlers())
    renderInventory()

    const history = await screen.findByRole('region', { name: 'Repack history' })
    const table = within(history).getByRole('table')
    for (const header of [
      'Date',
      'Reference',
      'Direction',
      'From (SKU × qty)',
      'To (SKU × qty)',
      'Operator',
      'Location',
    ]) {
      expect(within(table).getByText(header)).toBeInTheDocument()
    }
    expect(within(table).getByText('RP-2026-0007')).toBeInTheDocument()
    expect(within(table).getByText('Split')).toBeInTheDocument()
    expect(within(table).getByText('PK-6 × 2')).toBeInTheDocument()
    expect(within(table).getByText('UNIT-1 × 12')).toBeInTheDocument()
    expect(within(table).getByText('maya')).toBeInTheDocument()
    expect(within(table).getByText('Main')).toBeInTheDocument()
    // The date cell is a formatted timestamp, not the raw ISO string.
    expect(within(table).queryByText(REPACK_ROW.created_at)).not.toBeInTheDocument()
    expect(
      within(table).getAllByText(/24\/11\/2026|11\/24\/2026|2026/).length,
    ).toBeGreaterThan(0)
  })

  it('has no edit or delete controls in the read-only history card', async () => {
    currentRole.value = 'admin'
    routeApi(inventoryHandlers())
    renderInventory()

    const history = await screen.findByRole('region', { name: 'Repack history' })
    // The only buttons on the page live in the product table / header; the
    // history section itself must contain none (no Edit/Delete/rows actions).
    expect(within(history).queryByRole('button')).not.toBeInTheDocument()
  })

  it('shows the empty state when no repacks exist', async () => {
    currentRole.value = 'cashier'
    routeApi(inventoryHandlers({ repacks: [] }))
    renderInventory()

    const history = await screen.findByRole('region', { name: 'Repack history' })
    expect(await within(history).findByText('No repacks recorded')).toBeInTheDocument()
    expect(
      within(history).getByText(/Record one with the Repack action/),
    ).toBeInTheDocument()
  })
})

describe('InventoryPage repack dialog success (AC-43)', () => {
  it('closes the dialog, shows the returned reference and refreshes both lists', async () => {
    currentRole.value = 'cashier'
    const user = userEvent.setup()
    let postCount = 0
    routeApi({
      ...inventoryHandlers(),
      '/api/inventory/repack/': (url, init) => {
        postCount += 1
        expect(String(url)).toContain('/api/inventory/repack/')
        expect(JSON.parse(String(init?.body))).toEqual({
          source_product: 7,
          target_product: 9,
          source_quantity: 2,
        })
        return { reference: 'RP-2026-0007' }
      },
    })
    renderInventory()

    await user.click(await screen.findByRole('button', { name: 'Repack Six pack' }))

    // Single pack parent → no target selector; quantity only.
    const dialog = screen.getByRole('dialog', { name: 'Repack stock' })
    const qty = await within(dialog).findByLabelText('Source quantity')
    await user.type(qty, '2')
    await user.click(within(dialog).getByRole('button', { name: 'Confirm repack' }))

    // Dialog closed.
    await waitFor(() =>
      expect(screen.queryByRole('dialog', { name: 'Repack stock' })).not.toBeInTheDocument(),
    )
    // Success notice carries the reference returned by the API.
    const notice = await screen.findByRole('status')
    expect(notice).toHaveTextContent('Repack recorded')
    expect(notice).toHaveTextContent('RP-2026-0007')

    // products + repack history were refetched after the POST.
    await waitFor(() => expect(postCount).toBe(1))
    const gets = mockedFetch.mock.calls.filter(
      ([url, init]) => !init?.method && typeof url === 'string' &&
        (url === '/api/products/' || url.startsWith('/api/inventory/repacks/')),
    )
    expect(gets.filter(([url]) => url === '/api/products/').length).toBeGreaterThanOrEqual(2)
    expect(
      gets.filter(([url]) => String(url).startsWith('/api/inventory/repacks/')).length,
    ).toBeGreaterThanOrEqual(2)
  })

  it('does not show the success notice before a repack succeeds', async () => {
    currentRole.value = 'cashier'
    routeApi(inventoryHandlers())
    renderInventory()
    await screen.findByText('Six pack')
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
  })
})

describe('ProductPage pack mapping visibility (AC-47)', () => {
  it('hides mapping fields from a cashier', async () => {
    currentRole.value = 'cashier'
    routeApi(productHandlers([]))
    renderProductPage()

    await screen.findByRole('heading', { name: 'Edit product' })
    await waitFor(() =>
      expect(screen.queryByRole('heading', { name: 'Pack mapping' })).not.toBeInTheDocument(),
    )
    expect(screen.queryByLabelText('Unit product')).not.toBeInTheDocument()
    expect(screen.queryByLabelText('Units per pack')).not.toBeInTheDocument()
    // Cashiers also cannot delete a product.
    expect(screen.queryByRole('button', { name: 'Delete product' })).not.toBeInTheDocument()
  })

  it('shows editable mapping fields to a manager', async () => {
    currentRole.value = 'manager'
    routeApi(productHandlers([]))
    renderProductPage()
    const user = userEvent.setup()

    expect(
      await screen.findByRole('heading', { name: 'Pack mapping' }),
    ).toBeInTheDocument()
    const unitSelect = screen.getByLabelText('Unit product')
    expect(unitSelect).toBeEnabled()
    const perPack = screen.getByLabelText('Units per pack')
    expect(perPack).toBeEnabled()
    // The fields are actually editable, not just rendered.
    await user.selectOptions(unitSelect, '9')
    expect(unitSelect).toHaveValue('9')
    await user.clear(perPack)
    await user.type(perPack, '6')
    expect(perPack).toHaveValue(6)
  })
})

describe('ProductPage stock movements table (AC-45)', () => {
  const MOVEMENTS = [
    {
      id: 11,
      movement_type: 'sale',
      quantity: -2,
      reference: 'INV-0004',
      note: 'Counter sale',
      created_at: '2026-11-20T10:00:00+11:00',
    },
    {
      id: 12,
      movement_type: 'repack_out',
      quantity: -2,
      reference: 'RP-2026-0007',
      note: 'Converted 2 × PK-6 into UNIT-1 (auto)',
      created_at: '2026-11-24T14:05:00+11:00',
    },
    {
      id: 13,
      movement_type: 'repack_in',
      quantity: 12,
      reference: 'RP-2026-0007',
      note: 'Converted 2 × PK-6 into UNIT-1 (auto)',
      created_at: '2026-11-24T14:05:01+11:00',
    },
  ]

  it('renders Repack out / Repack in labels, badges, shared reference and generated note', async () => {
    currentRole.value = 'manager'
    routeApi(productHandlers(MOVEMENTS))
    renderProductPage()

    const table = await screen.findByRole('table')
    expect(await within(table).findByText('Repack out')).toBeInTheDocument()
    expect(within(table).getByText('Repack in')).toBeInTheDocument()
    // The shared reference appears on both repack rows.
    const references = within(table).getAllByText('RP-2026-0007')
    expect(references).toHaveLength(2)
    // The generated note is shown on both rows.
    const notes = within(table).getAllByText('Converted 2 × PK-6 into UNIT-1 (auto)')
    expect(notes).toHaveLength(2)
    // Regular movement types still render.
    expect(within(table).getByText('Sale')).toBeInTheDocument()
  })

  it('uses distinct badge classes for the two repack directions', async () => {
    currentRole.value = 'manager'
    routeApi(productHandlers(MOVEMENTS))
    renderProductPage()

    const table = await screen.findByRole('table')
    const outBadge = await within(table).findByText('Repack out')
    const inBadge = within(table).getByText('Repack in')
    expect(outBadge.className).toContain('mm-badge--danger')
    expect(inBadge.className).toContain('mm-badge--success')
  })

  it('shows the empty-state copy when the product has no movements', async () => {
    currentRole.value = 'cashier'
    routeApi(productHandlers([]))
    renderProductPage()

    expect(await screen.findByText('No stock movements yet')).toBeInTheDocument()
    expect(
      screen.getByText(/repacks\n?\s*\(pack ↔ unit conversions\) will appear here/),
    ).toBeInTheDocument()
  })
})
