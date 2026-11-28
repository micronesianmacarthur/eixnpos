/**
 * Pricing settings shared with the Settings > Pricing tab.
 *
 * The backend stores a single default target margin percentage and returns it as
 * a JSON number (see `backend/business/services.py`). This module owns the
 * client-side parsing/formatting so the page and its tests agree on the rules:
 * a number between 0 and 100 inclusive, with at most two fraction digits. The
 * backend enforces the same bounds; the client check exists to block an invalid
 * value before it is sent.
 */

/** Margin used by a fresh install and whenever no valid value is available. */
export const DEFAULT_TARGET_MARGIN_PERCENT = 15;

export const MIN_TARGET_MARGIN_PERCENT = 0;
export const MAX_TARGET_MARGIN_PERCENT = 100;
export const TARGET_MARGIN_DECIMAL_PLACES = 2;

export const MARGIN_RANGE_ERROR = `Enter a number between ${MIN_TARGET_MARGIN_PERCENT} and ${MAX_TARGET_MARGIN_PERCENT}.`;
export const MARGIN_DECIMALS_ERROR = `Enter a percentage with at most ${TARGET_MARGIN_DECIMAL_PLACES} decimal places.`;

/** Shape returned by GET/PATCH /api/settings/pricing/. */
export interface PricingSettings {
  default_target_margin_percent: number;
}

export type MarginParseResult =
  | { ok: true; value: number }
  | { ok: false; error: string };

/** Plain decimal literal — no exponents, no hex, no underscores. */
const DECIMAL_LITERAL = /^[+-]?(?:\d+(?:\.\d*)?|\.\d+)$/;

function fractionDigits(text: string): number {
  const dot = text.indexOf('.');
  if (dot === -1) return 0;
  // Trailing zeros do not add precision: 15.500 is the same as 15.5.
  return text.slice(dot + 1).replace(/0+$/, '').length;
}

/** Validate a raw input value, returning the number to send or a user message. */
export function parseTargetMarginPercent(raw: string): MarginParseResult {
  const text = raw.trim();
  if (!text || !DECIMAL_LITERAL.test(text)) {
    return { ok: false, error: MARGIN_RANGE_ERROR };
  }
  const value = Number(text);
  if (!Number.isFinite(value) || value < MIN_TARGET_MARGIN_PERCENT || value > MAX_TARGET_MARGIN_PERCENT) {
    return { ok: false, error: MARGIN_RANGE_ERROR };
  }
  if (fractionDigits(text) > TARGET_MARGIN_DECIMAL_PLACES) {
    return { ok: false, error: MARGIN_DECIMALS_ERROR };
  }
  return { ok: true, value };
}

/** Render a margin for the input, without trailing zeros. */
export function formatTargetMarginPercent(value: number): string {
  return String(value);
}

/**
 * Read the margin out of an API payload, falling back to the default whenever
 * the payload is missing the field or carries an unusable value.
 */
export function targetMarginFromSettings(data: unknown): number {
  if (data && typeof data === 'object') {
    const raw = (data as Record<string, unknown>).default_target_margin_percent;
    if (typeof raw === 'number' && Number.isFinite(raw)) {
      const parsed = parseTargetMarginPercent(String(raw));
      if (parsed.ok) return parsed.value;
    }
    if (typeof raw === 'string') {
      const parsed = parseTargetMarginPercent(raw);
      if (parsed.ok) return parsed.value;
    }
  }
  return DEFAULT_TARGET_MARGIN_PERCENT;
}
