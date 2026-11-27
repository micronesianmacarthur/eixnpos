/**
 * Unit tests for the ProductPage pack-mapping payload (S3 / R1.1).
 *
 * Clearing a pack mapping ("— Not a pack —") must send explicit ``null`` for
 * ``unit_product`` and ``units_per_pack`` so a PATCH does not silently keep the
 * old mapping. A non-manager/admin caller must not touch the payload at all.
 */
import { describe, expect, it } from 'vitest'

import { resolveMappingPayload } from '../../src/productMappingPayload'

function base() {
  return { name: 'Widget', price: '10' } as Record<string, unknown>
}
function form(unit_product: string, units_per_pack: string) {
  return { unit_product, units_per_pack }
}

describe('resolveMappingPayload (S3 pack-mapping clear)', () => {
  it('sends parsed numbers when a manager sets a mapping', () => {
    const payload = resolveMappingPayload(base(), form('9', '12'), true)
    expect(payload.unit_product).toBe(9)
    expect(payload.units_per_pack).toBe(12)
  })

  it('sends explicit nulls when a manager clears the mapping', () => {
    const payload = resolveMappingPayload(base(), form('', ''), true)
    // The critical S3 assertion: a cleared mapping emits null, not omission.
    expect(payload).toHaveProperty('unit_product', null)
    expect(payload).toHaveProperty('units_per_pack', null)
  })

  it('leaves the payload untouched for a non-manager/admin user', () => {
    const payload = resolveMappingPayload(base(), form('9', '12'), false)
    expect(payload).not.toHaveProperty('unit_product')
    expect(payload).not.toHaveProperty('units_per_pack')
    // A cashier clearing the field still must not clear the mapping API-side.
    const cleared = resolveMappingPayload(base(), form('', ''), false)
    expect(cleared).not.toHaveProperty('unit_product')
    expect(cleared).not.toHaveProperty('units_per_pack')
  })
})
