// @vitest-environment jsdom
/**
 * Component tests for the Repack dialog's multi-parent selection contract
 * (B2 / R1.10 / AC-44).
 *
 * A child product with two pack parents (6/pack and 12/pack) must NOT let the
 * operator confirm a repack until they have chosen a target — the old fallback
 * silently sent the POST to the first parent. These tests pin: Confirm is
 * disabled with two candidates and no selection, is enabled once a real
 * selection is made, and the POST body's ``target_product`` matches the chosen
 * candidate.
 */
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import '@testing-library/jest-dom/vitest'

import { apiFetch } from '../../src/api'
import RepackDialog from '../../src/RepackDialog'
import type { RepackProduct } from '../../src/repackMath'

vi.mock('../../src/api', () => ({ apiFetch: vi.fn() }))
const mockedFetch = vi.mocked(apiFetch)

function child(overrides: Partial<RepackProduct> = {}): RepackProduct {
  return {
    id: 9,
    sku: 'DIA-0009',
    name: 'Single',
    quantity: 26,
    unit_product: null,
    units_per_pack: null,
    pack_parents: [
      { id: 5, sku: 'DIA-0005', name: 'Six', units_per_pack: 6 },
      { id: 7, sku: 'DIA-0007', name: 'Twelve', units_per_pack: 12 },
    ],
    ...overrides,
  }
}

function productsById(): Map<number, RepackProduct> {
  const m = new Map<number, RepackProduct>()
  m.set(9, child())
  m.set(5, {
    id: 5, sku: 'DIA-0005', name: 'Six', quantity: 10,
    unit_product: 9, units_per_pack: 6,
  })
  m.set(7, {
    id: 7, sku: 'DIA-0007', name: 'Twelve', quantity: 3,
    unit_product: 9, units_per_pack: 12,
  })
  return m
}

function renderDialog(onSuccess = vi.fn()) {
  const onClose = vi.fn()
  render(
    <RepackDialog
      product={child()}
      productsById={productsById()}
      onClose={onClose}
      onSuccess={onSuccess}
    />,
  )
  return { onClose, onSuccess }
}

async function typeQty(value: string) {
  await userEvent.type(screen.getByLabelText('Source quantity'), value)
}

async function chooseTarget(value: string) {
  await userEvent.selectOptions(screen.getByLabelText('Target pack'), value)
}

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('RepackDialog multi-parent selection (B2 / AC-44)', () => {
  it('keeps Confirm disabled with two candidates and no selection', async () => {
    renderDialog()
    // Two pack parents → the "— Select target —" chooser is shown.
    expect(screen.getByLabelText('Target pack')).toBeInTheDocument()
    const confirm = screen.getByRole('button', { name: 'Confirm repack' })
    expect(confirm).toBeDisabled()
    // Typing a quantity alone must not enable Confirm.
    await typeQty('12')
    expect(screen.getByRole('button', { name: 'Confirm repack' })).toBeDisabled()
    expect(mockedFetch).not.toHaveBeenCalled()
  })

  it('enables Confirm after a real selection and POSTs the chosen candidate', async () => {
    const { onSuccess } = renderDialog()
    await typeQty('12')
    await chooseTarget('7') // the <option value="7">Twelve…</option>
    const confirm = screen.getByRole('button', { name: 'Confirm repack' })
    expect(confirm).toBeEnabled()

    mockedFetch.mockResolvedValue({ reference: 'RP-XYZ' })
    await userEvent.click(confirm)

    await waitFor(() => expect(onSuccess).toHaveBeenCalledWith('RP-XYZ'))
    expect(mockedFetch).toHaveBeenCalledTimes(1)
    const [path, init] = mockedFetch.mock.calls[0]
    expect(path).toBe('/api/inventory/repack/')
    const body = JSON.parse(init.body as string)
    // Must hit the *chosen* parent (Twelve, id 7), never the first (Six, id 5).
    expect(body.target_product).toBe(7)
    expect(body.source_product).toBe(9)
    expect(body.source_quantity).toBe(12)
  })

  it('keeps auto-select only for the single-candidate case', async () => {
    const single = child({
      pack_parents: [{ id: 5, sku: 'DIA-0005', name: 'Six', units_per_pack: 6 }],
    })
    const onSuccess = vi.fn()
    render(
      <RepackDialog
        product={single}
        productsById={productsById()}
        onClose={vi.fn()}
        onSuccess={onSuccess}
      />,
    )
    // Single candidate → no chooser; a valid quantity pre-enables Confirm.
    expect(screen.queryByLabelText('Target pack')).not.toBeInTheDocument()
    await typeQty('6')
    expect(screen.getByRole('button', { name: 'Confirm repack' })).toBeEnabled()
    mockedFetch.mockResolvedValue({ reference: 'RP-SINGLE' })
    await userEvent.click(screen.getByRole('button', { name: 'Confirm repack' }))
    await waitFor(() => expect(onSuccess).toHaveBeenCalledWith('RP-SINGLE'))
    const body = JSON.parse(mockedFetch.mock.calls[0][1].body as string)
    expect(body.target_product).toBe(5)
  })
})
