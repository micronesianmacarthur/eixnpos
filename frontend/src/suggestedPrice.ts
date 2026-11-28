/**
 * Suggested price calculation for the product form.
 *
 * The configured default target margin (Settings → Pricing, stored by the
 * backend at `GET /api/settings/pricing/`) is a gross margin percentage of the
 * selling price:
 *
 *     suggested_price = unit_cost / (1 - margin_percent / 100)
 *
 * A margin of 100% would divide by zero, so margins `>= 100` are treated as
 * unusable and fall back to {@link DEFAULT_TARGET_MARGIN_PERCENT}.
 *
 * Prices are rounded to the app's currency minor unit (two decimal places).
 * The margin is advisory: a missing, malformed, or out-of-range value falls
 * back to {@link DEFAULT_TARGET_MARGIN_PERCENT} rather than blocking the form.
 */

/** Fallback target margin, in percent, when the configured value is unusable. */
export const DEFAULT_TARGET_MARGIN_PERCENT = 15;

/** Sub-cent tolerance so binary float noise cannot round a half-cent down. */
const ROUNDING_EPSILON = 1e-9;

/**
 * Coerce the `default_target_margin_percent` value returned by the settings
 * API into a usable margin percentage.
 *
 * Accepts a JSON number or a numeric string (defensive: older or proxied
 * responses may stringify it). Anything else — missing, blank, non-numeric,
 * NaN/Infinity, negative, or 100 and above — falls back to the default margin.
 * A gross margin of 100% would divide by zero, so it is not usable here even
 * though the settings API accepts it as a storable percentage.
 */
export function resolveTargetMarginPercent(raw: unknown): number {
  let parsed: number;
  if (typeof raw === 'number') {
    parsed = raw;
  } else if (typeof raw === 'string' && raw.trim() !== '') {
    parsed = Number(raw);
  } else {
    return DEFAULT_TARGET_MARGIN_PERCENT;
  }

  if (!Number.isFinite(parsed) || parsed < 0 || parsed >= 100) {
    return DEFAULT_TARGET_MARGIN_PERCENT;
  }
  return parsed;
}

/**
 * Round a monetary amount to cents (two decimal places), half away from zero.
 * The epsilon absorbs float noise such as `1.265 * 100 === 126.49999999999999`.
 */
function roundToCents(value: number): number {
  const cents = Math.abs(value) * 100;
  const rounded = Math.round(cents + ROUNDING_EPSILON) / 100;
  return value < 0 ? -rounded : rounded;
}

/**
 * Compute the suggested price for a unit cost at a gross margin.
 *
 * Returns a fixed two-decimal string (e.g. `"117.65"` for a cost of `"100"` at
 * the 15% default margin) so the form field shows the currency minor unit.
 * Returns an empty string when the cost is empty or not a non-negative finite
 * number — there is nothing to calculate.
 */
export function computeSuggestedPrice(
  unitCost: string | number | null | undefined,
  marginPercent: number,
): string {
  if (unitCost === null || unitCost === undefined) return '';

  const text = typeof unitCost === 'string' ? unitCost.trim() : String(unitCost);
  if (text === '') return '';

  const cost = Number(text);
  if (!Number.isFinite(cost) || cost < 0) return '';

  const margin = resolveTargetMarginPercent(marginPercent);
  const price = cost / (1 - margin / 100);
  // The resolver keeps the margin below 100, so `price` is finite for any
  // finite cost. The guard keeps a pathological cost — or a future change to
  // the resolver — from rendering "Infinity" in a read-only field.
  if (!Number.isFinite(price)) return '';
  return roundToCents(price).toFixed(2);
}
