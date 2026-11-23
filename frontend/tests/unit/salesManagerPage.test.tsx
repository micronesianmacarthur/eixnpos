// @vitest-environment jsdom
/**
 * Component tests for SalesManagerPage (frontend/src/SalesManagerPage.tsx).
 *
 * The network layer (`apiFetch`) and the auth context are mocked, so these
 * tests are fully deterministic and make no real requests. Where the page
 * offers the `initialSales` test seam, we seed through it to skip the fetch
 * effect entirely; the fetch path itself is exercised via the mocked
 * `apiFetch` (loading / error / success states).
 */
import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import '@testing-library/jest-dom/vitest'

import { ApiError, apiFetch } from '../../src/api'
import SalesManagerPage, { type Sale } from '../../src/SalesManagerPage'

vi.mock('../../src/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../src/api')>()
  return { ...actual, apiFetch: vi.fn() }
})

// Auth is provided via context in the app; for component tests we stub the
// hook and switch roles through this mutable holder.
const currentRole = vi.hoisted(() => ({ value: 'admin' as string }))
vi.mock('../../src/authContext', () => ({
  useAuth: () => ({
    me: { user: { role: currentRole.value, username: 'maya' } },
    setMe: () => {},
  }),
}))

const mockedFetch = vi.mocked(apiFetch)

function sale(overrides: Partial<Sale> = {}): Sale {
  return {
    id: 1,
    number: 1001,
    cashier: 1,
    cashier_username: 'jane',
    customer: null,
    status: 'completed',
    subtotal: '50.00',
    discount_total: '0.00',
    total: '50.00',
    lines: [
      {
        id: 11,
        product: 1,
        product_name: 'Widget',
        quantity: 5,
        unit_price: '10.00',
        discount_type: 'none',
        discount_value: '0.00',
        discount_amount: '0.00',
        discount_authorizer: null,
        line_total: '50.00',
      },
    ],
    payments: [{ id: 21, method: 'cash', amount: '50.00', tendered: '60.00', change_due: '10.00', manager_approved: true, created_at: '2026-01-01T10:00:00Z' }],
    note: '',
    needs_review: false,
    created_at: '2026-01-01T10:00:00Z',
    completed_at: '2026-01-01T10:05:00Z',
    ...overrides,
  }
}

const SEED: Sale[] = [
  sale(),
  sale({
    id: 2,
    number: 1002,
    cashier_username: 'kofi',
    status: 'open',
    total: '30.00',
    created_at: '2026-01-02T09:00:00Z',
  }),
]

function renderPage(initialSales?: Sale[]) {
  return render(
    <MemoryRouter initialEntries={['/sales-manager']}>
      <SalesManagerPage initialSales={initialSales} />
    </MemoryRouter>,
  )
}

beforeEach(() => {
  currentRole.value = 'admin'
  mockedFetch.mockReset()
})

afterEach(() => {
  cleanup()
})

describe('SalesManagerPage — data fetching states', () => {
  it('shows the loading state while GET /api/sales/ is in flight', async () => {
    mockedFetch
      .mockReturnValueOnce(new Promise(() => {})) // sales never resolve
      .mockResolvedValue([]) // cashiers
    renderPage()
    expect(screen.getByRole('status')).toHaveTextContent('Loading sales…')
  })

  it('renders the fetched list after the request resolves', async () => {
    mockedFetch.mockResolvedValueOnce(SEED).mockResolvedValueOnce([])
    renderPage()
    expect(await screen.findByText('1001')).toBeInTheDocument()
    expect(screen.getByText('1002')).toBeInTheDocument()
    // KPI cards derived from the list.
    expect(await screen.findByTestId('kpi-open-sales')).toHaveTextContent('1')
    expect(mockedFetch).toHaveBeenCalledWith('/api/sales/')
  })

  it('normalizes a paginated { results } payload', async () => {
    mockedFetch.mockResolvedValueOnce({ results: [sale()] }).mockResolvedValueOnce([])
    renderPage()
    expect(await screen.findByText('1001')).toBeInTheDocument()
  })

  it('shows the API error message when the request fails', async () => {
    mockedFetch.mockRejectedValueOnce(new ApiError('Server error', 500))
    renderPage()
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('Failed to load sales')
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
  })
})

describe('SalesManagerPage — rendered states (seeded, no network)', () => {
  it('renders rows with status badge, cashier, totals, payment, and actions', () => {
    renderPage(SEED)
    const completedRow = screen.getByText('1001').closest('tr')!
    expect(within(completedRow).getByText('Completed')).toBeInTheDocument()
    expect(within(completedRow).getByText('jane')).toBeInTheDocument()
    expect(within(completedRow).getByText('Walk-in')).toBeInTheDocument()
    expect(within(completedRow).getAllByText('$50.00').length).toBeGreaterThan(0)
    expect(within(completedRow).getByTestId('action-refund')).toBeInTheDocument()

    const openRow = screen.getByText('1002').closest('tr')!
    expect(within(openRow).getByText('Open')).toBeInTheDocument()
    expect(within(openRow).getByTestId('action-void')).toBeInTheDocument()
    expect(within(openRow).getByTestId('action-reassign')).toBeInTheDocument()
  })

  it('shows the empty state for an empty list', () => {
    renderPage([])
    expect(screen.getByText('No sales yet')).toBeInTheDocument()
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
  })

  it('shows the no-match empty state when filters exclude everything', async () => {
    const user = userEvent.setup()
    renderPage(SEED)
    await user.click(screen.getByRole('button', { name: 'Refunded' }))
    expect(screen.getByText('No sales match')).toBeInTheDocument()
  })
})

describe('SalesManagerPage — filters', () => {
  it('filters rows by cashier select', async () => {
    const user = userEvent.setup()
    // Fetch path: sales + cashiers both resolved via the mocked apiFetch.
    mockedFetch
      .mockResolvedValueOnce(SEED)
      .mockResolvedValueOnce([
        { id: 1, username: 'jane', role: 'cashier' },
        { id: 2, username: 'kofi', role: 'cashier' },
      ])
    await renderPage() // no seeded prop → runs the fetch effect
    await screen.findByText('1001')

    await user.selectOptions(screen.getByLabelText('Filter by cashier'), 'kofi')
    expect(screen.queryByText('1001')).not.toBeInTheDocument()
    expect(screen.getByText('1002')).toBeInTheDocument()
  })

  it('shows KPI cards and clicking Open Sales applies the status filter', async () => {
    const user = userEvent.setup()
    renderPage(SEED)
    await user.click(screen.getByTestId('kpi-open-sales'))
    expect(screen.queryByText('1001')).not.toBeInTheDocument()
    expect(screen.getByText('1002')).toBeInTheDocument()
  })
})

describe('SalesManagerPage — role gating', () => {
  it('shows the permission warning for cashiers and hides the table', () => {
    currentRole.value = 'cashier'
    renderPage(SEED)
    expect(
      screen.getByText("You don't have permission to manage sales. Contact a manager or admin."),
    ).toBeInTheDocument()
    expect(screen.queryByTestId('sales-table')).not.toBeInTheDocument()
  })
})

describe('SalesManagerPage — manager actions', () => {
  it('voids an open sale through the confirmation modal', async () => {
    const user = userEvent.setup()
    const updated = sale({ id: 2, number: 1002, status: 'voided', total: '30.00' })
    mockedFetch.mockResolvedValue(updated)
    renderPage([SEED[1]])

    await user.click(screen.getByTestId('action-void'))
    const confirm = screen.getByRole('alertdialog')
    expect(confirm).toHaveTextContent('Void sale #1002?')
    await user.click(within(confirm).getByTestId('action-confirm'))

    await waitFor(() =>
      expect(mockedFetch).toHaveBeenCalledWith('/api/sales/2/void/', {
        method: 'POST',
      }),
    )
    // The sale #1002 row now shows the Voided badge (chip is separate).
    const row = screen.getByText('1002').closest('tr')!
    await waitFor(() =>
      expect(within(row).getAllByText('Voided').length).toBeGreaterThan(0),
    )
  })

  it('refunds a completed sale only when a reason is provided', async () => {
    const user = userEvent.setup()
    const updated = sale({ status: 'refunded' })
    mockedFetch.mockResolvedValue(updated)
    renderPage([SEED[0]])

    await user.click(screen.getByTestId('action-refund'))
    const confirm = screen.getByRole('alertdialog')

    // No reason yet — clicking confirm must not call the API.
    await user.click(within(confirm).getByTestId('action-confirm'))
    expect(await within(confirm).findByRole('alert')).toHaveTextContent(
      'reason is required',
    )
    expect(mockedFetch).not.toHaveBeenCalled()

    await user.type(within(confirm).getByLabelText(/reason/i), 'Customer changed mind')
    await user.click(within(confirm).getByTestId('action-confirm'))
    await waitFor(() =>
      expect(mockedFetch).toHaveBeenCalledWith('/api/sales/1/refund/', {
        method: 'POST',
        body: JSON.stringify({ reason: 'Customer changed mind' }),
      }),
    )
    const row = screen.getByText('1001').closest('tr')!
    await waitFor(() =>
      expect(within(row).getAllByText('Refunded').length).toBeGreaterThan(0),
    )
  })

  it('opens the sale detail drawer on row click', async () => {
    const user = userEvent.setup()
    renderPage(SEED)
    await user.click(screen.getByText('1001'))
    const drawer = screen.getByTestId('sale-drawer')
    expect(within(drawer).getByText('Widget')).toBeInTheDocument()
    expect(within(drawer).getByText('cash')).toBeInTheDocument()
  })
})
