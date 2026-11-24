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
import { cleanup, render, screen } from '@testing-library/react'
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

  it('surfaces the API error and offers retry', async () => {
    mockedFetch.mockRejectedValue(new ApiError('Server error', 500, undefined))
    render(<DashboardPage />)
    expect(await screen.findByText('Server error')).toBeInTheDocument()
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
})

describe('DashboardPage — role gating', () => {
  it('does not fetch the manager API for cashiers and shows a notice', () => {
    currentRole.value = 'cashier'
    render(<DashboardPage />)
    expect(mockedFetch).not.toHaveBeenCalled()
    expect(screen.getByText('Manager dashboard')).toBeInTheDocument()
  })
})
