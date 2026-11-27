/**
 * Pack-mapping payload resolution (S3 / R1.1) for the product create/update
 * form. Kept out of ProductPage.tsx so the component file stays
 * component-only (clean fast-refresh, no mixed exports).
 *
 * The pack mapping (``unit_product`` / ``units_per_pack``) is manager/admin-only
 * in the UI; the API still enforces it. Because edits use PATCH, clearing a
 * mapping ("— Not a pack —") would silently keep the old value unless we send an
 * explicit ``null`` for both keys. A non-manager/admin caller — who is not
 * allowed to touch the mapping at all — gets its payload returned unchanged.
 */

export interface MappingForm {
  unit_product: string;
  units_per_pack: string;
}

export type WithOptionalMapping = {
  unit_product?: unknown;
  units_per_pack?: unknown;
};

export function resolveMappingPayload<P extends WithOptionalMapping>(
  payload: P,
  form: MappingForm,
  isManagerOrAdmin: boolean,
): P {
  if (!isManagerOrAdmin) {
    return payload;
  }
  if (form.unit_product.trim() !== '') {
    payload.unit_product = parseInt(form.unit_product, 10);
    payload.units_per_pack = parseInt(form.units_per_pack, 10);
  } else {
    payload.unit_product = null;
    payload.units_per_pack = null;
  }
  return payload;
}
