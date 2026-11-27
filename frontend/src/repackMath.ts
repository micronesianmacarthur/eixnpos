// Pure repack preview arithmetic, unit-testable without React.
//
// A repack converts stock between a *pack* product (parent) and its unit
// product (child), linked by ``units_per_pack`` (the whole-unit ratio).
// Direction and ratio come from the stored mapping, never from the operator:
//   split (pack -> units): target = q * r
//   merge (units -> pack): target must be an exact multiple; target = q / r

export interface PackCandidate {
  /** 'split' means this product is a pack producing its unit_product. */
  direction: 'split' | 'merge';
  /** Id of the product on the *other side* of the repack. */
  targetId: number;
  ratio: number;
  label: string;
}

export interface RepackProduct {
  id: number;
  sku: string | null;
  name: string;
  quantity: number;
  unit_product: number | null;
  units_per_pack: number | null;
  unit_product_sku?: string | null;
  unit_product_name?: string | null;
  pack_parents?: PackParent[];
}

export interface PackParent {
  id: number;
  sku: string | null;
  name: string;
  units_per_pack: number | null;
}

/** Derive the possible repack candidates for a viewed product. */
export function buildCandidates(
  product: RepackProduct,
): PackCandidate[] {
  const candidates: PackCandidate[] = [];
  // This product IS a pack: split into its unit product.
  if (product.unit_product != null && product.units_per_pack != null) {
    candidates.push({
      direction: 'split',
      targetId: product.unit_product,
      ratio: product.units_per_pack,
      label: product.unit_product_name || product.unit_product_sku || `#${product.unit_product}`,
    });
  }
  // This product is a child of one or more packs: merge back into a chosen parent.
  for (const p of product.pack_parents || []) {
    if (p.units_per_pack != null) {
      candidates.push({
        direction: 'merge',
        targetId: p.id,
        ratio: p.units_per_pack,
        label: p.name || p.sku || `#${p.id}`,
      });
    }
  }
  return candidates;
}

export interface RepackPreview {
  valid: boolean;
  reason?: string;
  targetQuantity: number | null;
  sourceOnHand: number | null;
  targetOnHand: number | null;
}

/**
 * Compute the preview for a candidate and quantity.
 * ``quantity`` is the already-parsed whole number entered by the operator.
 * Returns on-hand projections using the loaded product quantities.
 */
export function repackPreview(
  source: RepackProduct,
  candidate: PackCandidate,
  quantity: number,
  targetQuantityOfTarget: number,
): RepackPreview {
  const ratio = candidate.ratio;
  let targetQuantity: number | null;

  if (candidate.direction === 'split') {
    targetQuantity = quantity * ratio;
    if (targetQuantity > 1_000_000) {
      return { valid: false, reason: 'Computed target exceeds 1,000,000.', targetQuantity: null, sourceOnHand: null, targetOnHand: null };
    }
  } else {
    if (quantity % ratio !== 0) {
      return {
        valid: false,
        reason: `${quantity} is not a multiple of ${ratio} (remainder ${quantity % ratio}).`,
        targetQuantity: null,
        sourceOnHand: null,
        targetOnHand: null,
      };
    }
    targetQuantity = Math.floor(quantity / ratio);
  }

  const sourceOnHand = source.quantity - quantity;
  if (sourceOnHand < 0) {
    return {
      valid: false,
      reason: `Only ${source.quantity} units on hand.`,
      targetQuantity: null,
      sourceOnHand: null,
      targetOnHand: null,
    };
  }

  return {
    valid: true,
    targetQuantity,
    sourceOnHand,
    targetOnHand: targetQuantityOfTarget + targetQuantity,
  };
}
