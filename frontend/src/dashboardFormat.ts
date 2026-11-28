/**
 * Currency + date formatting for the manager dashboard.
 *
 * Money arrives from the server as a 2-dp decimal *string*. Per the contract
 * the client must never re-parse a total into a JS `Number` for arithmetic;
 * here we only *format* it for display with `Intl.NumberFormat`, so a value
 * like "1234.56" renders as "$1,234.56" without ever losing precision.
 */

const moneyFormatter = new Intl.NumberFormat('en-US', {
  style: 'currency',
  currency: 'USD',
});

/** Parse a 2-dp money string to a finite number solely for display rounding. */
function toDisplayNumber(value: string): number {
  const parsed = Number(value ?? 0);
  return Number.isFinite(parsed) ? parsed : 0;
}

/** Format a 2-dp money string as USD, e.g. "1234.56" → "$1,234.56". */
export function formatMoney(value: string): string {
  return moneyFormatter.format(toDisplayNumber(value));
}

/**
 * Convert the UTC offset embedded in an ISO string (e.g. `+11:00`) to a
 * `Etc/GMT` IANA zone so `Intl` formats in that offset. `Etc/GMT` signs are
 * inverted (UTC+11 is `Etc/GMT-11`). Non-whole-hour offsets (e.g. +05:45)
 * aren't representable as a whole-hour `Etc/GMT` zone, so we return null and
 * the caller falls back to the environment's default zone.
 */
function offsetToEtcZone(iso: string): string | null {
  const match = /[+-](\d{2}):(\d{2})$/.exec(iso);
  if (!match) return null;
  const hours = Number(match[1]);
  const minutes = Number(match[2]);
  if (minutes !== 0) return null;
  const inverted = hours === 0 ? 0 : -hours;
  const sign = inverted >= 0 ? '+' : '-';
  return `Etc/GMT${sign}${Math.abs(inverted)}`;
}

const DEFAULT_DATE_FORMATTER = new Intl.DateTimeFormat('en-US', {
  dateStyle: 'medium',
  timeStyle: 'short',
});

/**
 * Whole-day distance between two `YYYY-MM-DD` business-day strings
 * (`to - from`). Both are calendar dates with no time/timezone component, so
 * the comparison is anchored at UTC midnight to stay DST-independent.
 * Returns 0 when either value is missing or unparseable.
 */
export function businessDayDifference(from: string, to: string): number {
  const fromMs = Date.parse(`${from}T00:00:00Z`);
  const toMs = Date.parse(`${to}T00:00:00Z`);
  if (!Number.isFinite(fromMs) || !Number.isFinite(toMs)) return 0;
  return Math.round((toMs - fromMs) / 86_400_000);
}

/** Human label for a non-negative day count: 1 → "1 day", 3 → "3 days". */
export function daysLabel(days: number): string {
  const safe = Number.isFinite(days) ? Math.max(0, Math.trunc(days)) : 0;
  return `${safe} ${safe === 1 ? 'day' : 'days'}`;
}

/**
 * Format an ISO datetime string into a readable wall-clock display.
 *
 * The server sends datetimes already expressed in the store's business-local
 * timezone with the UTC offset embedded (e.g. `2026-11-24T07:00:00+11:00`).
 * When the business IANA zone name (`business_timezone`, contract §3.2) is
 * provided we pass it to `Intl.DateTimeFormat` so the store clock is shown
 * regardless of the viewer's browser timezone AND regardless of whole-hour vs
 * fractional UTC offsets (+05:30, +09:30 — which `Etc/GMT` can't express).
 * Without a zone we fall back to the embedded offset via `Etc/GMT` (whole-hour
 * offsets only), then the environment default.
 */
export function formatDateTime(iso: string | undefined, businessTimezone?: string | null): string {
  if (!iso) return '';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso; // pass through unparseable text
  const zone = businessTimezone?.trim() || offsetToEtcZone(iso);
  if (zone) {
    return new Intl.DateTimeFormat('en-US', {
      dateStyle: 'medium',
      timeStyle: 'short',
      timeZone: zone,
    }).format(date);
  }
  return DEFAULT_DATE_FORMATTER.format(date);
}
