/**
 * Type definitions for the manager dashboard data consumed by the KPI cards
 * and session summary.
 *
 * These mirror the server contract in
 * `research/dashboard-metrics-and-data-contract.md` (§5.1, §5.5, §5.7) exactly:
 * money is a 2-dp decimal *string* (never re-parsed into a JS number for
 * totals; the client formats, it does not compute), datetimes are ISO-8601
 * with offset, and the server is the sole authority on "today".
 */

/** KPI split for the business-local "today" window (contract §5.1). */
export interface TodayKpi {
  sales_count: number;
  revenue: string;
  cash_sales: string;
  invoice_count: number;
  invoice_total: string;
  void_count: number;
  refund_count: number;
  avg_basket: string;
  open_count_today: number;
}

/** Register record inside the current-session payload (§4.5). */
export interface RegisterInfo {
  id: number;
  name: string;
  identifier: string;
}

/** Opener (cashier) record inside the current-session payload (§4.5). */
export interface SessionCashier {
  id: number;
  username: string;
  role: string;
}

/**
 * Current open shift payload (contract §5.5). When `open` is false no other
 * fields are present.
 */
export interface DashboardSession {
  open: boolean;
  session_id?: number;
  started_at?: string;
  started_at_local?: string;
  business_day?: string;
  register?: RegisterInfo;
  cashier?: SessionCashier;
  starting_cash?: string;
}

/**
 * Composite first-paint payload (contract §5.7): today KPI + session + low
 * stock in one call. This page consumes `today` and `session`; `inventory`
 * is present in the payload but owned by the low-stock panel.
 */
export interface DashboardOverview {
  business_day: string;
  generated_at: string;
  period: { scope: string; start: string; end: string };
  today: TodayKpi;
  session: DashboardSession;
  inventory: {
    generated_at: string;
    low_stock_count: number;
    items: Array<{
      product_id: number;
      product_name: string;
      quantity_on_hand: number;
      min_stock: number;
      sku?: string;
      category?: string;
    }>;
    threshold_source: {
      field: string;
      rule: string;
      non_stock_excluded: boolean;
    };
  };
}
