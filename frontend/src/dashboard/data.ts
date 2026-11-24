/**
 * Shared types, formatters, and API helpers for the dashboard analytics
 * panels (top items, monthly performance chart, low stock).
 *
 * Shapes mirror the dashboard data contract (research/dashboard-metrics-and-
 * data-contract.md §5) and the aggregation service (backend/dashboard/*).
 * Money arrives as 2-dp decimal strings and is formatted on display only —
 * the client never does float arithmetic on it (contract §3.1).
 */
import { apiFetch } from '../api';

/* ----------------------------------------------------------------------- */
/* — Payload types (verbatim from the server contract) —                  */
/* ----------------------------------------------------------------------- */

/** One entry in /api/dashboard/sales/top/ ($5.3). */
export interface TopItem {
  rank: number;
  product_id: number;
  product_name: string;
  units_sold: number;
  revenue: string; // 2-dp money string
  avg_unit_price: string; // 2-dp money string
}

export interface TopItemsPayload {
  scope: 'today' | 'week' | 'month' | 'range';
  total_items: number;
  top_items: TopItem[];
}

/** One month in /api/dashboard/sales/monthly/ ($5.4). */
export interface MonthlyPeriod {
  period: string; // "YYYY-MM"
  label: string; // "Mon YYYY"
  sale_count: number;
  revenue: string; // 2-dp money string
  cash_sales: string; // 2-dp money string
  avg_basket: string; // 2-dp money string
  mom_revenue: string | null; // null for first & in-progress periods
  mom_revenue_pct: number | null; // % change, or null
}

export interface MonthlyPayload {
  months_requested: number;
  periods: MonthlyPeriod[];
  latest_period: string | null;
}

/** One item in /api/dashboard/inventory/low-stock/ ($5.6). */
export interface LowStockItem {
  product_id: number;
  product_name: string;
  sku?: string | null;
  quantity_on_hand: number;
  min_stock: number;
  category?: string | null;
}

export interface LowStockPayload {
  generated_at: string;
  threshold_source: {
    field: 'min_stock';
    rule: string;
    non_stock_excluded: boolean;
  };
  low_stock_count: number;
  items: LowStockItem[];
}

/** Named scopes offered by the Top Items selector. */
export const TOP_SCOPES = ['today', 'week', 'month'] as const;
export type TopScope = (typeof TOP_SCOPES)[number];

export const TOP_SCOPE_LABELS: Record<TopScope, string> = {
  today: 'Today',
  week: 'Week',
  month: 'Month',
};

/** Plain-language range description per scope. */
export const TOP_SCOPE_HINTS: Record<TopScope, string> = {
  today: 'sales created since midnight, business time',
  week: 'the current Monday–Sunday week',
  month: 'the current calendar month',
};

/* ----------------------------------------------------------------------- */
/* — Formatting helpers (display-only; never compute with money)          */
/* ----------------------------------------------------------------------- */

const currency = new Intl.NumberFormat('en-US', {
  style: 'currency',
  currency: 'USD',
});

/** Parse a 2-dp money string (or number) for display formatting. */
function moneyToNumber(value: string | number | null | undefined): number {
  if (value === null || value === undefined) return 0;
  const parsed = typeof value === 'number' ? value : Number(value);
  return Number.isFinite(parsed) ? parsed : 0;
}

/** Format a 2-dp money string -> "$1,234.56". Clients never round or add. */
export function formatMoney(value: string | number | null | undefined): string {
  return currency.format(moneyToNumber(value));
}

export type MomTone = 'up' | 'down' | 'flat';

/** Classify a month-over-month % change for icon/badge tone. */
export function momTone(pct: number | null): MomTone {
  if (pct === null) return 'flat';
  if (pct > 0) return 'up';
  if (pct < 0) return 'down';
  return 'flat';
}

/**
 * Human label for a mom % change: "+13.16%", "-25.58%", or "—" when the
 * server couldn't compare (no previous month, or previous revenue was 0).
 */
export function formatMomPct(pct: number | null): string {
  if (pct === null) return '—';
  const sign = pct > 0 ? '+' : '';
  return `${sign}${pct.toFixed(2)}%`;
}

/** Better-matching down arrow: signed money for the absolute mom delta. */
export function formatMomDelta(momRevenue: string | null): string {
  if (momRevenue === null) return '—';
  const value = moneyToNumber(momRevenue);
  if (value > 0) return `+${formatMoney(value)}`;
  if (value < 0) return `-${formatMoney(Math.abs(value))}`;
  return formatMoney(0);
}

/* ----------------------------------------------------------------------- */
/* — API fetch helpers                                                    */
/* ----------------------------------------------------------------------- */

/** GET /api/dashboard/sales/top/?scope=…  */
export function fetchTopItems(scope: string): Promise<TopItemsPayload> {
  return apiFetch<TopItemsPayload>(
    `/api/dashboard/sales/top/?scope=${encodeURIComponent(scope)}`,
  );
}

/** GET /api/dashboard/sales/monthly/?months=N  */
export function fetchMonthly(months = 12): Promise<MonthlyPayload> {
  return apiFetch<MonthlyPayload>(
    `/api/dashboard/sales/monthly/?months=${months}`,
  );
}

/** GET /api/dashboard/inventory/low-stock/  */
export function fetchLowStock(): Promise<LowStockPayload> {
  return apiFetch<LowStockPayload>('/api/dashboard/inventory/low-stock/');
}
