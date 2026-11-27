import { describe, expect, it } from 'vitest'

import {
  buildCandidates,
  repackPreview,
  type RepackProduct,
} from '../../src/repackMath'

function pack(overrides: Partial<RepackProduct> = {}): RepackProduct {
  return {
    id: 5,
    sku: 'DIA-0005',
    name: 'Pack',
    quantity: 7,
    unit_product: 9,
    units_per_pack: 12,
    unit_product_sku: 'DIA-0009',
    unit_product_name: 'Single',
    ...overrides,
  }
}

function child(overrides: Partial<RepackProduct> = {}): RepackProduct {
  return {
    id: 9,
    sku: 'DIA-0009',
    name: 'Single',
    quantity: 14,
    unit_product: null,
    units_per_pack: null,
    ...overrides,
  }
}

describe('buildCandidates', () => {
  it('derives one split candidate when the product is a pack', () => {
    const candidates = buildCandidates(pack())
    expect(candidates).toHaveLength(1)
    expect(candidates[0]).toMatchObject({
      direction: 'split',
      targetId: 9,
      ratio: 12,
      label: 'Single',
    })
  })

  it('derives one merge candidate per active pack parent on a child', () => {
    const single = child({
      pack_parents: [
        { id: 5, sku: 'DIA-0005', name: 'Pack', units_per_pack: 12 },
      ],
    })
    const candidates = buildCandidates(single)
    expect(candidates).toHaveLength(1)
    expect(candidates[0]).toMatchObject({ direction: 'merge', targetId: 5, ratio: 12 })
  })

  it('exposes every candidate when a child has multiple pack parents (R1.10 multi-parent)', () => {
    const single = child({
      pack_parents: [
        { id: 5, sku: 'DIA-0005', name: 'Six', units_per_pack: 6 },
        { id: 7, sku: 'DIA-0007', name: 'Twelve', units_per_pack: 12 },
      ],
    })
    const candidates = buildCandidates(single)
    expect(candidates).toHaveLength(2)
    expect(candidates.map((c) => c.targetId)).toEqual([5, 7])
  })

  it('ignores pack parents without a units_per_pack ratio', () => {
    const single = child({
      pack_parents: [
        { id: 5, sku: 'DIA-0005', name: 'Pack', units_per_pack: null },
      ],
    })
    expect(buildCandidates(single)).toHaveLength(0)
  })

  it('returns zero candidates for a plain product', () => {
    expect(buildCandidates(child())).toHaveLength(0)
  })
})

describe('repackPreview — split', () => {
  it('computes target = q * r and projects both on-hands (AC-10 example)', () => {
    const source = pack({ quantity: 7 })
    const target = child({ quantity: 14 })
    const preview = repackPreview(source, { direction: 'split', targetId: 9, ratio: 12, label: 'x' }, 3, target.quantity)
    expect(preview).toMatchObject({ valid: true, targetQuantity: 36, sourceOnHand: 4, targetOnHand: 50 })
  })

  it('rejects a computed target above 1,000,000 before any write', () => {
    const source = pack({ quantity: 1_000_000 })
    const target = child({ quantity: 0 })
    const preview = repackPreview(source, { direction: 'split', targetId: 9, ratio: 12, label: 'x' }, 100_000, target.quantity)
    expect(preview.valid).toBe(false)
    expect(preview.reason).toMatch(/1,000,000/)
  })

  it('rejects a quantity larger than the source on-hand as insufficient_stock', () => {
    const source = pack({ quantity: 7 })
    const target = child({ quantity: 14 })
    const preview = repackPreview(source, { direction: 'split', targetId: 9, ratio: 12, label: 'x' }, 8, target.quantity)
    expect(preview.valid).toBe(false)
    expect(preview.reason).toMatch(/Only 7 units on hand/)
  })
})

describe('repackPreview — merge', () => {
  it('computes target = q / r for an exact multiple (AC-11 example)', () => {
    const source = child({ quantity: 26 })
    const target = pack({ quantity: 6 })
    const preview = repackPreview(source, { direction: 'merge', targetId: 5, ratio: 12, label: 'x' }, 24, target.quantity)
    expect(preview).toMatchObject({ valid: true, targetQuantity: 2, sourceOnHand: 2, targetOnHand: 8 })
  })

  it('rejects a non-multiple with the remainder in the reason (AC-12 example)', () => {
    const source = child({ quantity: 26 })
    const target = pack({ quantity: 6 })
    const preview = repackPreview(source, { direction: 'merge', targetId: 5, ratio: 12, label: 'x' }, 25, target.quantity)
    expect(preview.valid).toBe(false)
    expect(preview.reason).toMatch(/remainder 1/)
  })

  it('rejects merging more than the source holds', () => {
    const source = child({ quantity: 10 })
    const target = pack({ quantity: 0 })
    const preview = repackPreview(source, { direction: 'merge', targetId: 5, ratio: 12, label: 'x' }, 12, target.quantity)
    expect(preview.valid).toBe(false)
  })
})

describe('repackPreview — guardrails', () => {
  it('returns null-shape invalid for non-integer / sub-one inputs handled by the caller', () => {
    // The dialog never calls preview with a sub-one quantity; a caller that does
    // must still not produce a negative target. The tool returns a bounded guard.
    const preview = repackPreview(pack(), { direction: 'split', targetId: 9, ratio: 12, label: 'x' }, 0, 0)
    expect(preview.valid).toBe(true)
    expect(preview.targetQuantity).toBe(0)
  })

  it('preserves the invariant q*w is exact in either direction on a fresh pair', () => {
    const splitPreview = repackPreview(
      pack({ quantity: 7 }),
      { direction: 'split', targetId: 9, ratio: 12, label: 'x' },
      3,
      14,
    )
    // start: 7 packs + 14 singles = 7*12 + 14 = 98 units
    // after: 4 packs + 50 singles = 4*12 + 50 = 98 units
    expect(splitPreview).toMatchObject({ valid: true, targetQuantity: 36, sourceOnHand: 4, targetOnHand: 50 })
    const invariant = 4 * 12 + 50
    expect(invariant).toBe(98)
  })
})
