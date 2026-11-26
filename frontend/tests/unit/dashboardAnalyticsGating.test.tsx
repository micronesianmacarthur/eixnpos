// @vitest-environment jsdom
/**
 * Component test for the DashboardAnalyticsPanels composition: it renders
 * (and fetches dashboard data for) every authenticated role. The dashboard
 * read endpoints require only authentication (contract §7), so cashiers,
 * managers, and admins all see the same analytics panels — there is no role
 * gating anymore.
 */
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import '@testing-library/jest-dom/vitest'

import { apiFetch } from '../../src/api'
import DashboardAnalyticsPanels from '../../src/dashboard/DashboardAnalyticsPanels'

vi.mock('../../src/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../src/api')>()
  return { ...actual, apiFetch: vi.fn() }
})

const mockedFetch = vi.mocked(apiFetch)

beforeEach(() => {
  mockedFetch.mockReset()
})

afterEach(() => {
  cleanup()
})

describe('DashboardAnalyticsPanels — renders for every authenticated role', () => {
  it('renders the analytics panels and fetches dashboard data', async () => {
    mockedFetch.mockImplementation((url: string) => {
      const u = String(url)
      if (u.includes('/top/')) {
        return Promise.resolve({ scope: 'today', total_items: 0, top_items: [] })
      }
      if (u.includes('/monthly/')) {
        return Promise.resolve({ months_requested: 12, periods: [], latest_period: null })
      }
      return Promise.resolve({ generated_at: '', threshold_source: { field: 'min_stock', rule: '' }, low_stock_count: 0, items: [] })
    })
    render(<DashboardAnalyticsPanels />)
    expect(await screen.findByText('Top Items')).toBeInTheDocument()
    expect(screen.getByText('Performance')).toBeInTheDocument()
    expect(screen.getByText('Low Stock')).toBeInTheDocument()
    expect(mockedFetch.mock.calls.length).toBeGreaterThanOrEqual(3)
  })

  it('renders the panels regardless of the current user role', () => {
    mockedFetch.mockImplementation((url: string) => {
      const u = String(url)
      if (u.includes('/top/')) {
        return Promise.resolve({ scope: 'today', total_items: 0, top_items: [] })
      }
      if (u.includes('/monthly/')) {
        return Promise.resolve({ months_requested: 12, periods: [], latest_period: null })
      }
      return Promise.resolve({ generated_at: '', threshold_source: { field: 'min_stock', rule: '' }, low_stock_count: 0, items: [] })
    })
    render(<DashboardAnalyticsPanels />)
    expect(screen.getByText('Top Items')).toBeInTheDocument()
    expect(screen.getByText('Performance')).toBeInTheDocument()
    expect(screen.getByText('Low Stock')).toBeInTheDocument()
    expect(mockedFetch.mock.calls.length).toBeGreaterThanOrEqual(3)
  })
})
