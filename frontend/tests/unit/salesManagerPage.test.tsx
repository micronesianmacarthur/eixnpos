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
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
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
    me: { user: { role: currentRole.value, username: 'maya' }, business_timezone: currentTimezone.value },
    setMe: () => {},
  }),
}))

// Mutable holder for the business IANA timezone returned by /api/auth/me/.
const currentTimezone = vi.hoisted(() => ({ value: 'UTC' }))

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
  currentTimezone.value = 'UTC'
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
    // The Lines tab is the default.
    expect(within(drawer).getByText('Widget')).toBeInTheDocument()
    // Payment detail lives on the Payments tab.
    await user.click(within(drawer).getByRole('tab', { name: 'Payments' }))
    expect(within(drawer).getByText('cash')).toBeInTheDocument()
  })
})

describe('SalesManagerPage — KPI derivation', () => {
  it('derives open count, today revenue, avg basket, and needs-review count', () => {
    const today = new Date().toISOString()
    const yesterday = new Date(Date.now() - 24 * 60 * 60 * 1000).toISOString()
    renderPage([
      sale({ id: 1, number: 1001, status: 'open', total: '30.00' }),
      sale({ id: 2, number: 1002, status: 'completed', total: '80.00', created_at: today }),
      sale({ id: 3, number: 1003, status: 'completed', total: '20.00', created_at: yesterday }),
      sale({ id: 4, number: 1004, status: 'completed', total: '50.00', created_at: today, needs_review: true }),
    ])
    expect(screen.getByTestId('kpi-open-sales')).toHaveTextContent('1')
    // Today's revenue counts only completed sales created today (80 + 50).
    expect(screen.getByTestId('kpi-revenue')).toHaveTextContent('$130.00')
    // Avg basket = 130 / 2 completed-today.
    expect(screen.getByTestId('kpi-avg-basket')).toHaveTextContent('$65.00')
    expect(screen.getByTestId('kpi-needs-review')).toHaveTextContent('1')
  })

  it('uses the business timezone for "today", not the browser clock (S5)', () => {
    // Freeze the business's clock at a UTC instant that is Jan 2 in a +14:00
    // shop while still Jan 1 in UTC. A sale created at 15:00Z is Jan 2 in
    // Kiritimati (business-today) but Jan 1 in UTC — so counting it proves the
    // business timezone, not the browser clock, drives isToday.
    vi.setSystemTime(new Date('2026-01-01T20:30:00Z'))
    currentTimezone.value = 'Pacific/Kiritimati' // +14:00
    renderPage([
      sale({ id: 1, number: 1001, status: 'completed', total: '40.00', created_at: '2026-01-01T15:00:00Z' }),
    ])
    expect(screen.getByTestId('kpi-revenue')).toHaveTextContent('$40.00')
    vi.useRealTimers()
  })

  it('clicking the Needs Review KPI applies the needs_review filter', async () => {
    const user = userEvent.setup()
    renderPage([
      sale({ id: 1, number: 1001, status: 'completed' }),
      sale({ id: 2, number: 1002, status: 'completed', needs_review: true }),
    ])
    await user.click(screen.getByTestId('kpi-needs-review'))
    expect(screen.queryByText('1001')).not.toBeInTheDocument()
    expect(screen.getByText('1002')).toBeInTheDocument()
  })

  it('Today’s Revenue drill-down scopes to today like the KPI card', async () => {
    const user = userEvent.setup()
    const today = new Date().toISOString()
    const yesterday = new Date(Date.now() - 24 * 60 * 60 * 1000).toISOString()
    renderPage([
      // A completed sale today and one yesterday — the card reads only today's.
      sale({ id: 1, number: 1001, status: 'completed', total: '50.00', created_at: today }),
      sale({ id: 2, number: 1002, status: 'completed', total: '100.00', created_at: yesterday }),
    ])
    // The KPI shows only today's revenue.
    expect(screen.getByTestId('kpi-revenue')).toHaveTextContent('$50.00')

    await user.click(screen.getByTestId('kpi-revenue'))
    // The drill-down table shows the same set as the card — not yesterday's.
    expect(screen.getByText('1001')).toBeInTheDocument()
    expect(screen.queryByText('1002')).not.toBeInTheDocument()
  })
})

describe('SalesManagerPage — status chips, dates, and URL persistence', () => {
  function renderWithLocation(initialSales: Sale[]) {
    function LocationProbe() {
      const location = useLocation()
      return <div data-testid="location-probe">{location.pathname + decodeURIComponent(location.search)}</div>
    }
    return render(
      <MemoryRouter initialEntries={['/sales-manager']}>
        <LocationProbe />
        <Routes>
          <Route path="/sales-manager" element={<SalesManagerPage initialSales={initialSales} />} />
        </Routes>
      </MemoryRouter>,
    )
  }

  it('toggles a status chip with aria-pressed and filters by status', async () => {
    const user = userEvent.setup()
    renderWithLocation(SEED)
    const group = screen.getByTestId('filter-status')
    const completedChip = within(group).getByRole('button', { name: 'Completed' })
    expect(completedChip).toHaveAttribute('aria-pressed', 'false')
    await user.click(completedChip)
    expect(completedChip).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByText('1001')).toBeInTheDocument()
    expect(screen.queryByText('1002')).not.toBeInTheDocument()
    expect(screen.getByTestId('location-probe')).toHaveTextContent('status=completed')

    // Toggling a second status widens the filter (comma-joined in the URL).
    await user.click(within(group).getByRole('button', { name: 'Open' }))
    expect(screen.getByText('1002')).toBeInTheDocument()
    expect(screen.getByTestId('location-probe')).toHaveTextContent('status=completed,open')
  })

  it('filters sales by the needs-review checkbox and reflects it in the URL', async () => {
    const user = userEvent.setup()
    renderWithLocation([
      sale({ id: 1, number: 1001 }),
      sale({ id: 2, number: 1002, status: 'completed', needs_review: true }),
    ])
    const checkbox = screen.getByTestId('filter-needs-review')
    await user.click(checkbox)
    expect(checkbox).toBeChecked()
    expect(screen.queryByText('1001')).not.toBeInTheDocument()
    expect(screen.getByText('1002')).toBeInTheDocument()
    expect(screen.getByTestId('location-probe')).toHaveTextContent('needs_review=true')
  })

  it('filters sales by the from-date range', async () => {
    const user = userEvent.setup()
    renderWithLocation([
      sale({ id: 1, number: 1001, created_at: '2026-01-01T10:00:00Z' }),
      sale({ id: 2, number: 1002, status: 'open', created_at: '2026-03-01T10:00:00Z' }),
    ])
    await user.type(screen.getByTestId('filter-date'), '2026-02-01')
    expect(screen.queryByText('1001')).not.toBeInTheDocument()
    expect(screen.getByText('1002')).toBeInTheDocument()
  })

  it('filters by payment method and reflects it in the URL', async () => {
    const user = userEvent.setup()
    const cardSale = sale({
      id: 2,
      number: 1002,
      status: 'open',
      payments: [
        { id: 22, method: 'card', amount: '30.00', tendered: '30.00', change_due: '0.00', manager_approved: false, created_at: '2026-01-02T09:00:00Z' },
      ],
    })
    renderWithLocation([sale(), cardSale])
    expect(screen.getByTestId('filter-payment')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Card' }))
    // Only the card sale remains; the (cash) sale is filtered out.
    expect(screen.getByText('1002')).toBeInTheDocument()
    expect(screen.queryByText('1001')).not.toBeInTheDocument()
    expect(screen.getByTestId('location-probe')).toHaveTextContent('payment=card')

    // Toggling cash alongside card widens the filter back to both.
    await user.click(screen.getByRole('button', { name: 'Cash' }))
    expect(screen.getByText('1001')).toBeInTheDocument()
    expect(screen.getByTestId('location-probe')).toHaveTextContent('payment=card,cash')
  })

  it('searches by cashier name and persists the query to the URL after debounce', async () => {
    const user = userEvent.setup()
    renderWithLocation(SEED)
    await user.type(screen.getByTestId('sales-search'), 'kofi')
    // Debounced: not applied instantly…
    expect(screen.getByTestId('location-probe')).not.toHaveTextContent('search=')
    // …but committed to the URL (and the rows) shortly after.
    await waitFor(
      () => expect(screen.getByTestId('location-probe')).toHaveTextContent('search=kofi'),
      { timeout: 1000 },
    )
    expect(screen.queryByText('1001')).not.toBeInTheDocument()
    expect(screen.getByText('1002')).toBeInTheDocument()
  })

  it('restores filters from the URL on load', () => {
    render(
      <MemoryRouter initialEntries={['/sales-manager?search=kofi&status=open']}>
        <Routes>
          <Route path="/sales-manager" element={<SalesManagerPage initialSales={SEED} />} />
        </Routes>
      </MemoryRouter>,
    )
    expect(screen.getByTestId('sales-search')).toHaveValue('kofi')
    expect(screen.queryByText('1001')).not.toBeInTheDocument()
    expect(screen.getByText('1002')).toBeInTheDocument()
  })

  it('Clear filters resets the URL and shows all rows', async () => {
    const user = userEvent.setup()
    renderWithLocation(SEED)
    await user.click(screen.getByRole('button', { name: 'Refunded' }))
    expect(screen.getByText('No sales match')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Clear filters' }))
    expect(screen.getByText('1001')).toBeInTheDocument()
    expect(screen.getByText('1002')).toBeInTheDocument()
    expect(screen.getByTestId('location-probe')).toHaveTextContent(/^\/sales-manager$/)
  })
})

describe('SalesManagerPage — role gating', () => {
  it('allows managers without showing the permission warning', () => {
    currentRole.value = 'manager'
    renderPage(SEED)
    expect(screen.queryByText(/You don't have permission to manage sales/)).not.toBeInTheDocument()
    expect(screen.getByTestId('sales-table')).toBeInTheDocument()
  })
})

describe('SalesManagerPage — bulk void', () => {
  it('voids each selected open sale and reports a toast', async () => {
    const user = userEvent.setup()
    const updated = (id: number) => sale({ id, number: 1000 + id, status: 'voided' })
    mockedFetch.mockImplementation(async (url: string) => {
      const match = /\/api\/sales\/(\d+)\/void\//.exec(url)
      return match ? updated(Number(match[1])) : []
    })
    renderPage([
      sale({ id: 2, number: 1002, status: 'open', total: '30.00' }),
      sale({ id: 3, number: 1003, status: 'open', total: '40.00' }),
      sale({ id: 4, number: 1004, status: 'completed', total: '50.00' }),
    ])

    await user.click(screen.getByRole('checkbox', { name: 'Select sale 1002' }))
    await user.click(screen.getByRole('checkbox', { name: 'Select sale 1003' }))
    const bulkbar = screen.getByRole('region', { name: 'Bulk actions' })
    expect(within(bulkbar).getByText('2 selected')).toBeInTheDocument()

    await user.click(screen.getByTestId('bulk-void'))
    await waitFor(() => expect(mockedFetch).toHaveBeenCalledTimes(2))
    expect(mockedFetch).toHaveBeenCalledWith('/api/sales/2/void/', { method: 'POST' })
    expect(mockedFetch).toHaveBeenCalledWith('/api/sales/3/void/', { method: 'POST' })
    // The completed sale was never voided.
    expect(mockedFetch).not.toHaveBeenCalledWith('/api/sales/4/void/', { method: 'POST' })
    expect(await screen.findByText('Sale #1002 voided.')).toBeInTheDocument()
    expect(screen.getByText('Sale #1003 voided.')).toBeInTheDocument()
  })
})

describe('SalesManagerPage — reassign', () => {
  const CASHIERS = [
    { id: 1, username: 'jane', role: 'cashier' },
    { id: 2, username: 'kofi', role: 'cashier' },
  ]

  it('requires a cashier selection before calling the API', async () => {
    const user = userEvent.setup()
    mockedFetch.mockResolvedValueOnce([SEED[1]]).mockResolvedValueOnce(CASHIERS)
    await renderPage() // fetch path populates the cashier dropdown
    await screen.findByText('1002')

    await user.click(screen.getByTestId('action-reassign'))
    const confirm = screen.getByRole('alertdialog')
    await user.click(within(confirm).getByTestId('action-confirm'))
    expect(await within(confirm).findByRole('alert')).toHaveTextContent(
      'Select a cashier to reassign this sale to.',
    )
    expect(mockedFetch).not.toHaveBeenCalledWith('/api/sales/2/', expect.anything())
  })

  it('reassigns by sending the cashier id when the backend accepts the PATCH', async () => {
    const user = userEvent.setup()
    // sale 1002 is currently kofi (cashier id 2); reassign it to jane (id 1)
    // and the backend reflects the change in the response.
    const reassigned = sale({
      id: 2,
      number: 1002,
      status: 'open',
      cashier: 1,
      cashier_username: 'jane',
    })
    mockedFetch
      .mockResolvedValueOnce([SEED[1]])
      .mockResolvedValueOnce(CASHIERS)
      .mockResolvedValueOnce(reassigned)
    await renderPage()
    await screen.findByText('1002')

    await user.click(screen.getByTestId('action-reassign'))
    const confirm = screen.getByRole('alertdialog')
    await user.selectOptions(screen.getByTestId('reassign-select'), 'jane')
    await user.click(within(confirm).getByTestId('action-confirm'))

    // Real contract: the client PATCHes the cashier PK, not a username.
    await waitFor(() =>
      expect(mockedFetch).toHaveBeenCalledWith('/api/sales/2/', {
        method: 'PATCH',
        body: JSON.stringify({ cashier: 1 }),
      }),
    )
    expect(await screen.findByText('Sale #1002 reassigned to jane.')).toBeInTheDocument()
    const row = screen.getByText('1002').closest('tr')!
    expect(within(row).getByText('jane')).toBeInTheDocument()
  })

  it('surfaces an error when the backend response did not change the cashier', async () => {
    const user = userEvent.setup()
    // Backend echoes the sale back with its original cashier (no change).
    mockedFetch
      .mockResolvedValueOnce([SEED[1]])
      .mockResolvedValueOnce(CASHIERS)
      .mockResolvedValueOnce(SEED[1])
    await renderPage()
    await screen.findByText('1002')

    await user.click(screen.getByTestId('action-reassign'))
    const confirm = screen.getByRole('alertdialog')
    await user.selectOptions(screen.getByTestId('reassign-select'), 'jane')
    await user.click(within(confirm).getByTestId('action-confirm'))

    await waitFor(() =>
      expect(mockedFetch).toHaveBeenCalledWith('/api/sales/2/', expect.anything()),
    )
    // The modal stays open and tells the truth instead of claiming success.
    expect(await within(confirm).findByRole('alert')).toHaveTextContent(
      "didn't change this sale's cashier",
    )
    // No success toast fired — the sale was NOT reassigned.
    expect(screen.queryByText(/reassigned to/)).not.toBeInTheDocument()
  })
})

describe('SalesManagerPage — discount override', () => {
  function openDrawerOnOpenSale() {
    renderPage([SEED[1]]) // sale 1002 is open → Discount button in drawer
  }

  it('rejects a negative discount value without calling the API', async () => {
    const user = userEvent.setup()
    openDrawerOnOpenSale()
    await user.click(screen.getByText('1002'))
    const drawer = screen.getByTestId('sale-drawer')
    await user.click(within(drawer).getByRole('button', { name: 'Discount' }))

    const confirm = screen.getByRole('alertdialog')
    expect(confirm).toHaveTextContent('Override discount — Widget')
    await user.type(within(confirm).getByLabelText('Value'), '-5')
    await user.click(within(confirm).getByTestId('action-confirm'))
    expect(await within(confirm).findByRole('alert')).toHaveTextContent(
      'Enter a valid discount value.',
    )
    expect(mockedFetch).not.toHaveBeenCalled()
  })

  it('applies a percent discount and reloads the sale detail', async () => {
    const user = userEvent.setup()
    const discountedLine = { ...SEED[1].lines[0], discount_amount: '5.00' }
    const discountedSale = { ...SEED[1], lines: [discountedLine] }
    mockedFetch.mockImplementation(async (url: string) => {
      if (url.endsWith('/lines/11/discount/')) return discountedLine
      if (url.endsWith('/api/sales/2/')) return discountedSale
      return []
    })
    openDrawerOnOpenSale()
    await user.click(screen.getByText('1002'))
    const drawer = screen.getByTestId('sale-drawer')
    await user.click(within(drawer).getByRole('button', { name: 'Discount' }))

    const confirm = screen.getByRole('alertdialog')
    await user.type(within(confirm).getByLabelText('Value'), '10')
    await user.click(within(confirm).getByTestId('action-confirm'))

    await waitFor(() =>
      expect(mockedFetch).toHaveBeenCalledWith('/api/sales/2/lines/11/discount/', {
        method: 'POST',
        body: JSON.stringify({ discount_type: 'percent', discount_value: '10' }),
      }),
    )
    // The drawer reloads the sale so the new line discount is visible.
    expect(mockedFetch).toHaveBeenCalledWith('/api/sales/2/')
    expect(await screen.findByText('Discount applied to Widget.')).toBeInTheDocument()
    expect(within(screen.getByTestId('sale-drawer')).getByText('-$5.00')).toBeInTheDocument()
  })
})

describe('SalesManagerPage — action failures and dismissal', () => {
  it('keeps the void modal open with the API error when voiding fails', async () => {
    const user = userEvent.setup()
    mockedFetch.mockRejectedValueOnce(new ApiError('Manager approval required', 403))
    renderPage([SEED[1]])

    await user.click(screen.getByTestId('action-void'))
    const confirm = screen.getByRole('alertdialog')
    await user.click(within(confirm).getByTestId('action-confirm'))
    expect(await within(confirm).findByRole('alert')).toHaveTextContent(
      'Manager approval required',
    )
    // Modal stays open; the row is untouched.
    expect(within(confirm).getByTestId('action-confirm')).toBeEnabled()
    const row = screen.getByText('1002').closest('tr')!
    expect(within(row).getAllByText('Open').length).toBeGreaterThan(0)
  })

  it('closes the drawer with Escape', async () => {
    const user = userEvent.setup()
    renderPage(SEED)
    await user.click(screen.getByText('1001'))
    expect(screen.getByTestId('sale-drawer')).toBeInTheDocument()
    await user.keyboard('{Escape}')
    expect(screen.queryByTestId('sale-drawer')).not.toBeInTheDocument()
  })
})

describe('SalesManagerPage — CSV export', () => {
  it('exports the visible sales and reports how many were exported', async () => {
    const user = userEvent.setup()
    const createObjectURL = vi.fn((_blob: Blob) => 'blob:mock')
    const revokeObjectURL = vi.fn()
    vi.stubGlobal('URL', { ...URL, createObjectURL, revokeObjectURL })
    try {
      renderPage(SEED)
      await user.click(screen.getByTestId('export-csv'))
      expect(await screen.findByText('Exported 2 sales to CSV.')).toBeInTheDocument()
      expect(createObjectURL).toHaveBeenCalledTimes(1)
      const blob = createObjectURL.mock.calls[0][0] as unknown as Blob
      expect(blob.type).toContain('text/csv')
    } finally {
      vi.unstubAllGlobals()
    }
  })

  it('exports only selected rows and neutralizes leading formula characters', async () => {
    const user = userEvent.setup()
    const createObjectURL = vi.fn((_blob: Blob) => 'blob:mock')
    const revokeObjectURL = vi.fn()
    vi.stubGlobal('URL', { ...URL, createObjectURL, revokeObjectURL })
    try {
      // A cashier username that begins with `=` is a formula-injection vector.
      const risky = sale({
        id: 2,
        number: 2002,
        status: 'completed',
        cashier: 7,
        cashier_username: '=SUM(A1:A2)',
      })
      renderPage([sale(), risky])
      // Select only the risky sale, so the export covers it alone.
      await user.click(screen.getByRole('checkbox', { name: 'Select sale 2002' }))
      await user.click(screen.getByTestId('export-csv'))
      expect(await screen.findByText('Exported 1 sale to CSV.')).toBeInTheDocument()

      const blob = createObjectURL.mock.calls[0][0] as unknown as Blob
      const csv = typeof blob.text === 'function' ? await blob.text() : ''
      // The selected sale is present; the unselected one is not.
      expect(csv).toContain('2002')
      expect(csv).not.toContain('1001')
      // The leading `=` is neutralized with a quoted `'` marker.
      expect(csv).toContain("'=SUM(A1:A2)")
    } finally {
      vi.unstubAllGlobals()
    }
  })
})

describe('SalesManagerPage — keyboard accessibility (AC-19)', () => {
  it('opens the sale drawer via keyboard and manages focus (place on open, restore on close)', async () => {
    const user = userEvent.setup()
    renderPage(SEED)

    // Each row exposes a focusable "View details" control; focus it and
    // activate it with the keyboard rather than the mouse.
    const trigger = screen.getByRole('button', { name: 'View details for sale 1001' })
    trigger.focus()
    await user.keyboard('{Enter}')

    const drawer = screen.getByTestId('sale-drawer')
    expect(within(drawer).getByText('Widget')).toBeInTheDocument()
    // The drawer is a modal dialog: focus moved to its heading on open.
    await waitFor(() =>
      expect(document.activeElement).toHaveAccessibleName(/Sale #1001/),
    )

    // Escape still closes it…
    await user.keyboard('{Escape}')
    await waitFor(() =>
      expect(screen.queryByTestId('sale-drawer')).not.toBeInTheDocument(),
    )
    // …and focus returns to the control that opened it.
    await waitFor(() => expect(document.activeElement).toBe(trigger))
  })
})

describe('SalesManagerPage — drawer tabs (AC-07)', () => {
  it('renders all four spec tabs and switches content between them', async () => {
    const user = userEvent.setup()
    renderPage(SEED)
    // 1001 is completed (not open), so the open-sale discount button is absent.
    await user.click(screen.getByRole('button', { name: 'View details for sale 1001' }))

    const drawer = screen.getByTestId('sale-drawer')
    const tab = (name: string) => within(drawer).getByRole('tab', { name })

    // Default tab is Lines.
    expect(tab('Lines')).toHaveAttribute('aria-selected', 'true')
    expect(within(drawer).getByText('Widget')).toBeInTheDocument()

    // Payments shows the payment record.
    await user.click(tab('Payments'))
    expect(tab('Payments')).toHaveAttribute('aria-selected', 'true')
    expect(within(drawer).getByText('cash')).toBeInTheDocument()
    expect(within(drawer).getByText('Manager approved')).toBeInTheDocument()

    // Timeline includes Created / a payment event / Status.
    await user.click(tab('Timeline'))
    expect(tab('Timeline')).toHaveAttribute('aria-selected', 'true')
    expect(within(drawer).getByText('Created')).toBeInTheDocument()
    expect(within(drawer).getByText(/Paid cash/)).toBeInTheDocument()
    expect(within(drawer).getByText('Status')).toBeInTheDocument()

    // Receipt renders the itemised receipt (sale #, line).
    await user.click(tab('Receipt'))
    expect(tab('Receipt')).toHaveAttribute('aria-selected', 'true')
    expect(within(drawer).getByText(/Receipt for sale #1001/)).toBeInTheDocument()
    expect(within(drawer).getByText('Widget')).toBeInTheDocument()
    // The receipt's own sale-number metadata row.
    expect(within(drawer).getByText('Sale #')).toBeInTheDocument()
  })
})
