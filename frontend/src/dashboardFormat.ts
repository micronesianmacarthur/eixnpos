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
 * Format an ISO datetime string into a readable wall-clock display.
 *
 * The server sends datetimes already expressed in the store's business-local
 * timezone with the UTC offset embedded (e.g. `2026-11-24T07:00:00+11:00`).
 * We format using that embedded offset, so the session's "opened when" always
 * shows the store's own clock independent of the viewer's browser timezone
 * and independent of the CI/test machine's TZ. Whole-hour offsets map to an
 * `Etc/GMT` zone; anything unusual falls back to the environment default.
 */
export function formatDateTime(iso: string | undefined): string {
  if (!iso) return '';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso; // pass through unparseable text
  const zone = offsetToEtcZone(iso);
  if (zone) {
    return new Intl.DateTimeFormat('en-US', {
      dateStyle: 'medium',
      timeStyle: 'short',
      timeZone: zone,
    }).format(date);
  }
  return DEFAULT_DATE_FORMATTER.format(date);
}
