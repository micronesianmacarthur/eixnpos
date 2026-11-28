// @vitest-environment jsdom
/**
 * Component tests for SessionSummaryCard — the dashboard's open-shift card.
 *
 * Sessions are closed manually only, so the card must never imply an automatic
 * close and must surface a shift left open on an earlier business day (with a
 * way into the close flow) instead of claiming "No open shift". The network
 * layer is not used: the component takes the session payload as a prop, so
 * these are pure render assertions.
 */
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import '@testing-library/jest-dom/vitest'

import { SessionSummaryCard } from '../../src/SessionSummaryCard'
import { businessDayDifference, daysLabel } from '../../src/dashboardFormat'
import type { DashboardOpenSession, DashboardSession } from '../../src/DashboardTypes'

afterEach(() => {
  cleanup()
})

function openSession(overrides: Partial<DashboardOpenSession> = {}): DashboardOpenSession {
  const openedBy = { id: 3, username: 'jane', role: 'cashier' }
  return {
    session_id: 7,
    register_name: 'Front',
    opened_at: '2026-11-24T07:00:00+11:00',
    opened_at_local: '2026-11-24T07:00:00+11:00',
    started_at: '2026-11-24T07:00:00+11:00',
    started_at_local: '2026-11-24T07:00:00+11:00',
    business_day: '2026-11-24',
    register: { id: 1, name: 'Front', identifier: 'REG-1' },
    opened_by: openedBy,
    cashier: openedBy,
    cashiers: ['jane'],
    starting_cash: '100.00',
    ...overrides,
  }
}

function payload(overrides: Partial<DashboardSession> = {}): DashboardSession {
  return {
    open: false,
    count: 0,
    business_day: '2026-11-24',
    sessions: [],
    session: { open: false },
    previous_day_sessions: [],
    previous_day_count: 0,
    ...overrides,
  }
}

function renderCard(session: DashboardSession) {
  return render(
    <SessionSummaryCard
      session={session}
      loading={false}
      error={null}
      onRetry={vi.fn()}
      businessTimezone="Pacific/Noumea"
    />,
  )
}

describe('business day helpers', () => {
  it('counts whole calendar days between business-day strings', () => {
    expect(businessDayDifference('2026-11-23', '2026-11-24')).toBe(1)
    expect(businessDayDifference('2026-11-20', '2026-11-24')).toBe(4)
    expect(businessDayDifference('2026-11-24', '2026-11-24')).toBe(0)
    expect(businessDayDifference('not-a-date', '2026-11-24')).toBe(0)
  })

  it('pluralises the day label', () => {
    expect(daysLabel(0)).toBe('0 days')
    expect(daysLabel(1)).toBe('1 day')
    expect(daysLabel(3)).toBe('3 days')
  })
})

describe('SessionSummaryCard — manual close messaging', () => {
  it('shows the empty state when no shift is open at all', () => {
    renderCard(payload())

    expect(screen.getByText('No open shift')).toBeInTheDocument()
    expect(screen.getByText('Open a register to start selling.')).toBeInTheDocument()
    expect(screen.queryByText(/Unclosed shift/)).not.toBeInTheDocument()
  })

  it('says an open shift stays open until it is closed manually', () => {
    renderCard(payload({ open: true, count: 1, sessions: [openSession()] }))

    expect(screen.getByText(/Open since/)).toBeInTheDocument()
    expect(
      screen.getByText(/A shift stays open — even overnight — until someone closes it manually\./),
    ).toBeInTheDocument()
    expect(
      screen.getByRole('link', { name: 'Close & reconcile' }),
    ).toHaveAttribute('href', '/close')
    expect(screen.queryByText(/closes automatically|auto-close|countdown/i)).not.toBeInTheDocument()
  })

  it('surfaces a previous-day shift instead of claiming no open shift', () => {
    const stale = openSession({
      session_id: 11,
      business_day: '2026-11-23',
      opened_at: '2026-11-23T22:00:00+11:00',
      opened_at_local: '2026-11-23T22:00:00+11:00',
    })
    renderCard(
      payload({ previous_day_sessions: [stale], previous_day_count: 1 }),
    )

    // The heading is retained, but the copy must not imply everything is fine.
    expect(screen.getByText('No open shift')).toBeInTheDocument()
    expect(
      screen.getByText(/an earlier shift is still open\. Close and reconcile it below\./),
    ).toBeInTheDocument()

    expect(screen.getByText('Unclosed shift from an earlier day')).toBeInTheDocument()
    expect(
      screen.getByText(/they never close automatically at midnight\./),
    ).toBeInTheDocument()
    expect(screen.getByText('Register Front')).toBeInTheDocument()
    expect(screen.getByText(/Business day 2026-11-23/)).toBeInTheDocument()
    expect(screen.getByText(/1 day ago/)).toBeInTheDocument()

    const link = screen.getByRole('link', { name: 'Close & reconcile' })
    expect(link).toHaveAttribute('href', '/close?session=11')
  })

  it('lists every previous-day shift with its own close link', () => {
    const first = openSession({ session_id: 11, register_name: 'Front', business_day: '2026-11-22' })
    const second = openSession({ session_id: 12, register_name: 'Back', business_day: '2026-11-23' })
    renderCard(
      payload({ previous_day_sessions: [first, second], previous_day_count: 2 }),
    )

    expect(screen.getByText('2 unclosed shifts from earlier days')).toBeInTheDocument()
    const links = screen.getAllByRole('link', { name: 'Close & reconcile' })
    expect(links.map((link) => link.getAttribute('href'))).toEqual([
      '/close?session=11',
      '/close?session=12',
    ])
    expect(screen.getByText(/2 days ago/)).toBeInTheDocument()
    expect(screen.getByText(/1 day ago/)).toBeInTheDocument()
  })

  it('shows today’s open shift and the previous-day reminder together', () => {
    const stale = openSession({ session_id: 11, business_day: '2026-11-23' })
    renderCard(
      payload({
        open: true,
        count: 1,
        sessions: [openSession()],
        previous_day_sessions: [stale],
        previous_day_count: 1,
      }),
    )

    expect(screen.getByText(/Open since/)).toBeInTheDocument()
    expect(screen.getByText('Unclosed shift from an earlier day')).toBeInTheDocument()
  })
})
