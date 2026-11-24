// @vitest-environment jsdom
/**
 * Component tests for the dashboard analytics panels (src/dashboard/*):
 * TopItemsPanel, PerformancePanel, LowStockPanel.
 *
 * The network layer (`apiFetch`) is mocked. Seeded `initialData` renders the
 * expanded states without the fetch effect; the fetch path (loading / error /
 * success) is exercised via the mocked `apiFetch`, matching the SuppliersPage
 * test conventions.
 */
import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import '@testing-library/jest-dom/vitest'

import { ApiError, apiFetch } from '../../src/api'
import TopItemsPanel from '../../src/dashboard/TopItemsPanel'
import PerformancePanel from '../../src/dashboard/PerformancePanel'
import LowStockPanel from '../../src/dashboard/LowStockPanel'

vi.mock('../../src/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../src/api')>()
  return { ...actual, apiFetch: vi.fn() }
})

const mockedFetch = vi.mocked(apiFetch)

const TOP_PAYLOAD = {
  scope: 'today',
  total_items: 3,
  top_items: [
    { rank: 1, product_id: 17, product_name: 'Pasta', units_sold: 42, revenue: '320.50', avg_unit_price: '7.63' },
    { rank: 2, product_id: 4, product_name: 'Oil', units_sold: 31, revenue: '210.00', avg_unit_price: '6.77' },
    { rank: 3, product_id: 9, product_name: 'Bread', units_sold: 12, revenue: '48.00', avg_unit_price: '4.00' },
  ],
}

const MONTHLY_PAYLOAD = {
  months_requested: 6,
  periods: [
    { period: '2026-06', label: 'Jun 2026', sale_count: 80, revenue: '3800.00', cash_sales: '1700.00', avg_basket: '47.50', mom_revenue: null, mom_revenue_pct: null },
    { period: '2026-07', label: 'Jul 2026', sale_count: 90, revenue: '4300.00', cash_sales: '1950.00', avg_basket: '47.78', mom_revenue: '500.00', mom_revenue_pct: 13.16 },
    { period: '2026-08', label: 'Aug 2026', sale_count: 70, revenue: '3200.00', cash_sales: '1500.00', avg_basket: '45.71', mom_revenue: '-1100.00', mom_revenue_pct: -25.58 },
    // newest / in-progress month has no mom: the server reports null.
    { period: '2026-09', label: 'Sep 2026', sale_count: 75, revenue: '3400.00', cash_sales: '1600.00', avg_basket: '45.33', mom_revenue: null, mom_revenue_pct: null },
  ],
  latest_period: '2026-09',
}

const LOW_STOCK_PAYLOAD = {
  generated_at: '2026-11-24T09:12:00+11:00',
  threshold_source: { field: 'min_stock', rule: 'quantity <= min_stock', non_stock_excluded: true },
  low_stock_count: 3,
  items: [
    { product_id: 1, product_name: 'Rice', sku: 'rce-0001', quantity_on_hand: 7, min_stock: 10, category: 'Grocery' },
    { product_id: 17, product_name: 'Pasta', sku: 'pnt-0001', quantity_on_hand: 2, min_stock: 5, category: 'Grocery' },
    { product_id: 4, product_name: 'Cooking oil', quantity_on_hand: 0, min_stock: 6, category: 'Grocery' },
  ],
}

beforeEach(() => {
  mockedFetch.mockReset()
})

afterEach(() => {
  cleanup()
})

/* ------------------------------------------------------------------ */
/* TopItemsPanel                                                       */
/* ------------------------------------------------------------------ */
describe('TopItemsPanel — data fetching states', () => {
  it('shows the loading state while GET /top/ is in flight', () => {
    mockedFetch.mockReturnValue(new Promise(() => {}))
    render(<TopItemsPanel />)
    expect(screen.getByRole('status')).toHaveTextContent('Loading top items…')
    expect(mockedFetch).toHaveBeenCalledWith('/api/dashboard/sales/top/?scope=today')
  })

  it('renders the top items table after the request resolves', async () => {
    mockedFetch.mockResolvedValue(TOP_PAYLOAD)
    render(<TopItemsPanel />)
    expect(await screen.findByText('Pasta')).toBeInTheDocument()
    expect(screen.getAllByRole('row')).toHaveLength(4) // header + 3 items
    expect(screen.getByText('$320.50')).toBeInTheDocument()
    expect(screen.getByText('42')).toBeInTheDocument()
  })

  it('shows the API error message with a retry that refetches', async () => {
    mockedFetch.mockRejectedValue(new ApiError('Server error', 500))
    render(<TopItemsPanel />)
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('Server error')

    mockedFetch.mockResolvedValue(TOP_PAYLOAD)
    await userEvent.click(within(alert).getByText('Retry'))
    expect(await screen.findByText('Pasta')).toBeInTheDocument()
    expect(mockedFetch).toHaveBeenCalledTimes(2)
  })
})

describe('TopItemsPanel — rendered states (seeded)', () => {
  it('shows rank, product, units, revenue, and avg price columns', () => {
    render(<TopItemsPanel initialData={TOP_PAYLOAD} />)
    const row = screen.getByText('Pasta').closest('tr')!
    expect(within(row).getByText('1')).toBeInTheDocument()
    expect(within(row).getByText('42')).toBeInTheDocument()
    expect(within(row).getByText('$320.50')).toBeInTheDocument()
    expect(within(row).getByText('$7.63')).toBeInTheDocument()
  })

  it('shows the "N more" notice when more than 5 items exist', () => {
    render(<TopItemsPanel
      initialData={{ scope: 'today', total_items: 8, top_items: TOP_PAYLOAD.top_items }}
    />)
    expect(screen.getByText(/8 items sold — 5 more not shown/)).toBeInTheDocument()
  })

  it('shows the empty state when no items were sold', () => {
    render(<TopItemsPanel initialData={{ scope: 'today', total_items: 0, top_items: [] }} />)
    expect(screen.getByText('No items sold in this period')).toBeInTheDocument()
  })

  it('switches scope and fetches the matching endpoint', async () => {
    render(<TopItemsPanel initialData={TOP_PAYLOAD} />)
    await userEvent.click(screen.getByRole('tab', { name: 'Week' }))
    await waitFor(() =>
      expect(mockedFetch).toHaveBeenCalledWith('/api/dashboard/sales/top/?scope=week'),
    )
  })
})

/* ------------------------------------------------------------------ */
/* PerformancePanel                                                    */
/* ------------------------------------------------------------------ */
describe('PerformancePanel — data fetching states', () => {
  it('shows the loading state while GET /monthly/ is in flight', () => {
    mockedFetch.mockReturnValue(new Promise(() => {}))
    render(<PerformancePanel />)
    expect(screen.getByRole('status')).toHaveTextContent('Loading performance…')
    expect(mockedFetch).toHaveBeenCalledWith('/api/dashboard/sales/monthly/?months=12')
  })

  it('renders the accessible chart and data table after resolve', async () => {
    mockedFetch.mockResolvedValue(MONTHLY_PAYLOAD)
    render(<PerformancePanel />)
    const table = await screen.findByRole('table')
    expect(within(table).getByText('Jul 2026')).toBeInTheDocument()
    const chart = screen.getByRole('img')
    expect(chart).toHaveAccessibleName()
    // data table shows month-over-month change
    expect(within(table).getByText('+13.16%')).toBeInTheDocument()
    expect(within(table).getByText('-25.58%')).toBeInTheDocument()
  })

  it('shows a retry banner on error', async () => {
    mockedFetch.mockRejectedValue(new ApiError('boom', 500))
    render(<PerformancePanel />)
    expect(await screen.findByRole('alert')).toHaveTextContent('boom')
  })
})

describe('PerformancePanel — rendered states (seeded)', () => {
  it('renders monthly revenue and mom deltas in the table', () => {
    render(<PerformancePanel initialData={MONTHLY_PAYLOAD} />)
    const table = screen.getByRole('table')
    expect(within(table).getByText('$3,800.00')).toBeInTheDocument()
    expect(within(table).getByText('$4,300.00')).toBeInTheDocument()
    const rows = within(table).getAllByRole('row')
    const julRow = rows.find((r) => within(r).queryByText('Jul 2026'))!
    expect(julRow.textContent).toContain('+13.16%')
    expect(julRow.textContent).toContain('+$500.00')
    const augRow = rows.find((r) => within(r).queryByText('Aug 2026'))!
    expect(augRow.textContent).toContain('-25.58%')
    expect(augRow.textContent).toContain('-$1,100.00')
  })

  it('renders an em-dash for months with no mom comparison', () => {
    render(<PerformancePanel initialData={MONTHLY_PAYLOAD} />)
    const rows = screen.getAllByRole('row')
    const junRow = rows.find((r) => within(r).queryByText('Jun 2026'))!
    expect(junRow.textContent).toContain('—')
    const sepRow = rows.find((r) => within(r).queryByText('Sep 2026'))!
    expect(sepRow.textContent).toContain('—')
  })

  it('shows the empty state when no periods exist', () => {
    render(<PerformancePanel initialData={{ months_requested: 12, periods: [], latest_period: null }} />)
    expect(screen.getByText('No sales data yet')).toBeInTheDocument()
  })
})

/* ------------------------------------------------------------------ */
/* LowStockPanel                                                       */
/* ------------------------------------------------------------------ */
describe('LowStockPanel — data fetching states', () => {
  it('shows the loading state while GET /low-stock/ is in flight', () => {
    mockedFetch.mockReturnValue(new Promise(() => {}))
    render(<LowStockPanel />)
    expect(screen.getByRole('status')).toHaveTextContent('Loading stock levels…')
    expect(mockedFetch).toHaveBeenCalledWith('/api/dashboard/inventory/low-stock/')
  })

  it('renders items, quantities, thresholds, and status after resolve', async () => {
    mockedFetch.mockResolvedValue(LOW_STOCK_PAYLOAD)
    render(<LowStockPanel />)
    expect(await screen.findByText('Pasta')).toBeInTheDocument()
    expect(screen.getByText('Cooking oil')).toBeInTheDocument()
    expect(screen.getByText('Out of stock')).toBeInTheDocument()
    expect(screen.getByText('Low')).toBeInTheDocument()
    // sku · category meta is shown
    expect(screen.getByText(/pnt-0001 · Grocery/)).toBeInTheDocument()
    // header subtitle shows the count
    expect(screen.getByText(/3 items at or below restock threshold/)).toBeInTheDocument()
  })

  it('shows the empty state when nothing is below threshold', async () => {
    mockedFetch.mockResolvedValue({ ...LOW_STOCK_PAYLOAD, low_stock_count: 0, items: [] })
    render(<LowStockPanel />)
    expect(await screen.findByText('No items below threshold')).toBeInTheDocument()
  })

  it('shows a retry banner on error', async () => {
    mockedFetch.mockRejectedValue(new ApiError('boom', 500))
    render(<LowStockPanel />)
    expect(await screen.findByRole('alert')).toHaveTextContent('boom')
  })
})

describe('LowStockPanel — rendered states (seeded)', () => {
  it('shows product metadata (sku · category) alongside the item', () => {
    render(<LowStockPanel initialData={LOW_STOCK_PAYLOAD} />)
    expect(screen.getByText(/pnt-0001.*Grocery/)).toBeInTheDocument()
    expect(screen.getByText('Grocery')).toBeInTheDocument()
  })

  it('marks out-of-stock items as danger and low items as warning', () => {
    render(<LowStockPanel initialData={LOW_STOCK_PAYLOAD} />)
    expect(screen.getByText('Out of stock').closest('.mm-badge')?.className).toContain('mm-badge--danger')
    expect(screen.getByText('Low').closest('.mm-badge')?.className).toContain('mm-badge--warning')
  })

  it('shows the empty state when seeded empty', () => {
    render(<LowStockPanel initialData={{ ...LOW_STOCK_PAYLOAD, low_stock_count: 0, items: [] }} />)
    expect(screen.getByText('No items below threshold')).toBeInTheDocument()
  })
})
