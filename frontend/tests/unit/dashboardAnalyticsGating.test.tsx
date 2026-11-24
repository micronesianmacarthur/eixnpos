// @vitest-environment jsdom
/**
 * Component test for the DashboardAnalyticsPanels composition: it must only
 * render (and only fetch dashboard data) for manager/admin roles. Non-managers
 * on the shared landing page must not fire dashboard requests they cannot
 * authorize (contract §7).
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

const currentRole = vi.hoisted(() => ({ value: 'admin' as string }))
vi.mock('../../src/authContext', () => ({
  useAuth: () => ({ me: { user: { role: currentRole.value } } }),
}))

const mockedFetch = vi.mocked(apiFetch)

beforeEach(() => {
  currentRole.value = 'admin'
  mockedFetch.mockReset()
})

afterEach(() => {
  cleanup()
})

describe('DashboardAnalyticsPanels — role gating', () => {
  it('renders the analytics panels and fetches dashboard data for managers', async () => {
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
    expect(screen.getByText('Shop Performance')).toBeInTheDocument()
    expect(screen.getByText('Low Stock')).toBeInTheDocument()
    expect(mockedFetch.mock.calls.length).toBeGreaterThanOrEqual(3)
  })

  it('renders nothing and makes no dashboard requests for cashiers', () => {
    currentRole.value = 'cashier'
    render(<DashboardAnalyticsPanels />)
    expect(screen.queryByText('Top Items')).not.toBeInTheDocument()
    expect(screen.queryByText('Shop Performance')).not.toBeInTheDocument()
    expect(screen.queryByText('Low Stock')).not.toBeInTheDocument()
    expect(mockedFetch).not.toHaveBeenCalled()
  })
})
