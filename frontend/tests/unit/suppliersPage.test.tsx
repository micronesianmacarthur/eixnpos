// @vitest-environment jsdom
/**
 * Component tests for SuppliersPage (frontend/src/SuppliersPage.tsx).
 *
 * The network layer (`apiFetch`) and the auth context are mocked, so these
 * tests are fully deterministic and make no real requests. Where the page
 * offers the `initialSuppliers` test seam, we seed through it to skip the
 * fetch effect entirely; the fetch path itself is exercised via the mocked
 * `apiFetch` (loading / error / success states).
 */
import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import '@testing-library/jest-dom/vitest'

import { ApiError, apiFetch } from '../../src/api'
import SuppliersPage, { type Supplier } from '../../src/SuppliersPage'

vi.mock('../../src/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../src/api')>()
  return { ...actual, apiFetch: vi.fn() }
})

// Auth is provided via context in the app; for component tests we stub the
// hook and switch roles through this mutable holder.
const currentRole = vi.hoisted(() => ({ value: 'admin' as string }))
vi.mock('../../src/authContext', () => ({
  useAuth: () => ({
    me: { user: { role: currentRole.value } },
    setMe: () => {},
  }),
}))

const mockedFetch = vi.mocked(apiFetch)

function supplier(overrides: Partial<Supplier> = {}): Supplier {
  return {
    id: 1,
    name: 'Acme Foods',
    contact_name: 'Jane Doe',
    email: 'jane@acme.test',
    phone: '555-0100',
    address: '1 Main St',
    notes: '',
    active: true,
    created_at: '2026-01-01T00:00:00Z',
    ...overrides,
  }
}

const SEED: Supplier[] = [
  supplier(),
  supplier({
    id: 2,
    name: 'Beacon Drinks',
    contact_name: 'Bob Ross',
    email: '',
    phone: '',
    active: false,
  }),
]

beforeEach(() => {
  currentRole.value = 'admin'
  mockedFetch.mockReset()
})

afterEach(() => {
  cleanup()
})

describe('SuppliersPage — data fetching states', () => {
  it('shows the loading state while GET /api/suppliers/ is in flight', async () => {
    mockedFetch.mockReturnValue(new Promise(() => {})) // never resolves
    render(<SuppliersPage />)
    expect(screen.getByRole('status')).toHaveTextContent('Loading suppliers...')
  })

  it('renders the fetched list after the request resolves', async () => {
    mockedFetch.mockResolvedValue(SEED)
    render(<SuppliersPage />)
    expect(await screen.findByText('Acme Foods')).toBeInTheDocument()
    expect(screen.getByText('Beacon Drinks')).toBeInTheDocument()
    expect(screen.getByText('2 suppliers on file · 1 active · 1 inactive'))
      .toBeInTheDocument()
    expect(mockedFetch).toHaveBeenCalledWith('/api/suppliers/')
  })

  it('normalizes a paginated { results } payload', async () => {
    mockedFetch.mockResolvedValue({ results: [supplier()] })
    render(<SuppliersPage />)
    expect(await screen.findByText('Acme Foods')).toBeInTheDocument()
  })

  it('shows the API error message when the request fails', async () => {
    mockedFetch.mockRejectedValue(new ApiError('Server error', 500))
    render(<SuppliersPage />)
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('Server error')
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
  })

  it('shows a generic message for non-ApiError failures', async () => {
    mockedFetch.mockRejectedValue(new TypeError('Network down'))
    render(<SuppliersPage />)
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Failed to load suppliers',
    )
  })
})

describe('SuppliersPage — rendered states (seeded, no network)', () => {
  it('renders rows with name, contact, phone, and status badge', () => {
    render(<SuppliersPage initialSuppliers={SEED} />)
    const row = screen.getByText('Acme Foods').closest('tr')!
    expect(within(row).getByText('Jane Doe')).toBeInTheDocument()
    expect(within(row).getByText('555-0100')).toBeInTheDocument()
    expect(within(row).getByText('Active')).toBeInTheDocument()
    const otherRow = screen.getByText('Beacon Drinks').closest('tr')!
    expect(within(otherRow).getByText('Inactive')).toBeInTheDocument()
  })

  it('renders empty dashes for missing phone, keeps present contact', () => {
    render(<SuppliersPage initialSuppliers={[SEED[1]]} />)
    const row = screen.getByText('Beacon Drinks').closest('tr')!
    expect(within(row).getByText('Bob Ross')).toBeInTheDocument()
    expect(within(row).getAllByText('—')).toHaveLength(1)
  })

  it('shows the "No suppliers yet" empty state for an empty list', () => {
    render(<SuppliersPage initialSuppliers={[]} />)
    expect(screen.getByText('No suppliers yet')).toBeInTheDocument()
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
  })
})

describe('SuppliersPage — search and status filter', () => {
  it('filters rows by supplier name', async () => {
    const user = userEvent.setup()
    render(<SuppliersPage initialSuppliers={SEED} />)
    await user.type(screen.getByLabelText('Search suppliers'), 'beacon')
    expect(screen.queryByText('Acme Foods')).not.toBeInTheDocument()
    expect(screen.getByText('Beacon Drinks')).toBeInTheDocument()
  })

  it('filters rows by contact name', async () => {
    const user = userEvent.setup()
    render(<SuppliersPage initialSuppliers={SEED} />)
    await user.type(screen.getByLabelText('Search suppliers'), 'bob')
    expect(screen.getByText('Beacon Drinks')).toBeInTheDocument()
    expect(screen.queryByText('Acme Foods')).not.toBeInTheDocument()
  })

  it('shows the no-match empty state when the search has no hits', async () => {
    const user = userEvent.setup()
    render(<SuppliersPage initialSuppliers={SEED} />)
    await user.type(screen.getByLabelText('Search suppliers'), 'zzz')
    expect(screen.getByText('No matching suppliers')).toBeInTheDocument()
  })

  it('filters by Active and Inactive status', async () => {
    const user = userEvent.setup()
    render(<SuppliersPage initialSuppliers={SEED} />)
    const select = screen.getByLabelText('Filter by supplier status')

    await user.selectOptions(select, 'Active')
    expect(screen.getByText('Acme Foods')).toBeInTheDocument()
    expect(screen.queryByText('Beacon Drinks')).not.toBeInTheDocument()

    await user.selectOptions(select, 'Inactive')
    expect(screen.queryByText('Acme Foods')).not.toBeInTheDocument()
    expect(screen.getByText('Beacon Drinks')).toBeInTheDocument()
  })

  it('combines search and status filter (AND semantics)', async () => {
    const user = userEvent.setup()
    render(<SuppliersPage initialSuppliers={SEED} />)
    await user.selectOptions(
      screen.getByLabelText('Filter by supplier status'),
      'Active',
    )
    await user.type(screen.getByLabelText('Search suppliers'), 'beacon')
    expect(screen.getByText('No matching suppliers')).toBeInTheDocument()
  })
})

describe('SuppliersPage — role gating', () => {
  it('hides manage buttons and shows the hint for cashiers', () => {
    currentRole.value = 'cashier'
    render(<SuppliersPage initialSuppliers={SEED} />)
    expect(screen.queryByText('+ New supplier')).not.toBeInTheDocument()
    expect(screen.queryByLabelText('Edit Acme Foods')).not.toBeInTheDocument()
    expect(screen.queryByLabelText('Delete Acme Foods')).not.toBeInTheDocument()
    expect(
      screen.getByText(
        'Supplier management actions are available to managers and admins.',
      ),
    ).toBeInTheDocument()
  })

  it('shows manage buttons for managers', () => {
    currentRole.value = 'manager'
    render(<SuppliersPage initialSuppliers={SEED} />)
    expect(screen.getByText('+ New supplier')).toBeInTheDocument()
    expect(screen.getByLabelText('Edit Acme Foods')).toBeInTheDocument()
  })
})

describe('SuppliersPage — create flow', () => {
  it('does not submit when the name is empty (native required blocks it)', async () => {
    const user = userEvent.setup()
    render(<SuppliersPage initialSuppliers={[]} />)
    await user.click(screen.getByText('+ New supplier'))
    const nameInput = screen.getByLabelText('Name')
    expect(nameInput).toBeRequired()
    await user.click(screen.getByRole('button', { name: 'Create supplier' }))
    expect(mockedFetch).not.toHaveBeenCalled()
    expect(screen.getByRole('dialog')).toBeInTheDocument()
  })

  it('POSTs the new supplier and appends it to the list', async () => {
    const user = userEvent.setup()
    mockedFetch.mockResolvedValue(supplier({ id: 3, name: 'Cedar Supplies' }))
    render(<SuppliersPage initialSuppliers={[]} />)
    await user.click(screen.getByText('+ New supplier'))
    await user.type(screen.getByLabelText('Name'), 'Cedar Supplies')
    await user.click(screen.getByRole('button', { name: 'Create supplier' }))

    await waitFor(() =>
      expect(mockedFetch).toHaveBeenCalledWith('/api/suppliers/', {
        method: 'POST',
        body: JSON.stringify({
          name: 'Cedar Supplies',
          contact_name: '',
          email: '',
          phone: '',
          address: '',
          notes: '',
          active: true,
        }),
      }),
    )
    expect(await screen.findByText('Cedar Supplies')).toBeInTheDocument()
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('surfaces a server error inside the modal and keeps it open', async () => {
    const user = userEvent.setup()
    mockedFetch.mockRejectedValue(new ApiError('Name already exists', 400))
    render(<SuppliersPage initialSuppliers={[]} />)
    await user.click(screen.getByText('+ New supplier'))
    await user.type(screen.getByLabelText('Name'), 'Acme Foods')
    await user.click(screen.getByRole('button', { name: 'Create supplier' }))

    const dialog = screen.getByRole('dialog')
    expect(await within(dialog).findByRole('alert')).toHaveTextContent(
      'Name already exists',
    )
    expect(screen.getByRole('dialog')).toBeInTheDocument()
  })

  it('disables the submit button while the request is in flight', async () => {
    const user = userEvent.setup()
    mockedFetch.mockReturnValue(new Promise(() => {}))
    render(<SuppliersPage initialSuppliers={[]} />)
    await user.click(screen.getByText('+ New supplier'))
    await user.type(screen.getByLabelText('Name'), 'Slow Supplier')
    await user.click(screen.getByRole('button', { name: 'Create supplier' }))
    const submit = screen.getByRole('button', { name: /Saving\.\.\./ })
    expect(submit).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Cancel' })).toBeDisabled()
  })
})

describe('SuppliersPage — edit flow', () => {
  it('prefills the modal with the supplier and PATCHes on save', async () => {
    const user = userEvent.setup()
    mockedFetch.mockResolvedValue(supplier({ contact_name: 'Janet Doe' }))
    render(<SuppliersPage initialSuppliers={SEED} />)
    await user.click(screen.getByLabelText('Edit Acme Foods'))

    const dialog = screen.getByRole('dialog')
    expect(within(dialog).getByLabelText('Name')).toHaveValue('Acme Foods')
    expect(within(dialog).getByLabelText('Contact name')).toHaveValue('Jane Doe')

    await user.clear(within(dialog).getByLabelText('Contact name'))
    await user.type(within(dialog).getByLabelText('Contact name'), 'Janet Doe')
    await user.click(screen.getByRole('button', { name: 'Save changes' }))

    await waitFor(() =>
      expect(mockedFetch).toHaveBeenCalledWith('/api/suppliers/1/', {
        method: 'PATCH',
        body: JSON.stringify({
          name: 'Acme Foods',
          contact_name: 'Janet Doe',
          email: 'jane@acme.test',
          phone: '555-0100',
          address: '1 Main St',
          notes: '',
          active: true,
        }),
      }),
    )
    expect(await screen.findByText('Janet Doe')).toBeInTheDocument()
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('surfaces a PATCH server error inside the modal', async () => {
    const user = userEvent.setup()
    mockedFetch.mockRejectedValue(new ApiError('Invalid email', 400))
    render(<SuppliersPage initialSuppliers={SEED} />)
    await user.click(screen.getByLabelText('Edit Acme Foods'))
    await user.click(screen.getByRole('button', { name: 'Save changes' }))

    const dialog = screen.getByRole('dialog')
    expect(await within(dialog).findByRole('alert')).toHaveTextContent(
      'Invalid email',
    )
  })
})

describe('SuppliersPage — delete flow', () => {
  it('sends DELETE and removes the row after confirmation', async () => {
    const user = userEvent.setup()
    mockedFetch.mockResolvedValue(undefined)
    render(<SuppliersPage initialSuppliers={SEED} />)
    await user.click(screen.getByLabelText('Delete Acme Foods'))

    const confirm = screen.getByRole('alertdialog')
    expect(confirm).toHaveTextContent('Delete Acme Foods?')
    await user.click(
      within(confirm).getByRole('button', { name: 'Delete supplier' }),
    )

    await waitFor(() =>
      expect(mockedFetch).toHaveBeenCalledWith('/api/suppliers/1/', {
        method: 'DELETE',
      }),
    )
    await waitFor(() =>
      expect(screen.queryByText('Acme Foods')).not.toBeInTheDocument(),
    )
    expect(screen.getByText('Beacon Drinks')).toBeInTheDocument()
  })

  it('keeps the row and shows the error when DELETE fails', async () => {
    const user = userEvent.setup()
    mockedFetch.mockRejectedValue(new ApiError('Linked products exist', 409))
    render(<SuppliersPage initialSuppliers={SEED} />)
    await user.click(screen.getByLabelText('Delete Acme Foods'))
    await user.click(
      within(screen.getByRole('alertdialog')).getByRole('button', {
        name: 'Delete supplier',
      }),
    )

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Linked products exist',
    )
    expect(screen.getAllByText('Acme Foods').length).toBeGreaterThan(0)
  })

  it('does not call the API when delete is cancelled', async () => {
    const user = userEvent.setup()
    render(<SuppliersPage initialSuppliers={SEED} />)
    await user.click(screen.getByLabelText('Delete Acme Foods'))
    await user.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument()
    expect(mockedFetch).not.toHaveBeenCalled()
    expect(screen.getByText('Acme Foods')).toBeInTheDocument()
  })
})
