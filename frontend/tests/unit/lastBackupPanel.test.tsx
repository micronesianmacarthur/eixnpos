// @vitest-environment jsdom
/**
 * Component tests for LastBackupPanel (src/dashboard/LastBackupPanel), the
 * dashboard card that answers "when was the system last backed up" and lets
 * a manager trigger a backup immediately.
 *
 * The network layer (`apiFetch`) is mocked. Seeding `initialStatus` renders
 * the expanded states without the fetch effect; the fetch path (loading /
 * error / success) and the quick-action POST are exercised via the mocked
 * `apiFetch`, matching the other dashboard panel suites.
 */
import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import '@testing-library/jest-dom/vitest'

import { ApiError, apiFetch } from '../../src/api'
import LastBackupPanel from '../../src/dashboard/LastBackupPanel'
import type { BackupStatus } from '../../src/dashboard/data'

vi.mock('../../src/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../src/api')>()
  return { ...actual, apiFetch: vi.fn(), errorMessage: actual.errorMessage }
})

const mockedFetch = vi.mocked(apiFetch)

const CONFIGURED_WITH_BACKUP: BackupStatus = {
  backup_path_configured: true,
  last_backup_at: '2026-11-24T04:00:00+00:00',
  last_backup_at_local: '2026-11-24T07:00:00+11:00',
  last_backup_path: '/data/backups/backup_20261124_040000.json',
}

const CONFIGURED_NEVER: BackupStatus = {
  backup_path_configured: true,
  last_backup_at: null,
  last_backup_at_local: null,
  last_backup_path: null,
}

const NOT_CONFIGURED: BackupStatus = {
  backup_path_configured: false,
  last_backup_at: null,
  last_backup_at_local: null,
  last_backup_path: null,
}

beforeEach(() => {
  mockedFetch.mockReset()
})

afterEach(() => {
  cleanup()
})

describe('LastBackupPanel — data fetching states', () => {
  it('shows the loading state while GET /backup/ is in flight', () => {
    mockedFetch.mockReturnValue(new Promise(() => {}))
    render(<LastBackupPanel />)
    expect(screen.getByText('Loading backup status…')).toBeInTheDocument()
    expect(mockedFetch).toHaveBeenCalledWith('/api/dashboard/backup/')
  })

  it('renders the last-backup time (business local) after the request resolves', async () => {
    mockedFetch.mockResolvedValue(CONFIGURED_WITH_BACKUP)
    render(<LastBackupPanel />)

    // The business-local instant (+11:00) formats with the embedded offset,
    // independent of the viewer's browser timezone.
    expect(await screen.findByText(/Nov 24, 2026/)).toBeInTheDocument()
    expect(mockedFetch).toHaveBeenCalledWith('/api/dashboard/backup/')
  })

  it('shows the API error message with a retry that refetches', async () => {
    mockedFetch.mockRejectedValue(new ApiError('Server error', 500))
    render(<LastBackupPanel />)
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('Server error')

    mockedFetch.mockResolvedValue(CONFIGURED_WITH_BACKUP)
    await userEvent.click(within(alert).getByText('Retry'))
    expect(await screen.findByText(/Nov 24, 2026/)).toBeInTheDocument()
    expect(mockedFetch).toHaveBeenCalledTimes(2)
  })
})

describe('LastBackupPanel — rendered states (seeded)', () => {
  it('shows "Never backed up" when a path is configured but no dump exists', () => {
    render(<LastBackupPanel initialStatus={CONFIGURED_NEVER} />)
    expect(screen.getByText('Never backed up')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Create a backup now' })).toBeEnabled()
  })

  it('shows the business-local timestamp when a backup exists', () => {
    render(<LastBackupPanel initialStatus={CONFIGURED_WITH_BACKUP} />)
    expect(screen.getByText(/Nov 24, 2026/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Create a backup now' })).toBeEnabled()
  })

  it('disables the button when no backup path is configured and explains why', () => {
    render(<LastBackupPanel initialStatus={NOT_CONFIGURED} />)
    expect(screen.getByText('Never backed up')).toBeInTheDocument()
    const button = screen.getByRole('button', { name: /no backup path is configured/ })
    expect(button).toBeDisabled()
    // The tooltip lives on the wrapping span (native title attr).
    const anchor = button.closest('.mm-tooltip-anchor')
    expect(anchor?.getAttribute('title')).toMatch(/Settings/i)
  })

  it('exposes an accessible name on the action button', () => {
    render(<LastBackupPanel initialStatus={CONFIGURED_WITH_BACKUP} />)
    const button = screen.getByRole('button', { name: 'Create a backup now' })
    expect(button).toBeInTheDocument()
  })
})

describe('LastBackupPanel — quick backup action', () => {
  it('calls POST /api/backups/trigger/ then refreshes the status', async () => {
    mockedFetch
      // first call: initial GET status
      .mockResolvedValueOnce(CONFIGURED_WITH_BACKUP)
      // second call: POST trigger returns the new dump path
      .mockResolvedValueOnce({ path: '/data/backups/backup_20261124_041000.json' })
      // third call: status re-fetch after the trigger
      .mockResolvedValueOnce({
        ...CONFIGURED_WITH_BACKUP,
        last_backup_at: '2026-11-24T04:10:00+00:00',
        last_backup_at_local: '2026-11-24T07:10:00+11:00',
        last_backup_path: '/data/backups/backup_20261124_041000.json',
      })

    const user = userEvent.setup()
    render(<LastBackupPanel />)
    await screen.findByText(/Nov 24, 2026/)

    await user.click(screen.getByRole('button', { name: 'Create a backup now' }))

    await waitFor(() =>
      expect(mockedFetch).toHaveBeenCalledWith('/api/backups/trigger/', {
        method: 'POST',
      }),
    )
    expect(await screen.findByText(/Nov 24, 2026/)).toBeInTheDocument()
    expect(mockedFetch).toHaveBeenCalledTimes(3)
  })

  it('shows a busy state while the POST is in flight', async () => {
    mockedFetch
      // initial GET status, then the trigger POST hangs (never resolves)
      .mockResolvedValueOnce(CONFIGURED_WITH_BACKUP)
      .mockReturnValueOnce(new Promise(() => {}))

    const user = userEvent.setup()
    render(<LastBackupPanel />)
    await screen.findByText(/Nov 24, 2026/)

    await user.click(screen.getByRole('button', { name: 'Create a backup now' }))

    const busy = await screen.findByRole('button', { name: 'Creating a backup now' })
    expect(busy).toBeDisabled()
    expect(busy).toHaveAttribute('aria-busy', 'true')
  })

  it('announces an error in the live region when the trigger fails', async () => {
    mockedFetch
      // initial GET status succeeds; the trigger POST fails
      .mockResolvedValueOnce(CONFIGURED_WITH_BACKUP)
      .mockRejectedValueOnce(new ApiError('backup failed', 500))

    const user = userEvent.setup()
    render(<LastBackupPanel />)
    await screen.findByText(/Nov 24, 2026/)

    await user.click(screen.getByRole('button', { name: 'Create a backup now' }))

    await waitFor(() => {
      const live = screen.getByRole('status')
      expect(live.textContent).toContain('Backup failed')
    })
  })

  it('does not trigger when no backup path is configured', async () => {
    const user = userEvent.setup()
    render(<LastBackupPanel initialStatus={NOT_CONFIGURED} />)
    await user.click(screen.getByRole('button', { name: /no backup path is configured/ }))
    expect(mockedFetch).not.toHaveBeenCalledWith('/api/backups/trigger/', expect.anything())
  })
})
