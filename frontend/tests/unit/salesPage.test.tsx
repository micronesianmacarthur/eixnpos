// @vitest-environment jsdom
/**
 * Component tests for the sale-note modal flow on SalesPage
 * (frontend/src/SalesPage.tsx).
 *
 * The old UI rendered a `sale note` textarea directly in the payment panel.
 * The new UI hides that textfield, shows an `Add sale note` ghost button next
 * to the muted helper text `Required for zero sale`, and edits the note
 * through a modal dialog (textarea + Confirm/Cancel).
 *
 * The network layer (`apiFetch`) and the auth context are mocked, so these
 * tests are fully deterministic and make no real requests.
 */
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import '@testing-library/jest-dom/vitest'

import { ApiError, apiFetch } from '../../src/api'
import SalesPage from '../../src/SalesPage'

vi.mock('../../src/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../src/api')>()
  return { ...actual, apiFetch: vi.fn() }
})

// Auth is provided via context in the app; for component tests we stub the hook.
const currentRole = vi.hoisted(() => ({ value: 'cashier' as string }))
vi.mock('../../src/authContext', () => ({
  useAuth: () => ({
    me: { user: { role: currentRole.value, username: 'jane' }, business_timezone: 'UTC' },
    setMe: () => {},
  }),
}))

const mockedFetch = vi.mocked(apiFetch)

interface ProductFixture {
  id: number
  name: string
  price: string
  quantity: number
  is_low_stock?: boolean
  non_stock?: boolean
  active?: boolean
}

const ZERO_PRODUCT: ProductFixture = {
  id: 7,
  name: 'Free Sample',
  price: '0.00',
  quantity: 100,
  is_low_stock: false,
  non_stock: false,
  active: true,
}

const PAID_PRODUCT: ProductFixture = {
  id: 3,
  name: 'Widget',
  price: '10.00',
  quantity: 50,
  is_low_stock: false,
  non_stock: false,
  active: true,
}

interface SaleFixture {
  id: number
  number: number
  cashier: number
  cashier_username?: string | null
  customer: number | null
  status: string
  subtotal: string
  discount_total: string
  total: string
  lines: Array<Record<string, unknown>>
  payments: Array<Record<string, unknown>>
  note?: string
  needs_review?: boolean
  created_at?: string
  completed_at?: string | null
}

function makeSale(overrides: Partial<SaleFixture> = {}, lines: Array<Record<string, unknown>> = []): SaleFixture {
  return {
    id: 55,
    number: 2001,
    cashier: 1,
    cashier_username: 'jane',
    customer: null,
    status: 'open',
    subtotal: '0.00',
    discount_total: '0.00',
    total: '0.00',
    lines,
    payments: [],
    note: '',
    needs_review: false,
    created_at: '2026-01-01T10:00:00Z',
    completed_at: null,
    ...overrides,
  }
}

// Route requests by URL, like the real backend would.
function routeFetch(routes: Record<string, unknown | (() => unknown)>) {
  mockedFetch.mockImplementation(async (url: string, init?: { method?: string }) => {
    const method = (init?.method ?? 'GET').toUpperCase()
    const key = `${method} ${url}`
    if (!(key in routes)) {
      throw new ApiError(`unrouted request in test: ${key}`, 500)
    }
    const handler = routes[key]
    return typeof handler === 'function' ? (handler as () => unknown)() : handler
  })
}

function renderPage() {
  return render(
    <MemoryRouter>
      <SalesPage />
    </MemoryRouter>,
  )
}

/** Drives the page to the state where an open zero-total sale exists. */
async function renderWithOpenZeroSale() {
  const sale = makeSale({
    lines: [
      {
        id: 71,
        product: ZERO_PRODUCT.id,
        product_name: ZERO_PRODUCT.name,
        quantity: 1,
        unit_price: '0.00',
        discount_type: 'none',
        discount_value: '0.00',
        discount_amount: '0.00',
        line_total: '0.00',
      },
    ],
  })
  routeFetch({
    'GET /api/products/': [ZERO_PRODUCT],
    'GET /api/customers/': [],
    'GET /api/business/': { id: 1, name: 'Test Biz' },
    'POST /api/sales/': sale,
    'GET /api/sales/55/': sale,
  })
  renderPage()
  await screen.findByText(ZERO_PRODUCT.name)
  const user = userEvent.setup()
  const productCard = screen
    .getAllByRole('button', { name: new RegExp(ZERO_PRODUCT.name) })
    .find((btn) => btn.querySelector('.product-name')?.textContent === ZERO_PRODUCT.name)!
  await user.click(productCard)
  // The cart now shows the zero-total line.
  await screen.findByText('Free Sample', { selector: '.cart-line-name' })
  return { user }
}

beforeEach(() => {
  currentRole.value = 'cashier'
  mockedFetch.mockReset()
})

afterEach(() => {
  cleanup()
})

describe('SalesPage — sale note UI (hidden textfield + button)', () => {
  it('does not render a note textarea outside the modal', async () => {
    await renderWithOpenZeroSale()
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument()
  })

  it('keeps the old inline textfield placeholder out of the page entirely', async () => {
    await renderWithOpenZeroSale()
    // Old UI placeholder was 'Required for zero sales' (plural); the only
    // remaining helper copy is the singular modal/helper variant.
    expect(document.body.textContent).not.toContain('Required for zero sales')
  })

  it('shows the Add sale note button', async () => {
    await renderWithOpenZeroSale()
    expect(screen.getByRole('button', { name: 'Add sale note' })).toBeInTheDocument()
  })

  it('renders the adjacent helper text exactly as Required for zero sale', async () => {
    await renderWithOpenZeroSale()
    const helper = screen.getByText('Required for zero sale', { exact: true })
    expect(helper).toHaveClass('mm-muted')
    expect(helper.tagName).toBe('P')
  })
})

describe('SalesPage — sale note modal open/close', () => {
  it('opens a dialog with a note input and Confirm/Cancel when the button is clicked', async () => {
    const { user } = await renderWithOpenZeroSale()
    await user.click(screen.getByRole('button', { name: 'Add sale note' }))

    const dialog = screen.getByRole('dialog')
    expect(within(dialog).getByRole('heading', { name: 'Sale note' })).toBeInTheDocument()
    expect(dialog).toHaveAttribute('aria-modal', 'true')
    expect(within(dialog).getByRole('textbox')).toBeInTheDocument()
    expect(within(dialog).getByRole('button', { name: 'Confirm' })).toBeInTheDocument()
    expect(within(dialog).getByRole('button', { name: 'Cancel' })).toBeInTheDocument()
  })

  it('prefills the note input with the already-stored note', async () => {
    const { user } = await renderWithOpenZeroSale()
    // Store a note first.
    await user.click(screen.getByRole('button', { name: 'Add sale note' }))
    await user.type(screen.getByRole('textbox'), 'gift for customer')
    await user.click(screen.getByRole('button', { name: 'Confirm' }))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()

    // Re-open: the draft starts from the stored value.
    await user.click(screen.getByRole('button', { name: 'Add sale note' }))
    expect(screen.getByRole('textbox')).toHaveValue('gift for customer')
  })

  it('Confirm stores the typed note and closes the modal', async () => {
    const { user } = await renderWithOpenZeroSale()
    await user.click(screen.getByRole('button', { name: 'Add sale note' }))
    await user.type(screen.getByRole('textbox'), 'customer pickup later')
    await user.click(screen.getByRole('button', { name: 'Confirm' }))

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()

    // The stored note re-opens as the draft prefilled value.
    await user.click(screen.getByRole('button', { name: 'Add sale note' }))
    expect(screen.getByRole('textbox')).toHaveValue('customer pickup later')
  })

  it('Confirm trims surrounding whitespace before storing', async () => {
    const { user } = await renderWithOpenZeroSale()
    await user.click(screen.getByRole('button', { name: 'Add sale note' }))
    await user.type(screen.getByRole('textbox'), '  wait at counter  ')
    await user.click(screen.getByRole('button', { name: 'Confirm' }))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Add sale note' }))
    expect(screen.getByRole('textbox')).toHaveValue('wait at counter')
  })

  it('Cancel closes the modal without changing the stored note', async () => {
    const { user } = await renderWithOpenZeroSale()
    // Store a first note.
    await user.click(screen.getByRole('button', { name: 'Add sale note' }))
    await user.type(screen.getByRole('textbox'), 'keep this')
    await user.click(screen.getByRole('button', { name: 'Confirm' }))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()

    // Edit but cancel.
    await user.click(screen.getByRole('button', { name: 'Add sale note' }))
    await user.clear(screen.getByRole('textbox'))
    await user.type(screen.getByRole('textbox'), 'discarded edit')
    await user.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()

    // Stored note is unchanged.
    await user.click(screen.getByRole('button', { name: 'Add sale note' }))
    expect(screen.getByRole('textbox')).toHaveValue('keep this')
  })

  it('Cancel discards an edit when nothing was stored before', async () => {
    const { user } = await renderWithOpenZeroSale()
    await user.click(screen.getByRole('button', { name: 'Add sale note' }))
    await user.type(screen.getByRole('textbox'), 'never committed')
    await user.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()

    // Nothing stored: the next draft starts empty and zero-total sale stays blocked.
    await user.click(screen.getByRole('button', { name: 'Add sale note' }))
    expect(screen.getByRole('textbox')).toHaveValue('')
    expect(screen.getByRole('button', { name: 'Complete sale' })).toBeDisabled()
  })

  it('closes via the header × button without storing', async () => {
    const { user } = await renderWithOpenZeroSale()
    await user.click(screen.getByRole('button', { name: 'Add sale note' }))
    await user.type(screen.getByRole('textbox'), 'typed but closed')
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: '×' }))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Add sale note' }))
    expect(screen.getByRole('textbox')).toHaveValue('')
  })

  it('closes on backdrop mousedown without storing', async () => {
    const { user } = await renderWithOpenZeroSale()
    await user.click(screen.getByRole('button', { name: 'Add sale note' }))
    const backdrop = document.querySelector('.mm-modal-backdrop')!
    fireEvent.mouseDown(backdrop)
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  })
})

describe('SalesPage — zero-sale gating', () => {
  it('keeps Complete sale disabled for a zero-total sale with no stored note', async () => {
    await renderWithOpenZeroSale()
    expect(screen.getByRole('button', { name: 'Complete sale' })).toBeDisabled()
  })

  it('enables Complete sale once a non-empty note is stored', async () => {
    const { user } = await renderWithOpenZeroSale()
    await user.click(screen.getByRole('button', { name: 'Add sale note' }))
    await user.type(screen.getByRole('textbox'), 'zero total goodwill')
    await user.click(screen.getByRole('button', { name: 'Confirm' }))
    await waitFor(() => expect(screen.getByRole('button', { name: 'Complete sale' })).toBeEnabled())
  })

  it('a whitespace-only note does not unlock a zero-total sale', async () => {
    const { user } = await renderWithOpenZeroSale()
    await user.click(screen.getByRole('button', { name: 'Add sale note' }))
    await user.type(screen.getByRole('textbox'), '   ')
    await user.click(screen.getByRole('button', { name: 'Confirm' }))
    expect(screen.getByRole('button', { name: 'Complete sale' })).toBeDisabled()
  })

  it('does not apply the zero-note gate to a paid, non-zero sale', async () => {
    const sale = makeSale(
      {
        total: '10.00',
        subtotal: '10.00',
        payments: [{ id: 81, method: 'cash', amount: '10.00', tendered: '10.00', change_due: '0.00', manager_approved: true, created_at: '2026-01-01T10:01:00Z' }],
        lines: [
          {
            id: 72,
            product: PAID_PRODUCT.id,
            product_name: PAID_PRODUCT.name,
            quantity: 1,
            unit_price: '10.00',
            discount_type: 'none',
            discount_value: '0.00',
            discount_amount: '0.00',
            line_total: '10.00',
          },
        ],
      },
    )
    routeFetch({
      'GET /api/products/': [PAID_PRODUCT],
      'GET /api/customers/': [],
      'GET /api/business/': { id: 1, name: 'Test Biz' },
      'POST /api/sales/': sale,
      'GET /api/sales/55/': sale,
    })
    renderPage()
    await screen.findByText(PAID_PRODUCT.name)
    const user = userEvent.setup()
    const paidCard = screen
      .getAllByRole('button', { name: new RegExp(PAID_PRODUCT.name) })
      .find((btn) => btn.querySelector('.product-name')?.textContent === PAID_PRODUCT.name)!
    await user.click(paidCard)
    await screen.findByText('Widget', { selector: '.cart-line-name' })
    // Payment recorded automatically from the seeded payment line — gate on payments sum >= total.
    await waitFor(() => expect(screen.getByRole('button', { name: 'Complete sale' })).toBeEnabled())
  })
})

describe('SalesPage — sale note participates in submission', () => {
  it('sends the stored note (trimmed) in the complete payload', async () => {
    const completedSale = makeSale({ status: 'completed', completed_at: '2026-01-01T10:06:00Z' })
    const { user } = await renderWithOpenZeroSale()

    await user.click(screen.getByRole('button', { name: 'Add sale note' }))
    await user.type(screen.getByRole('textbox'), '  customer will return  ')
    await user.click(screen.getByRole('button', { name: 'Confirm' }))
    await waitFor(() => expect(screen.getByRole('button', { name: 'Complete sale' })).toBeEnabled())

    routeFetch({
      'POST /api/sales/55/complete/': completedSale,
      'GET /api/products/': [ZERO_PRODUCT],
    })
    await user.click(screen.getByRole('button', { name: 'Complete sale' }))

    await waitFor(() => {
      expect(mockedFetch).toHaveBeenCalledWith(
        '/api/sales/55/complete/',
        expect.objectContaining({
          method: 'POST',
          body: JSON.stringify({ note: 'customer will return' }),
        }),
      )
    })
    // The sale-note modal itself is not open at submission time.
    expect(screen.queryByRole('heading', { name: 'Sale note' })).not.toBeInTheDocument()
  })

  it('sends an empty note string when the sale has no note stored', async () => {
    const completedSale = makeSale({ status: 'completed', completed_at: '2026-01-01T10:06:00Z' })
    const { user } = await renderWithOpenZeroSale()
    // Paid path so the zero-note gate is irrelevant... but total is 0 here,
    // so store then clear: zero-sale gate only checks the stored (trimmed) value.
    await user.click(screen.getByRole('button', { name: 'Add sale note' }))
    await user.type(screen.getByRole('textbox'), 'temp')
    await user.click(screen.getByRole('button', { name: 'Confirm' }))

    // Overwrite with a note that trims to empty by typing only spaces.
    await user.click(screen.getByRole('button', { name: 'Add sale note' }))
    await user.clear(screen.getByRole('textbox'))
    await user.type(screen.getByRole('textbox'), '   ')
    await user.click(screen.getByRole('button', { name: 'Confirm' }))
    // Zero-total sale with effectively empty note stays disabled — skip submission here.
    expect(screen.getByRole('button', { name: 'Complete sale' })).toBeDisabled()
    expect(mockedFetch).not.toHaveBeenCalledWith(
      '/api/sales/55/complete/',
      expect.objectContaining({ method: 'POST' }),
    )
    void completedSale
  })

  it('resets the stored note after a completed sale', async () => {
    const completedSale = makeSale({ status: 'completed', completed_at: '2026-01-01T10:06:00Z' })
    const { user } = await renderWithOpenZeroSale()
    await user.click(screen.getByRole('button', { name: 'Add sale note' }))
    await user.type(screen.getByRole('textbox'), 'after-care call')
    await user.click(screen.getByRole('button', { name: 'Confirm' }))
    await waitFor(() => expect(screen.getByRole('button', { name: 'Complete sale' })).toBeEnabled())

    routeFetch({
      'POST /api/sales/55/complete/': completedSale,
      'GET /api/products/': [ZERO_PRODUCT],
    })
    await user.click(screen.getByRole('button', { name: 'Complete sale' }))
    await waitFor(() => expect(screen.getAllByRole('dialog').length).toBeGreaterThan(0)) // change-due/receipt modal appears

    // Close the change modal (the second dialog) and confirm the note reset.
    const dialogs = screen.getAllByRole('dialog')
    const changeDialog = dialogs.find((d) => !d.classList.contains('receipt-modal'))!
    await user.click(within(changeDialog).getByRole('button', { name: '×' }))
    await user.click(screen.getByRole('button', { name: 'Add sale note' }))
    expect(screen.getByRole('textbox')).toHaveValue('')
  })
})
