// @vitest-environment jsdom
/**
 * Component tests for the manager dashboard page (DashboardPage + KPI row +
 * session card).
 *
 * The network layer (`apiFetch`) and the auth context are mocked, so these
 * tests are fully deterministic and make no real requests. The dashboard's
 * data comes from the composite `GET /api/dashboard/` endpoint; state
 * transitions (loading / error / success / empty / non-manager) are each
 * exercised in isolation.
 */
import { cleanup, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import '@testing-library/jest-dom/vitest'

import { ApiError, apiFetch } from '../../src/api'
import DashboardPage from '../../src/DashboardPage'
import { formatMoney, formatDateTime } from '../../src/dashboardFormat'
import type { DashboardOverview } from '../../src/DashboardTypes'

vi.mock('../../src/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../src/api')>()
  return { ...actual, apiFetch: vi.fn() }
})

// Auth is provided via context in the app; for component tests we stub the
// hook and switch roles through this mutable holder.
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

// The analytics panels (top items / performance / low stock) are sibling
// components with their own suites; the apiFetch stub below returns the
// composite overview shape, which those panels' own endpoints do not use.
// Stub them out so page-level tests only exercise KPIs + session.
vi.mock('../../src/dashboard/DashboardAnalyticsPanels', () => ({
  default: () => null,
}))

const mockedFetch = vi.mocked(apiFetch)

/** A realistic composite payload matching the server contract (§5.7). */
function overview(): DashboardOverview {
  return {
    business_day: '2026-11-24',
    generated_at: '2026-11-24T09:12:00+11:00',
    business_timezone: 'Pacific/Noumea',
    period: {
      scope: 'today',
      start: '2026-11-24T00:00:00+11:00',
      end: '2026-11-24T09:12:00+11:00',
    },
    today: {
      sales_count: 12,
      revenue: '1234.56',
      cash_sales: '567.89',
      invoice_count: 2,
      invoice_total: '600.00',
      void_count: 0,
      refund_count: 0,
      avg_basket: '102.88',
      open_count_today: 3,
    },
    session: {
      open: true,
      count: 1,
      business_day: '2026-11-24',
      sessions: [
        {
          session_id: 7,
          register_name: 'Front',
          opened_at: '2026-11-24T07:00:00+11:00',
          opened_at_local: '2026-11-24T07:00:00+11:00',
          started_at: '2026-11-24T07:00:00+11:00',
          started_at_local: '2026-11-24T07:00:00+11:00',
          business_day: '2026-11-24',
          register: { id: 1, name: 'Front', identifier: 'REG-1' },
          opened_by: { id: 3, username: 'jane', role: 'cashier' },
          cashier: { id: 3, username: 'jane', role: 'cashier' },
          cashiers: ['jane'],
          starting_cash: '100.00',
        },
      ],
      session: {
        session_id: 7,
        register_name: 'Front',
        opened_at: '2026-11-24T07:00:00+11:00',
        opened_at_local: '2026-11-24T07:00:00+11:00',
        started_at: '2026-11-24T07:00:00+11:00',
        started_at_local: '2026-11-24T07:00:00+11:00',
        business_day: '2026-11-24',
        register: { id: 1, name: 'Front', identifier: 'REG-1' },
        opened_by: { id: 3, username: 'jane', role: 'cashier' },
        cashier: { id: 3, username: 'jane', role: 'cashier' },
        cashiers: ['jane'],
        starting_cash: '100.00',
      },
    },
    inventory: {
      generated_at: '2026-11-24T09:12:00+11:00',
      low_stock_count: 0,
      items: [],
      threshold_source: {
        field: 'min_stock',
        rule: 'quantity <= min_stock',
        non_stock_excluded: true,
      },
    },
  }
}

beforeEach(() => {
  currentRole.value = 'admin'
  mockedFetch.mockReset()
})

afterEach(() => {
  cleanup()
})

describe('dashboard format helpers', () => {
  it('formats a 2-dp money string as USD without arithmetic', () => {
    expect(formatMoney('1234.56')).toBe('$1,234.56');
    expect(formatMoney('0.00')).toBe('$0.00');
    expect(formatMoney('.5')).toBe('$0.50');
  })
  it('formats an ISO datetime', () => {
    expect(formatDateTime('2026-11-24T07:00:00+11:00')).toMatch(/Nov 24, 2026/);
    expect(formatDateTime(undefined)).toBe('');
  })
  it('formats a datetime in a fractional-hour business timezone (S2)', () => {
    // +05:30 (Asia/Kolkata) cannot be expressed as a whole-hour Etc/GMT zone;
    // the IANA business_timezone must drive the wall clock. 2026-11-24T13:45
    // +05:30 == 08:15:00 UTC.
    expect(
      formatDateTime('2026-11-24T13:45:00+05:30', 'Asia/Kolkata'),
    ).toMatch(/Nov 24, 2026/);
    expect(
      formatDateTime('2026-11-24T13:45:00+05:30', 'Asia/Kolkata'),
    ).toMatch(/1:45 PM/);
  })
  it('falls back to the embedded offset when no IANA zone is provided (S2)', () => {
    // Without businessTimezone the whole-hour offset path still works; the
    // fractional case falls back to the environment default without crashing.
    expect(formatDateTime('2026-11-24T07:00:00+11:00', null)).toMatch(/Nov 24, 2026/);
    expect(formatDateTime('2026-11-24T13:45:00+05:30', undefined)).toMatch(/Nov 24, 2026/);
  })
})

describe('DashboardPage — data fetching states', () => {
  it('shows the loading skeleton while GET /api/dashboard/ is in flight', async () => {
    mockedFetch.mockReturnValue(new Promise(() => {})) // never resolves
    render(<DashboardPage />)
    expect(mockedFetch).toHaveBeenCalledWith('/api/dashboard/')
    expect(screen.getByTestId('kpi-sales-today-skeleton')).toBeInTheDocument()
  })

  it('renders the headline KPIs from the composite payload', async () => {
    mockedFetch.mockResolvedValue(overview())
    render(<DashboardPage />)

    expect(await screen.findByTestId('kpi-sales-today')).toHaveTextContent('12')
    expect(screen.getByTestId('kpi-revenue-today')).toHaveTextContent('$1,234.56')
    expect(screen.getByTestId('kpi-cash-today')).toHaveTextContent('$567.89')
    // Invoice tile shows "count · total".
    const invoice = screen.getByTestId('kpi-invoices-today')
    expect(invoice).toHaveTextContent('2')
    expect(invoice).toHaveTextContent('$600.00')
  })

  it('shows the current session opened time and opener', async () => {
    mockedFetch.mockResolvedValue(overview())
    render(<DashboardPage />)

    const title = await screen.findByText(/Open since/)
    expect(title).toHaveTextContent(/Nov 24, 2026/)
    expect(title).toHaveTextContent(/7:00 AM/)
    expect(screen.getByText(/Opener jane/)).toBeInTheDocument()
    expect(screen.getByText(/Starting cash \$100\.00/)).toBeInTheDocument()
  })

  it('renders the LastBackupPanel for managers', async () => {
    mockedFetch.mockResolvedValue(overview())
    render(<DashboardPage />)

    // The page mounts the backup card next to the KPIs + session summary.
    expect(await screen.findByRole('heading', { name: 'Backups' })).toBeInTheDocument()
    // The panel's own GET /api/dashboard/backup/ fires (it shares the mocked
    // fetch; the overview-shaped stub leaves it as a "never backed up" card
    // rather than an error).
    expect(mockedFetch).toHaveBeenCalledWith('/api/dashboard/backup/')
  })

  it('surfaces the API error and offers retry', async () => {
    mockedFetch.mockRejectedValue(new ApiError('Server error', 500, undefined))
    render(<DashboardPage />)
    expect(await screen.findByText('Server error')).toBeInTheDocument()
  })

  it('renders em-dash KPI tiles instead of zeros on load failure (S7)', async () => {
    mockedFetch.mockRejectedValue(new ApiError('Server error', 500, undefined))
    render(<DashboardPage />)
    // Once the failure settles, the tiles must read "unknown", never "0 sales".
    const sales = await screen.findByTestId('kpi-sales-today')
    expect(sales).toHaveTextContent('—')
    expect(screen.getByTestId('kpi-revenue-today')).toHaveTextContent('—')
    expect(screen.getByTestId('kpi-cash-today')).toHaveTextContent('—')
    expect(screen.getByTestId('kpi-invoices-today')).toHaveTextContent('—')
  })
})

describe('DashboardPage — empty / zero state', () => {
  it('renders zero KPI tiles and a "no open shift" session when empty', async () => {
    const empty = overview()
    empty.today = {
      sales_count: 0, revenue: '0.00', cash_sales: '0.00',
      invoice_count: 0, invoice_total: '0.00', void_count: 0,
      refund_count: 0, avg_basket: '0.00', open_count_today: 0,
    }
    empty.session = {
      open: false,
      count: 0,
      business_day: '2026-11-24',
      sessions: [],
      session: { open: false },
    }
    mockedFetch.mockResolvedValue(empty)
    render(<DashboardPage />)

    expect(await screen.findByTestId('kpi-sales-today')).toHaveTextContent('0')
    expect(screen.getByTestId('kpi-revenue-today')).toHaveTextContent('$0.00')
    expect(screen.getByTestId('kpi-cash-today')).toHaveTextContent('$0.00')
    const invoice = screen.getByTestId('kpi-invoices-today')
    expect(invoice).toHaveTextContent('0')
    expect(invoice).not.toHaveTextContent('·')
    expect(await screen.findByText('No open shift')).toBeInTheDocument()
  })

  it('renders one row per open register when multiple shifts are open', async () => {
    const multi = overview()
    multi.session = {
      open: true,
      count: 2,
      business_day: '2026-11-24',
      sessions: [
        {
          session_id: 7,
          register_name: 'Front',
          opened_at: '2026-11-24T07:00:00+11:00',
          opened_at_local: '2026-11-24T07:00:00+11:00',
          started_at: '2026-11-24T07:00:00+11:00',
          started_at_local: '2026-11-24T07:00:00+11:00',
          business_day: '2026-11-24',
          register: { id: 1, name: 'Front', identifier: 'REG-1' },
          opened_by: { id: 3, username: 'jane', role: 'cashier' },
          cashier: { id: 3, username: 'jane', role: 'cashier' },
          cashiers: ['jane'],
          starting_cash: '100.00',
        },
        {
          session_id: 8,
          register_name: 'Back',
          opened_at: '2026-11-24T08:30:00+11:00',
          opened_at_local: '2026-11-24T08:30:00+11:00',
          started_at: '2026-11-24T08:30:00+11:00',
          started_at_local: '2026-11-24T08:30:00+11:00',
          business_day: '2026-11-24',
          register: { id: 2, name: 'Back', identifier: 'REG-2' },
          opened_by: { id: 4, username: 'sam', role: 'cashier' },
          cashier: { id: 4, username: 'sam', role: 'cashier' },
          cashiers: ['sam'],
          starting_cash: '50.00',
        },
      ],
    }
    mockedFetch.mockResolvedValue(multi)
    render(<DashboardPage />)

    expect(await screen.findByText('2 open shifts')).toBeInTheDocument()
    expect(screen.getByText(/Across 2 registers/)).toBeInTheDocument()
    // Both openers/registers are listed.
    expect(screen.getByText(/Opener jane/)).toBeInTheDocument()
    expect(screen.getByText(/Opener sam/)).toBeInTheDocument()
    expect(screen.getByText(/Register Front/)).toBeInTheDocument()
    expect(screen.getByText(/Register Back/)).toBeInTheDocument()
  })

  it('shows a "no completed sales today" banner when today has zero sales (S9)', async () => {
    const empty = overview()
    empty.today = {
      sales_count: 0, revenue: '0.00', cash_sales: '0.00',
      invoice_count: 0, invoice_total: '0.00', void_count: 0,
      refund_count: 0, avg_basket: '0.00', open_count_today: 0,
    }
    empty.session = {
      open: true,
      count: 1,
      business_day: '2026-11-24',
      sessions: [],
      session: { open: false },
    }
    mockedFetch.mockResolvedValue(empty)
    render(<DashboardPage />)

    expect(await screen.findByText(/No completed sales today/)).toBeInTheDocument()
  })
})

describe('DashboardPage — stale-data pill and refresh (S6)', () => {
  it('shows a "Data from" pill with a manual refresh button', async () => {
    mockedFetch.mockResolvedValue(overview())
    render(<DashboardPage />)

    expect(await screen.findByText(/Data from/)).toBeInTheDocument()
    const stale = (screen.getByText(/Data from/).closest(
      '.dashboard-stale',
    )) as HTMLElement
    expect(
      within(stale).getByRole('button', { name: 'Refresh' }),
    ).toBeInTheDocument()
  })

  it('re-requests the composite on manual refresh', async () => {
    mockedFetch.mockResolvedValue(overview())
    render(<DashboardPage />)
    await screen.findByText(/Data from/)
    const stale = (screen.getByText(/Data from/).closest(
      '.dashboard-stale',
    )) as HTMLElement
    const refresh = within(stale).getByRole('button', { name: 'Refresh' })
    mockedFetch.mockClear()
    await userEvent.click(refresh)
    expect(mockedFetch).toHaveBeenCalledWith('/api/dashboard/')
  })

  it('does not stamp a client clock on first-load failure, then updates on a successful refresh (F1)', async () => {
    let mainCalls = 0
    // The composite and the backup panel each make a call; only the composite
    // fails on the first load, then succeeds on the manual refresh.
    mockedFetch.mockImplementation((path: string) => {
      if (path === '/api/dashboard/') {
        mainCalls += 1
        return mainCalls === 1
          ? Promise.reject(new ApiError('Server error', 500, undefined))
          : Promise.resolve(overview())
      }
      // /api/dashboard/backup/ — not under test, just keep it harmless.
      return Promise.resolve(overview())
    })
    render(<DashboardPage />)

    // First load rejects: the pill must NOT claim a server timestamp it never
    // received — a client-clock "Data from <now>" would be fabricated.
    expect(await screen.findByText('Server error')).toBeInTheDocument()
    expect(screen.queryByText(/Data from/)).not.toBeInTheDocument()
    expect(screen.getByText('Data not yet loaded')).toBeInTheDocument()

    // A later successful refresh fills in the honest, server-provided time.
    const stale = (screen.getByText('Data not yet loaded').closest(
      '.dashboard-stale',
    )) as HTMLElement
    await userEvent.click(within(stale).getByRole('button', { name: 'Refresh' }))
    expect(await screen.findByText(/Data from/)).toBeInTheDocument()
  })
})

describe('DashboardPage — low-stock KPI tile drill-down (S9)', () => {
  it('renders the 5th tile with the low-stock count', async () => {
    const payload = overview()
    payload.inventory.low_stock_count = 4
    mockedFetch.mockResolvedValue(payload)
    render(<DashboardPage />)

    const tile = await screen.findByTestId('kpi-low-stock')
    expect(tile).toHaveTextContent('4')
    expect(screen.getByText('Low stock')).toBeInTheDocument()
  })

  it('low-stock tile is clickable and scrolls to the low-stock panel', async () => {
    const scrollIntoView = vi.fn()
    // jsdom has no low-stock panel mounted (analytics panels are stubbed),
    // so stub the element lookup to return a fake with an observable scroll.
    const originalGet = document.getElementById.bind(document)
    document.getElementById = vi.fn((id: string) =>
      id === 'low-stock-panel' ? ({ scrollIntoView } as unknown as HTMLElement) : originalGet(id),
    ) as typeof document.getElementById
    mockedFetch.mockResolvedValue(overview())
    render(<DashboardPage />)

    const tile = await screen.findByTestId('kpi-low-stock')
    const button = tile.closest('button') ?? tile
    await userEvent.click(button)
    expect(scrollIntoView).toHaveBeenCalled()
  })
})

describe('DashboardPage — role agnostic access', () => {
  it('loads the dashboard for cashiers (KPIs render, no backup call)', async () => {
    currentRole.value = 'cashier'
    mockedFetch.mockResolvedValue(overview())
    render(<DashboardPage />)

    // A cashier fetches the composite and renders the headline KPIs.
    expect(mockedFetch).toHaveBeenCalledWith('/api/dashboard/')
    expect(await screen.findByTestId('kpi-sales-today')).toHaveTextContent('12')
    expect(screen.getByTestId('kpi-revenue-today')).toHaveTextContent('$1,234.56')

    // The backup panel is a manager/admin affordance: not rendered and its
    // GET /api/dashboard/backup/ endpoint is never called for a cashier.
    expect(screen.queryByRole('heading', { name: 'Backups' })).not.toBeInTheDocument()
    expect(mockedFetch).not.toHaveBeenCalledWith('/api/dashboard/backup/')
  })
})
