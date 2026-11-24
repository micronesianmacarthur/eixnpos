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
 * One open shift (§4.5/§5.5 item). Carries the canonical B2 fields
 * (`register_name`, `opened_at_local`, `opened_by`, `cashiers`) plus legacy
 * aliases (`started_at_local`, `cashier`) kept for backward compatibility.
 */
export interface DashboardOpenSession {
  session_id: number;
  register_name: string;
  opened_at: string;
  opened_at_local: string;
  started_at: string;       // legacy alias of opened_at
  started_at_local: string; // legacy alias of opened_at_local
  business_day: string;
  register: RegisterInfo;
  opened_by: SessionCashier;
  cashier: SessionCashier;  // legacy alias of opened_by
  cashiers: string[];       // register-assigned cashier usernames
  starting_cash: string;
}

/**
 * Current-session payload (contract §5.5). ``open`` is false when there are
 * no open shifts; ``sessions`` is empty then. ``session`` is a backward-
 * compatible single-session accessor (the first open shift, same shape as the
 * pre-B2 response) or ``{open: false}``.
 */
export interface DashboardSession {
  open: boolean;
  count: number;
  business_day: string;
  sessions: DashboardOpenSession[];
  session?: DashboardOpenSession | { open: boolean };
}

/**
 * Composite first-paint payload (contract §5.7): today KPI + session + low
 * stock in one call. This page consumes `today` and `session`; `inventory`
 * is present in the payload but owned by the low-stock panel.
 */
export interface DashboardOverview {
  business_day: string;
  generated_at: string;
  /** IANA name of the business's configured timezone (contract §3.2 / S2). */
  business_timezone: string;
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
