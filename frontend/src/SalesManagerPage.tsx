import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react';
import { useSearchParams } from 'react-router-dom';
import { apiFetch, ApiError } from './api';
import { useAuth } from './authContext';
import { Icon, type IconName } from './Icon';
import './SalesManagerPage.css';

/**
 * Sales Manager page — a manager-only, read-first oversight screen for all
 * open and recent sales.
 *
 * Data comes from the real `GET /api/sales/` endpoint (no server-side
 * filtering exists in v1, so the full list is fetched and filtered
 * client-side, matching the SuppliersPage pattern). KPI metrics are derived
 * from the same list, so they always agree with the table.
 */

export interface SaleLine {
  id: number;
  product: number;
  product_name: string;
  quantity: number;
  unit_price: string;
  discount_type: string;
  discount_value: string;
  discount_amount: string;
  discount_authorizer: number | null;
  line_total: string;
}

export interface Payment {
  id: number;
  method: string;
  amount: string;
  tendered: string | null;
  change_due: string;
  manager_approved: boolean;
  created_at: string;
}

export interface Sale {
  id: number;
  number: number;
  cashier: number;
  cashier_username: string;
  customer: number | null;
  status: string;
  subtotal: string;
  discount_total: string;
  total: string;
  lines: SaleLine[];
  payments: Payment[];
  note: string;
  needs_review: boolean;
  created_at: string;
  completed_at: string | null;
}

interface Cashier {
  id: number;
  username: string;
  role: string;
  active?: boolean;
}

/** Spec §4.6 drawer tabs. */
type DrawerTab = 'lines' | 'payments' | 'timeline' | 'receipt';

const DRAWER_TAB_LABELS: Record<DrawerTab, string> = {
  lines: 'Lines',
  payments: 'Payments',
  timeline: 'Timeline',
  receipt: 'Receipt',
};

interface SaleManagerPageProps {
  /** Opt-in test seam: render these sales without hitting the network. */
  initialSales?: Sale[];
}

const STATUS_LABELS: Record<string, string> = {
  pending: 'Pending',
  open: 'Open',
  completed: 'Completed',
  voided: 'Voided',
  refunded: 'Refunded',
};

const STATUS_BADGES: Record<string, string> = {
  pending: 'mm-badge--info',
  open: 'mm-badge--primary',
  completed: 'mm-badge--success',
  voided: 'mm-badge--danger',
  refunded: 'mm-badge--muted',
};

const PAYMENT_METHODS: Record<string, string> = {
  cash: 'cash',
  card: 'card',
  split: 'split',
  invoice: 'invoice',
};

const OPEN_STATUSES = ['pending', 'open'];

function normalizeList<T>(data: T[] | { results: T[] }): T[] {
  return Array.isArray(data) ? data : data.results;
}

const currency = new Intl.NumberFormat('en-US', {
  style: 'currency',
  currency: 'USD',
});

function toNumber(value: string | number | null | undefined): number {
  const parsed = Number(value ?? 0);
  return Number.isFinite(parsed) ? parsed : 0;
}

function formatMoney(value: string | number): string {
  return currency.format(toNumber(value));
}

/**
 * Serialize a date as `YYYY-MM-DD` in the given IANA timezone.
 * Without a zone argument it falls back to the host's own zone (which the old
 * browser-clock bug used); callers pass `me.business_timezone` (S5).
 */
function ymdInZone(instant: Date | string, timeZone: string): string {
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).formatToParts(new Date(instant));
  const get = (t: string) => parts.find((p) => p.type === t)?.value ?? '00';
  return `${get('year')}-${get('month')}-${get('day')}`;
}

/**
 * True when the ISO instant falls on the business-local calendar date of
 * "now" — i.e. same `YYYY-MM-DD` in the business timezone. This replaces the
 * old browser-clock comparison so `/sales-manager` and the `/` dashboard agree
 * on what day it is regardless of the viewer's timezone (S5, contract §3.4).
 */
function isToday(isoDate: string, timeZone: string): boolean {
  const date = new Date(isoDate);
  if (Number.isNaN(date.getTime())) return false;
  return ymdInZone(isoDate, timeZone) === ymdInZone(new Date(), timeZone);
}

/** Local date as `YYYY-MM-DD` (matches the `<input type="date">` value format). */
function todayIso(): string {
  const d = new Date();
  const month = `${d.getMonth() + 1}`.padStart(2, '0');
  const day = `${d.getDate()}`.padStart(2, '0');
  return `${d.getFullYear()}-${month}-${day}`;
}

/**
 * CSV cell sanitizer. Spreadsheets interpret a leading `= + - @` as a formula,
 * so a value that begins with one is a formula-injection vector (a Django
 * username like `+charlie` would otherwise evaluate on open). Prefix such
 * cells with a single quote, which is the conventional CSV "text" marker and
 * is ignored by parsers when it forms a whole cell.
 */
function sanitizeCsvCell(value: string): string {
  return /^[=+\-@]/.test(value) ? `'${value}` : value;
}

function relativeTime(isoDate: string): string {
  const date = new Date(isoDate);
  if (Number.isNaN(date.getTime())) return isoDate;
  const diffMs = Date.now() - date.getTime();
  const minutes = Math.round(diffMs / 60_000);
  if (minutes < 1) return 'just now';
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} hr ago`;
  const days = Math.round(hours / 24);
  if (days < 7) return `${days} day${days === 1 ? '' : 's'} ago`;
  return date.toLocaleDateString();
}

function absoluteTime(isoDate: string): string {
  const date = new Date(isoDate);
  return Number.isNaN(date.getTime()) ? isoDate : date.toLocaleString();
}

export interface Toast {
  id: number;
  kind: 'success' | 'error';
  message: string;
}

/** KPI summary bar — four clickable metric tiles that apply table filters. */
function KpiBar({
  openCount,
  revenue,
  avgBasket,
  reviewCount,
  onOpenClick,
  onRevenueClick,
  onReviewClick,
}: {
  openCount: number;
  revenue: string;
  avgBasket: string;
  reviewCount: number;
  onOpenClick: () => void;
  onRevenueClick: () => void;
  onReviewClick: () => void;
}) {
  const cards: Array<{
    testid: string;
    label: string;
    value: string;
    hint: string;
    icon: IconName;
    tone: string;
    onClick?: () => void;
  }> = [
    {
      testid: 'kpi-open-sales',
      label: 'Open Sales',
      value: String(openCount),
      hint: 'pending · open',
      icon: 'sales',
      tone: 'mm-icon-tile--info',
      onClick: onOpenClick,
    },
    {
      testid: 'kpi-revenue',
      label: "Today's Revenue",
      value: revenue,
      hint: 'completed today',
      icon: 'sales-manager',
      tone: 'mm-icon-tile--success',
      onClick: onRevenueClick,
    },
    {
      testid: 'kpi-avg-basket',
      label: 'Avg. Basket',
      value: avgBasket,
      hint: 'revenue ÷ completed',
      icon: 'customers',
      tone: '',
    },
    {
      testid: 'kpi-needs-review',
      label: 'Needs Review',
      value: String(reviewCount),
      hint: 'void · refund · blank',
      icon: 'sparkles',
      tone: 'mm-icon-tile--warning',
      onClick: onReviewClick,
    },
  ];

  return (
    <div className="sales-manager-kpis" aria-label="Sales summary">
      {cards.map((card) => {
        const inner = (
          <>
            <span className={`mm-icon-tile ${card.tone}`}>
              <Icon name={card.icon} size={22} />
            </span>
            <div className="sales-manager-kpi-text">
              <span className="sales-manager-kpi-value" data-testid={card.testid}>
                {card.value}
              </span>
              <span className="sales-manager-kpi-label">{card.label}</span>
              <span className="sales-manager-kpi-hint">{card.hint}</span>
            </div>
          </>
        );
        return card.onClick ? (
          <button
            key={card.testid}
            type="button"
            className="mm-card sales-manager-kpi sales-manager-kpi--link"
            onClick={card.onClick}
          >
            {inner}
          </button>
        ) : (
          <div key={card.testid} className="mm-card sales-manager-kpi">
            {inner}
          </div>
        );
      })}
    </div>
  );
}

/** Filter toolbar: search + status multi + cashier + needs-review + date. */
function SalesManagerToolbar({
  search,
  onSearch,
  statuses,
  onStatusToggle,
  needsReview,
  onNeedsReview,
  payments,
  onPaymentToggle,
  cashiers,
  cashier,
  onCashier,
  fromDate,
  onFromDate,
  toDate,
  onToDate,
  results,
  total,
  onClear,
}: {
  search: string;
  onSearch: (value: string) => void;
  statuses: string[];
  onStatusToggle: (status: string) => void;
  needsReview: boolean;
  onNeedsReview: (checked: boolean) => void;
  payments: string[];
  onPaymentToggle: (method: string) => void;
  cashiers: Cashier[];
  cashier: string;
  onCashier: (value: string) => void;
  fromDate: string;
  onFromDate: (value: string) => void;
  toDate: string;
  onToDate: (value: string) => void;
  results: number;
  total: number;
  onClear: () => void;
}) {
  const hasFilters =
    search !== '' ||
    statuses.length > 0 ||
    needsReview ||
    payments.length > 0 ||
    cashier !== '' ||
    fromDate !== '' ||
    toDate !== '';

  return (
    <div className="sales-manager-toolbar">
      <div className="sales-manager-field sales-manager-search">
        <label className="mm-visually-hidden" htmlFor="sales-search">
          Search sales
        </label>
        <input
          id="sales-search"
          data-testid="sales-search"
          className="mm-input"
          type="search"
          value={search}
          onChange={(e) => onSearch(e.target.value)}
          placeholder="Search sale #, cashier, customer, note"
          aria-label="Search sales"
        />
      </div>

      <div className="sales-manager-filters">
        <div className="sales-manager-field">
          <label className="mm-visually-hidden" htmlFor="filter-status">
            Filter by status
          </label>
          <div
            id="filter-status"
            data-testid="filter-status"
            className="sales-manager-statuschips"
            role="group"
            aria-label="Filter by status"
          >
            {Object.keys(STATUS_LABELS).map((status) => {
              const active = statuses.includes(status);
              return (
                <button
                  key={status}
                  type="button"
                  className={`sales-manager-chip${active ? ' active' : ''}`}
                  aria-pressed={active}
                  onClick={() => onStatusToggle(status)}
                >
                  {STATUS_LABELS[status]}
                </button>
              );
            })}
          </div>
        </div>

        <div className="sales-manager-field">
          <label className="mm-visually-hidden" htmlFor="filter-cashier">
            Filter by cashier
          </label>
          <select
            id="filter-cashier"
            data-testid="filter-cashier"
            className="mm-select"
            value={cashier}
            onChange={(e) => onCashier(e.target.value)}
            aria-label="Filter by cashier"
          >
            <option value="">All cashiers</option>
            {cashiers.map((c) => (
              <option key={c.id} value={c.username}>
                {c.username}
              </option>
            ))}
          </select>
        </div>

        <div className="sales-manager-field">
          <div
            id="filter-payment"
            data-testid="filter-payment"
            className="sales-manager-statuschips"
            role="group"
            aria-label="Filter by payment method"
          >
            {Object.keys(PAYMENT_METHODS).map((method) => {
              const active = payments.includes(method);
              return (
                <button
                  key={method}
                  type="button"
                  className={`sales-manager-chip${active ? ' active' : ''}`}
                  aria-pressed={active}
                  data-testid={`payment-chip-${method}`}
                  onClick={() => onPaymentToggle(method)}
                >
                  {method.charAt(0).toUpperCase() + method.slice(1)}
                </button>
              );
            })}
          </div>
        </div>

        <div className="sales-manager-field sales-manager-datefield">
          <label className="mm-label" htmlFor="filter-date-from">
            From
          </label>
          <input
            id="filter-date-from"
            data-testid="filter-date"
            className="mm-input"
            type="date"
            value={fromDate}
            onChange={(e) => onFromDate(e.target.value)}
          />
        </div>

        <div className="sales-manager-field sales-manager-datefield">
          <label className="mm-label" htmlFor="filter-date-to">
            To
          </label>
          <input
            id="filter-date-to"
            className="mm-input"
            type="date"
            value={toDate}
            onChange={(e) => onToDate(e.target.value)}
          />
        </div>

        <label className="sales-manager-review-check">
          <input
            type="checkbox"
            checked={needsReview}
            onChange={(e) => onNeedsReview(e.target.checked)}
            data-testid="filter-needs-review"
          />
          <span>Needs review</span>
        </label>

        {hasFilters && (
          <button
            type="button"
            className="mm-btn mm-btn--ghost mm-btn--sm"
            onClick={onClear}
          >
            Clear filters
          </button>
        )}
      </div>

      <p className="sales-manager-count mm-muted">
        Showing {results} of {total} sale{total === 1 ? '' : 's'}
      </p>
    </div>
  );
}

/** Loading skeleton matching the table's column count. */
function SalesManagerSkeleton() {
  return (
    <div className="mm-loading sales-manager-loading" role="status">
      <span className="mm-spinner" aria-hidden="true" />
      Loading sales…
    </div>
  );
}

/** Empty state with the sales-manager icon. */
function SalesManagerEmpty({ hasSales }: { hasSales: boolean }) {
  return (
    <div className="mm-empty sales-manager-empty">
      <span className="mm-icon-tile mm-icon-tile--info">
        <Icon name="sales-manager" size={22} />
      </span>
      <h2 className="mm-empty-title">{hasSales ? 'No sales match' : 'No sales yet'}</h2>
      <p className="mm-empty-text">
        {hasSales
          ? 'Adjust filters or date range.'
          : 'No sales recorded yet. The first sale will appear here.'}
      </p>
    </div>
  );
}

/** Sale detail drawer (right slide-over) with lines + payments + totals. */
function SaleDrawer({
  sale,
  onClose,
  onDiscount,
}: {
  sale: Sale;
  onClose: () => void;
  onDiscount: (line: SaleLine) => void;
}) {
  const panelRef = useRef<HTMLElement | null>(null);
  const titleRef = useRef<HTMLHeadingElement | null>(null);

  // Spec §4.6 — the drawer renders four tabs, defaulting to "Lines". The
  // drawer is conditionally mounted only while open, so each open remounts
  // with fresh state; no effect is needed to reset the tab between sales
  // (and an effect doing so would trip react/set-state-in-effect).
  const [activeTab, setActiveTab] = useState<DrawerTab>('lines');

  useEffect(() => {
    // This is a modal dialog: on open we move focus to the heading so a
    // keyboard-only manager lands inside the drawer, and we trap Tab within
    // it (cycling first->last->first) so focus can't escape into the page
    // behind the backdrop. Escape closes it. The invoking control's focus is
    // restored by the parent once this drawer unmounts.
    titleRef.current?.focus();
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.stopPropagation();
        onClose();
        return;
      }
      if (event.key !== 'Tab') return;
      const panel = panelRef.current;
      if (!panel) return;
      const focusables = Array.from(
        panel.querySelectorAll<HTMLElement>(
          'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
        ),
      );
      if (focusables.length === 0) return;
      const first = focusables[0] as HTMLElement;
      const last = focusables[focusables.length - 1] as HTMLElement;
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [sale.id, onClose]);

  const open = sale.status === 'open' || sale.status === 'pending';
  const paymentsTotal = sale.payments.reduce(
    (sum, p) => sum + toNumber(p.amount),
    0,
  );

  return (
    <div
      className="sales-manager-drawer-backdrop"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <aside
        ref={panelRef}
        className="sales-manager-drawer"
        data-testid="sale-drawer"
        role="dialog"
        aria-modal="true"
        aria-labelledby="sale-drawer-title"
      >
        <header className="sales-manager-drawer-header">
          <div>
            <h2
              id="sale-drawer-title"
              tabIndex={-1}
              ref={titleRef}
              className="sales-manager-drawer-title"
            >
              Sale #{sale.number}
            </h2>
            <p className="mm-muted">
              {sale.cashier_username} · {sale.customer ? `Customer #${sale.customer}` : 'Walk-in'} ·{' '}
              {relativeTime(sale.created_at)}
            </p>
          </div>
          <span className={`mm-badge ${STATUS_BADGES[sale.status] ?? 'mm-badge--muted'}`}>
            {STATUS_LABELS[sale.status] ?? sale.status}
          </span>
          <button
            type="button"
            className="mm-modal-close"
            onClick={onClose}
            aria-label="Close sale details"
          >
            <Icon name="x" size={20} />
          </button>
        </header>

        <div className="sales-manager-drawer-body">
          <div
            className="sales-manager-drawer-tabs"
            role="tablist"
            aria-label="Sale details"
          >
            {(Object.keys(DRAWER_TAB_LABELS) as DrawerTab[]).map((tab) => (
              <button
                key={tab}
                type="button"
                role="tab"
                id={`sale-tab-${tab}`}
                aria-selected={activeTab === tab}
                aria-controls={`sale-panel-${tab}`}
                tabIndex={activeTab === tab ? 0 : -1}
                className={`sales-manager-drawer-tab${activeTab === tab ? ' active' : ''}`}
                onClick={() => setActiveTab(tab)}
              >
                {DRAWER_TAB_LABELS[tab]}
              </button>
            ))}
          </div>

          {activeTab === 'lines' && (
          <div
            role="tabpanel"
            id="sale-panel-lines"
            aria-labelledby="sale-tab-lines"
          >
            {sale.lines.length === 0 ? (
              <p className="mm-muted">No items on this sale.</p>
            ) : (
              <div className="mm-table-wrap">
                <table className="mm-table sales-manager-lines-table">
                  <thead>
                    <tr>
                      <th scope="col">Product</th>
                      <th scope="col">Qty</th>
                      <th scope="col">Unit</th>
                      <th scope="col">Discount</th>
                      <th scope="col">Total</th>
                      {open && (
                        <th scope="col">
                          <span className="mm-visually-hidden">Discount</span>
                        </th>
                      )}
                    </tr>
                  </thead>
                  <tbody>
                    {sale.lines.map((line) => (
                      <tr key={line.id}>
                        <td>{line.product_name}</td>
                        <td>{line.quantity}</td>
                        <td>{formatMoney(line.unit_price)}</td>
                        <td className={toNumber(line.discount_amount) > 0 ? 'sales-manager-negative' : ''}>
                          {toNumber(line.discount_amount) > 0
                            ? `-${formatMoney(line.discount_amount)}`
                            : '—'}
                        </td>
                        <td>{formatMoney(line.line_total)}</td>
                        {open && (
                          <td>
                            <button
                              type="button"
                              className="mm-btn mm-btn--outline mm-btn--sm"
                              onClick={() => onDiscount(line)}
                            >
                              Discount
                            </button>
                          </td>
                        )}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
          )}

          {activeTab === 'payments' && (
          <div
            role="tabpanel"
            id="sale-panel-payments"
            aria-labelledby="sale-tab-payments"
          >
            {sale.payments.length === 0 ? (
              <p className="mm-muted">No payments recorded.</p>
            ) : (
              <ul className="sales-manager-payments">
                {sale.payments.map((payment) => (
                  <li key={payment.id} className="sales-manager-payment">
                    <span className="mm-badge mm-badge--muted">
                      {PAYMENT_METHODS[payment.method] ?? payment.method}
                    </span>
                    <span>{formatMoney(payment.amount)}</span>
                    {payment.tendered != null && (
                      <span className="mm-muted">
                        tendered {formatMoney(payment.tendered)}
                      </span>
                    )}
                    {toNumber(payment.change_due) > 0 && (
                      <span className="mm-muted">
                        change {formatMoney(payment.change_due)}
                      </span>
                    )}
                    {payment.manager_approved && (
                      <span className="mm-badge mm-badge--info">Manager approved</span>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </div>
          )}

          {activeTab === 'timeline' && (
          <div
            role="tabpanel"
            id="sale-panel-timeline"
            aria-labelledby="sale-tab-timeline"
          >
            <ul className="sales-manager-timeline">
              <li>
                <span className="sales-manager-timeline-dot" aria-hidden="true" />
                <div>
                  <strong>Created</strong>
                  <span className="mm-muted">{relativeTime(sale.created_at)}</span>
                </div>
              </li>
              {sale.payments.map((payment) => (
                <li key={payment.id}>
                  <span className="sales-manager-timeline-dot" aria-hidden="true" />
                  <div>
                    <strong>
                      Paid {PAYMENT_METHODS[payment.method] ?? payment.method} ·{' '}
                      {formatMoney(payment.amount)}
                    </strong>
                    <span className="mm-muted">{relativeTime(payment.created_at)}</span>
                  </div>
                </li>
              ))}
              {sale.completed_at && (
                <li>
                  <span className="sales-manager-timeline-dot" aria-hidden="true" />
                  <div>
                    <strong>Completed</strong>
                    <span className="mm-muted">{relativeTime(sale.completed_at)}</span>
                  </div>
                </li>
              )}
              <li>
                <span className="sales-manager-timeline-dot" aria-hidden="true" />
                <div>
                  <strong>Status</strong>
                  <span className={sale.status === 'voided' || sale.status === 'refunded' ? 'sales-manager-negative' : ''}>
                    {STATUS_LABELS[sale.status] ?? sale.status}
                  </span>
                </div>
              </li>
              {sale.note && (
                <li>
                  <span className="sales-manager-timeline-dot" aria-hidden="true" />
                  <div>
                    <strong>Note</strong>
                    <span>{sale.note}</span>
                  </div>
                </li>
              )}
            </ul>
          </div>
          )}

          {activeTab === 'receipt' && (
          <div
            role="tabpanel"
            id="sale-panel-receipt"
            aria-labelledby="sale-tab-receipt"
          >
            <div className="receipt-slip">
              <div className="receipt-merchant">
                <p className="receipt-store">Store</p>
                <div className="receipt-merchant-meta mm-muted">
                  <p>Receipt for sale #{sale.number}</p>
                </div>
              </div>
              <div className="receipt-rule" aria-hidden="true" />
              <dl className="receipt-sale-meta">
                <div className="receipt-meta-row">
                  <dt>Date / Time</dt>
                  <dd>{sale.completed_at ? relativeTime(sale.completed_at) : '\u2014'}</dd>
                </div>
                <div className="receipt-meta-row">
                  <dt>Cashier</dt>
                  <dd>{sale.cashier_username || '\u2014'}</dd>
                </div>
                <div className="receipt-meta-row">
                  <dt>Sale #</dt>
                  <dd>{sale.number}</dd>
                </div>
              </dl>
              <div className="receipt-rule" aria-hidden="true" />
              <div className="receipt-items">
                <div className="receipt-column-head" aria-hidden="true">
                  <span>Item</span>
                  <span>Qty</span>
                  <span>Total</span>
                </div>
                {sale.lines.length === 0 && (
                  <div className="receipt-item">
                    <span className="receipt-item-name">No items</span>
                  </div>
                )}
                {sale.lines.map((line) => (
                  <div key={line.id} className="receipt-item">
                    <span className="receipt-item-name">{line.product_name}</span>
                    <span className="receipt-item-qty">×{line.quantity}</span>
                    <span className="receipt-item-total">{formatMoney(line.line_total)}</span>
                  </div>
                ))}
              </div>
              <div className="receipt-rule" aria-hidden="true" />
              <div className="receipt-totals">
                <div className="receipt-total-row">
                  <span>Subtotal</span>
                  <span>{formatMoney(sale.subtotal)}</span>
                </div>
                {toNumber(sale.discount_total) > 0 && (
                  <div className="receipt-total-row">
                    <span>Discount</span>
                    <span>{`-${formatMoney(sale.discount_total)}`}</span>
                  </div>
                )}
                <div className="receipt-total-row receipt-grand-total">
                  <span>Total</span>
                  <span>{formatMoney(sale.total)}</span>
                </div>
              </div>
            </div>
          </div>
          )}

          <section aria-label="Sale totals" className="sales-manager-drawer-totals">
            <div>
              <span className="mm-muted">Subtotal</span>
              <span>{formatMoney(sale.subtotal)}</span>
            </div>
            <div>
              <span className="mm-muted">Discount</span>
              <span className={toNumber(sale.discount_total) > 0 ? 'sales-manager-negative' : ''}>
                {toNumber(sale.discount_total) > 0
                  ? `-${formatMoney(sale.discount_total)}`
                  : formatMoney('0')}
              </span>
            </div>
            <div>
              <span className="mm-muted">Paid</span>
              <span>{formatMoney(paymentsTotal)}</span>
            </div>
            <div className="sales-manager-drawer-grand-total">
              <span>Total</span>
              <span>{formatMoney(sale.total)}</span>
            </div>
          </section>
        </div>
      </aside>
    </div>
  );
}

/** Confirmation modal shared by void / refund / reassign flows. */
function ActionModal({
  title,
  description,
  confirmLabel,
  busyLabel,
  busy,
  error,
  children,
  onConfirm,
  onClose,
}: {
  title: string;
  description: string;
  confirmLabel: string;
  busyLabel: string;
  busy: boolean;
  error: string;
  children?: ReactNode;
  onConfirm: () => void;
  onClose: () => void;
}) {
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !busy) onClose();
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [busy, onClose]);

  return (
    <div
      className="mm-modal-backdrop"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget && !busy) onClose();
      }}
    >
      <div
        className="mm-modal"
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="action-modal-title"
      >
        <header className="mm-modal-header">
          <h2 id="action-modal-title" className="mm-modal-title">
            {title}
          </h2>
          <button
            type="button"
            className="mm-modal-close"
            onClick={onClose}
            disabled={busy}
            aria-label="Close"
          >
            <Icon name="x" size={20} />
          </button>
        </header>
        <div className="mm-modal-body">
          {error && (
            <div className="mm-alert mm-alert--danger" role="alert">
              {error}
            </div>
          )}
          <p>{description}</p>
          {children}
        </div>
        <footer className="mm-modal-footer">
          <button
            type="button"
            className="mm-btn mm-btn--ghost"
            onClick={onClose}
            disabled={busy}
          >
            Cancel
          </button>
          <button
            type="button"
            className="mm-btn mm-btn--danger"
            data-testid="action-confirm"
            onClick={onConfirm}
            disabled={busy}
          >
            {busy ? busyLabel : confirmLabel}
          </button>
        </footer>
      </div>
    </div>
  );
}

/** Drop-down of toast notifications, top-right. */
function Toasts({
  toasts,
  onDismiss,
}: {
  toasts: Toast[];
  onDismiss: (id: number) => void;
}) {
  return (
    <div className="sales-manager-toasts" aria-live="polite" aria-atomic="false">
      {toasts.map((toast) => (
        <div
          key={toast.id}
          className={`mm-alert mm-alert--banner sales-manager-toast${
            toast.kind === 'error' ? ' sales-manager-toast--error' : ''
          }`}
          role={toast.kind === 'error' ? 'alert' : 'status'}
        >
          <span>{toast.message}</span>
          <button
            type="button"
            className="sales-manager-toast-close"
            onClick={() => onDismiss(toast.id)}
            aria-label="Dismiss notification"
          >
            <Icon name="x" size={16} />
          </button>
        </div>
      ))}
    </div>
  );
}

export default function SalesManagerPage({ initialSales }: SaleManagerPageProps) {
  const { me } = useAuth();
  const businessTimezone = me?.business_timezone || 'UTC';
  const [searchParams, setSearchParams] = useSearchParams();

  const seeded = initialSales !== undefined;
  const [sales, setSales] = useState<Sale[]>(() =>
    initialSales ? normalizeList(initialSales) : [],
  );
  const [loading, setLoadingState] = useState(!seeded);
  const [error, setError] = useState('');
  const [stale, setStale] = useState(false);
  // Incremented on every successful load/refresh so the stale-data pill's 30 s
  // timer is re-armed (a manual refresh must reset the "stale" deadline).
  const [refreshTick, setRefreshTick] = useState(0);

  const [cashiers, setCashiers] = useState<Cashier[]>([]);

  // Filter state, reflected in the URL query string so the view is shareable.
  const search = searchParams.get('search') ?? '';
  const statusFilter = searchParams.get('status') ?? '';
  const cashierFilter = searchParams.get('cashier') ?? '';
  const paymentFilter = searchParams.get('payment') ?? '';
  const needsReview = searchParams.get('needs_review') === 'true';
  const fromDate = searchParams.get('from') ?? '';
  const toDate = searchParams.get('to') ?? '';

  // The search box is a debounced local input: typing is instant, but the URL
  // (and therefore the visible filter) only commits after 300 ms of quiet.
  const [searchInput, setSearchInput] = useState(search);
  const [prevSearch, setPrevSearch] = useState(search);
  // Keep the local search box in sync when the URL `search` changes from an
  // external navigation (back/forward, a shared link). Done during render
  // (React's "adjust state when a prop changes" pattern) rather than in an
  // effect, so the oxlint react/set-state-in-effect check stays clean.
  if (search !== prevSearch) {
    setPrevSearch(search);
    setSearchInput(search);
  }

  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());
  const [drawerSale, setDrawerSale] = useState<Sale | null>(null);
  const [toasts, setToasts] = useState<Toast[]>([]);

  // Focus is managed across the drawer's open/close (AC-19 / WCAG 2.1.1 A):
  // remember the invoking control so closing the drawer can return focus to
  // it after the drawer (and its focusable elements) unmount.
  const drawerReturnFocus = useRef<HTMLElement | null>(null);

  const openDrawer = useCallback((sale: Sale) => {
    drawerReturnFocus.current =
      (document.activeElement as HTMLElement | null) ?? null;
    setDrawerSale(sale);
  }, []);

  const closeDrawer = useCallback(() => {
    setDrawerSale(null);
  }, []);

  // Once the drawer unmounts, hand focus back to the control that opened it.
  useEffect(() => {
    if (drawerSale !== null || !drawerReturnFocus.current) return;
    const target = drawerReturnFocus.current;
    drawerReturnFocus.current = null;
    const frame = requestAnimationFrame(() => {
      if (target.isConnected) target.focus();
    });
    return () => cancelAnimationFrame(frame);
  }, [drawerSale]);

  // Action-modal state.
  const [voidTarget, setVoidTarget] = useState<Sale | null>(null);
  const [refundTarget, setRefundTarget] = useState<Sale | null>(null);
  const [reassignTarget, setReassignTarget] = useState<Sale | null>(null);
  const [reassignCashier, setReassignCashier] = useState('');
  const [discountLine, setDiscountLine] = useState<{ sale: Sale; line: SaleLine } | null>(null);
  const [discountType, setDiscountType] = useState('percent');
  const [discountValue, setDiscountValue] = useState('');
  const [voidReason, setVoidReason] = useState('');
  const [refundReason, setRefundReason] = useState('');
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState('');

  const setFilter = useCallback(
    (patch: Record<string, string>) => {
      const next = new URLSearchParams(searchParams);
      for (const [key, value] of Object.entries(patch)) {
        if (value) next.set(key, value);
        else next.delete(key);
      }
      setSearchParams(next, { replace: true });
    },
    [searchParams, setSearchParams],
  );

  const clearFilters = useCallback(() => {
    setSearchParams({}, { replace: true });
    setSelectedIds(new Set());
  }, [setSearchParams]);

  const pushToast = useCallback((kind: Toast['kind'], message: string) => {
    setToasts((list) => [...list, { id: Date.now() + Math.random(), kind, message }]);
  }, []);

  const dismissToast = useCallback((id: number) => {
    setToasts((list) => list.filter((t) => t.id !== id));
  }, []);

  // Load sales + cashiers from the API on mount, unless seeded (test seam).
  useEffect(() => {
    if (seeded) return;
    let active = true;
    const load = async () => {
      setLoadingState(true);
      setError('');
      try {
        const [saleData, cashierData] = await Promise.all([
          apiFetch<Sale[] | { results: Sale[] }>('/api/sales/'),
          apiFetch<Cashier[] | { results: Cashier[] }>('/api/users/?role=cashier&page_size=100').catch(() => []),
        ]);
        if (!active) return;
        setSales(normalizeList(saleData));
        setCashiers(normalizeList(cashierData));
        setStale(false);
        setRefreshTick((n) => n + 1);
      } catch (err) {
        if (!active) return;
        setError(err instanceof ApiError ? err.message : 'Failed to load sales');
      } finally {
        if (active) setLoadingState(false);
      }
    };
    void load();
    return () => {
      active = false;
    };
  }, [seeded]);

  // Stale-data pill: if no refresh arrives within 30 s, offer one. Re-armed on
  // every successful load/refresh (via refreshTick), so the pill reliably
  // reappears after a manual refresh too.
  useEffect(() => {
    const timer = setTimeout(() => setStale(true), 30_000);
    return () => clearTimeout(timer);
  }, [refreshTick]);

  // Debounced search: reflect the local search box into the URL query string.
  useEffect(() => {
    const timer = setTimeout(() => {
      setFilter({ search: searchInput });
    }, 300);
    return () => clearTimeout(timer);
  }, [searchInput, setFilter]);

  // Derived KPIs from the full list (so they always match the table source).
  const kpis = useMemo(() => {
    const open = sales.filter((s) => OPEN_STATUSES.includes(s.status));
    const completedToday = sales.filter(
      (s) => s.status === 'completed' && isToday(s.created_at, businessTimezone),
    );
    const revenue = completedToday.reduce((sum, s) => sum + toNumber(s.total), 0);
    const avgBasket = completedToday.length > 0 ? revenue / completedToday.length : 0;
    const review = sales.filter((s) => s.needs_review).length;
    return { open: open.length, revenue, avgBasket, review };
  }, [sales, businessTimezone]);

  const statuses = useMemo(
    () => statusFilter.split(',').filter(Boolean),
    [statusFilter],
  );

  const payments = useMemo(
    () => paymentFilter.split(',').filter(Boolean),
    [paymentFilter],
  );

  // Client-side filtering (server has no filter params in v1).
  const filteredSales = useMemo(() => {
    const query = search.trim().toLowerCase();
    const statusesList = statuses;
    return sales.filter((s) => {
      if (query) {
        const haystack = [
          String(s.number),
          s.cashier_username,
          s.customer ? String(s.customer) : '',
          s.note,
        ]
          .join(' ')
          .toLowerCase();
        if (!haystack.includes(query)) return false;
      }
      if (statusesList.length > 0 && !statusesList.includes(s.status)) return false;
      if (cashierFilter && s.cashier_username !== cashierFilter) return false;
      if (payments.length > 0) {
        const method = s.payments[0]?.method;
        if (!method || !payments.includes(method)) return false;
      }
      if (needsReview && !s.needs_review) return false;
      if (fromDate) {
        const from = new Date(`${fromDate}T00:00:00`);
        if (Number.isNaN(from.getTime()) || new Date(s.created_at) < from) return false;
      }
      if (toDate) {
        const to = new Date(`${toDate}T23:59:59`);
        if (Number.isNaN(to.getTime()) || new Date(s.created_at) > to) return false;
      }
      return true;
    });
  }, [sales, search, statuses, cashierFilter, payments, needsReview, fromDate, toDate]);

  const sortedSales = useMemo(
    () =>
      [...filteredSales].sort(
        (a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime(),
      ),
    [filteredSales],
  );

  // Refetch to keep the board fresh; also clears the stale pill.
  const refresh = useCallback(async () => {
    try {
      const data = await apiFetch<Sale[] | { results: Sale[] }>('/api/sales/');
      setSales(normalizeList(data));
      setError('');
      setStale(false);
      setRefreshTick((n) => n + 1);
      pushToast('success', 'Sales refreshed.');
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to load sales');
      pushToast('error', 'Could not refresh sales.');
    }
  }, [pushToast]);

  async function replaceSale(updated: Sale) {
    setSales((list) => list.map((s) => (s.id === updated.id ? updated : s)));
    setDrawerSale((d) => (d && d.id === updated.id ? updated : d));
  }

  async function voidSale(sale: Sale) {
    const reason = voidReason.trim();
    if (!reason) {
      setActionError('A reason is required to void a sale.');
      return;
    }
    setBusy(true);
    setActionError('');
    try {
      const updated = await apiFetch<Sale>(`/api/sales/${sale.id}/void/`, {
        method: 'POST',
        body: JSON.stringify({ reason }),
      });
      await replaceSale(updated);
      setVoidTarget(null);
      setVoidReason('');
      pushToast('success', `Sale #${sale.number} voided.`);
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : 'Failed to void sale');
    } finally {
      setBusy(false);
    }
  }

  async function refundSale(sale: Sale) {
    const reason = refundReason.trim();
    if (!reason) {
      setActionError('A reason is required to refund a sale.');
      return;
    }
    setBusy(true);
    setActionError('');
    try {
      const updated = await apiFetch<Sale>(`/api/sales/${sale.id}/refund/`, {
        method: 'POST',
        body: JSON.stringify({ reason }),
      });
      await replaceSale(updated);
      setRefundTarget(null);
      setRefundReason('');
      pushToast('success', `Sale #${sale.number} refunded.`);
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : 'Failed to refund sale');
    } finally {
      setBusy(false);
    }
  }

  async function reassignSale(sale: Sale) {
    if (!reassignCashier) {
      setActionError('Select a cashier to reassign this sale to.');
      return;
    }
    setBusy(true);
    setActionError('');
    try {
      const updated = await apiFetch<Sale>(`/api/sales/${sale.id}/`, {
        method: 'PATCH',
        // `cashier` is a PK relation; the backend validates this target user
        // has a cashier/manager-tier role. Send the id, never the username.
        body: JSON.stringify({ cashier: Number(reassignCashier) }),
      });
      // The backend now accepts `cashier` (PK) for manager/admin. Confirm the
      // response actually reflects the change; if it didn't, say so rather
      // than claim success.
      if (updated.cashier_username === sale.cashier_username) {
        setActionError(
          "The backend didn't change this sale's cashier.",
        );
        return;
      }
      await replaceSale(updated);
      setReassignTarget(null);
      setReassignCashier('');
      pushToast('success', `Sale #${sale.number} reassigned to ${updated.cashier_username}.`);
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : 'Failed to reassign sale');
    } finally {
      setBusy(false);
    }
  }

  async function setLineDiscount() {
    if (!discountLine) return;
    const value = Number(discountValue);
    if (!Number.isFinite(value) || value < 0) {
      setActionError('Enter a valid discount value.');
      return;
    }
    setBusy(true);
    setActionError('');
    try {
      const updated = await apiFetch<SaleLine>(
        `/api/sales/${discountLine.sale.id}/lines/${discountLine.line.id}/discount/`,
        {
          method: 'POST',
          body: JSON.stringify({
            discount_type: discountType,
            discount_value: String(value),
          }),
        },
      );
      // Reload the sale detail so totals reflect the updated line.
      const refreshed = await apiFetch<Sale>(`/api/sales/${discountLine.sale.id}/`);
      await replaceSale(refreshed);
      setDiscountLine(null);
      setDiscountValue('');
      setDiscountType('percent');
      pushToast('success', `Discount applied to ${updated.product_name}.`);
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : 'Failed to apply discount');
    } finally {
      setBusy(false);
    }
  }

  const bulkVoid = useCallback(async () => {
    // Void every selected sale sequentially, reporting a toast per row. No
    // silent cap: capping at 10 used to leave rows 11+ selected but unvoided
    // before clearing the selection, making them look handled.
    const reason = voidReason.trim();
    if (!reason) {
      setActionError('A reason is required to void a sale.');
      return;
    }
    const targets = sortedSales.filter((s) => selectedIds.has(s.id));
    if (targets.length === 0) return;
    setBusy(true);
    try {
      for (const sale of targets) {
        try {
          const updated = await apiFetch<Sale>(`/api/sales/${sale.id}/void/`, {
            method: 'POST',
            body: JSON.stringify({ reason }),
          });
          await replaceSale(updated);
          pushToast('success', `Sale #${sale.number} voided.`);
        } catch {
          pushToast('error', `Sale #${sale.number} could not be voided.`);
        }
      }
      setSelectedIds(new Set());
      setVoidReason('');
    } finally {
      setBusy(false);
    }
  }, [selectedIds, sortedSales, pushToast, voidReason]);

  function exportCsv() {
    // Spec §4.5 "Export selected": when a selection exists, export only the
    // selected rows (still sorted); otherwise export all visible rows.
    const source =
      selectedIds.size > 0
        ? sortedSales.filter((s) => selectedIds.has(s.id))
        : sortedSales;
    const rows = source.map((s) => [
      s.number,
      s.status,
      s.cashier_username,
      s.customer ?? 'Walk-in',
      s.lines.length,
      s.subtotal,
      s.discount_total,
      s.total,
      s.payments[0]?.method ?? '—',
      s.created_at,
    ]);
    const header = [
      'sale_number',
      'status',
      'cashier',
      'customer',
      'items',
      'subtotal',
      'discount',
      'total',
      'payment',
      'created_at',
    ];
    // UTF-8 BOM so Excel opens the file correctly.
    const csv = `\uFEFF${[header, ...rows]
      .map((row) =>
        row
          .map((cell) => {
            // Neutralize spreadsheet formula injection: a value beginning
            // with `=`, `+`, `-` or `@` would otherwise be evaluated by Excel.
            const str = sanitizeCsvCell(String(cell));
            return /[",\n]/.test(str) ? `"${str.replace(/"/g, '""')}"` : str;
          })
          .join(','),
      )
      .join('\n')}`;
    const today = new Date().toISOString().slice(0, 10).replace(/-/g, '');
    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `sales-manager-${today}.csv`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
    pushToast('success', `Exported ${rows.length} sale${rows.length === 1 ? '' : 's'} to CSV.`);
  }

  const toggleSelect = (id: number) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const allSelected =
    sortedSales.length > 0 && sortedSales.every((s) => selectedIds.has(s.id));

  const toggleAll = () => {
    if (allSelected) setSelectedIds(new Set());
    else setSelectedIds(new Set(sortedSales.map((s) => s.id)));
  };

  // Shared modal open helpers.
  const openDiscount = (line: SaleLine) => {
    if (!drawerSale) return;
    setDiscountLine({ sale: drawerSale, line });
    setDiscountType('percent');
    setDiscountValue('');
    setActionError('');
  };

  const openReassign = (sale: Sale) => {
    setReassignTarget(sale);
    setReassignCashier('');
    setActionError('');
  };

  const openRefund = (sale: Sale) => {
    setRefundTarget(sale);
    setRefundReason('');
    setActionError('');
  };

  const openVoid = (sale: Sale) => {
    setVoidTarget(sale);
    setVoidReason('');
    setActionError('');
  };

  const canManage = me !== null && (me.user.role === 'manager' || me.user.role === 'admin');

  return (
    <div className="mm-page sales-manager-page">
      <header className="mm-page-header">
        <div>
          <h1 className="mm-page-title">Sales Manager</h1>
          <p className="mm-page-subtitle">
            {sales.length} sale{sales.length === 1 ? '' : 's'} on file ·{' '}
            {kpis.open} open
          </p>
        </div>
        <div className="sales-manager-header-actions">
          {stale && (
            <button
              type="button"
              className="sales-manager-stale"
              onClick={() => void refresh()}
            >
              <span aria-hidden="true">⚠</span> Data may be stale — click to refresh
            </button>
          )}
          <button
            type="button"
            className="mm-btn mm-btn--outline"
            data-testid="export-csv"
            onClick={exportCsv}
          >
            <Icon name="sales-manager" size={16} /> Export CSV
          </button>
          <button
            type="button"
            className="mm-btn mm-btn--outline"
            onClick={() => void refresh()}
          >
            Refresh
          </button>
        </div>
      </header>

      {error && (
        <div className="mm-alert mm-alert--danger" role="alert">
          <span>Failed to load sales: {error}</span>{' '}
          <button type="button" className="sales-manager-retry" onClick={() => { setError(''); void refresh(); }}>
            Retry
          </button>
        </div>
      )}

      {!error && (
        <KpiBar
          openCount={kpis.open}
          revenue={formatMoney(kpis.revenue)}
          avgBasket={formatMoney(kpis.avgBasket)}
          reviewCount={kpis.review}
          onOpenClick={() => setFilter({ status: 'open,pending' })}
          onRevenueClick={() => {
            // Match the KPI's definition (completed sales created today) so
            // the drill-down shows the same set as the revenue card.
            const day = todayIso();
            setFilter({ status: 'completed', from: day, to: day });
          }}
          onReviewClick={() => {
            setFilter({ status: '', needs_review: 'true' });
          }}
        />
      )}

      {!canManage && (
        <div className="mm-alert mm-alert--warning sales-manager-role-alert" role="alert">
          You don&apos;t have permission to manage sales. Contact a manager or admin.
        </div>
      )}

      {selectedIds.size > 0 && (
        <div className="sales-manager-bulkbar" role="region" aria-label="Bulk actions">
          <span>
            {selectedIds.size} selected
          </span>
          <label className="mm-field sales-manager-bulk-reason">
            <span className="mm-label mm-visually-hidden">Void reason</span>
            <input
              className="mm-input mm-input--sm"
              type="text"
              value={voidReason}
              onChange={(e) => setVoidReason(e.target.value)}
              placeholder="Void reason (required)"
              aria-label="Void reason"
            />
          </label>
          <button
            type="button"
            className="mm-btn mm-btn--danger mm-btn--sm"
            data-testid="bulk-void"
            onClick={() => void bulkVoid()}
            disabled={busy}
          >
            Void selected
          </button>
          <button
            type="button"
            className="mm-btn mm-btn--outline mm-btn--sm"
            onClick={() => setSelectedIds(new Set())}
            disabled={busy}
          >
            Clear selection
          </button>
        </div>
      )}

      <section className="mm-card sales-manager-panel" aria-label="Sales">
        {!canManage ? null : (
          <>
            <SalesManagerToolbar
              search={searchInput}
              onSearch={setSearchInput}
              statuses={statuses}
              onStatusToggle={(status) => {
                const next = statuses.includes(status)
                  ? statuses.filter((s) => s !== status)
                  : [...statuses, status];
                setFilter({ status: next.join(',') });
              }}
              needsReview={needsReview}
              onNeedsReview={(checked) =>
                setFilter({ needs_review: checked ? 'true' : '' })
              }
              payments={payments}
              onPaymentToggle={(method) => {
                const next = payments.includes(method)
                  ? payments.filter((m) => m !== method)
                  : [...payments, method];
                setFilter({ payment: next.join(',') });
              }}
              cashiers={cashiers}
              cashier={cashierFilter}
              onCashier={(value) => setFilter({ cashier: value })}
              fromDate={fromDate}
              onFromDate={(value) => setFilter({ from: value })}
              toDate={toDate}
              onToDate={(value) => setFilter({ to: value })}
              results={sortedSales.length}
              total={sales.length}
              onClear={clearFilters}
            />

            {loading && <SalesManagerSkeleton />}

            {!loading && !error && sortedSales.length === 0 && (
              <SalesManagerEmpty hasSales={sales.length > 0} />
            )}

            {!loading && sortedSales.length > 0 && (
              <div className="mm-table-wrap sales-manager-table-wrap">
                <table className="mm-table sales-manager-table" data-testid="sales-table">
                  <thead>
                    <tr>
                      <th className="col-check">
                        <input
                          type="checkbox"
                          aria-label="Select all sales"
                          checked={allSelected}
                          onChange={toggleAll}
                        />
                      </th>
                      <th>Sale #</th>
                      <th>Status</th>
                      <th>Cashier</th>
                      <th>Customer</th>
                      <th>Items</th>
                      <th>Subtotal</th>
                      <th>Discount</th>
                      <th>Total</th>
                      <th>Payment</th>
                      <th>Created</th>
                      <th className="col-actions">
                        <span className="mm-visually-hidden">Actions</span>
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {sortedSales.map((sale) => (
                      <tr
                        key={sale.id}
                        className="sales-manager-row"
                        onClick={() => openDrawer(sale)}
                        style={{ cursor: 'pointer' }}
                      >
                        <td className="col-check" onClick={(e) => e.stopPropagation()}>
                          <input
                            type="checkbox"
                            aria-label={`Select sale ${sale.number}`}
                            checked={selectedIds.has(sale.id)}
                            onChange={() => toggleSelect(sale.id)}
                          />
                        </td>
                        <td className="col-number">
                          <button
                            type="button"
                            className="sales-manager-number-btn"
                            aria-label={`View details for sale ${sale.number}`}
                            onClick={() => openDrawer(sale)}
                          >
                            {sale.number}
                          </button>
                        </td>
                        <td>
                          <span className={`mm-badge ${STATUS_BADGES[sale.status] ?? 'mm-badge--muted'}`}>
                            {STATUS_LABELS[sale.status] ?? sale.status}
                          </span>
                        </td>
                        <td>{sale.cashier_username}</td>
                        <td>{sale.customer ? `Customer #${sale.customer}` : <span className="mm-muted">Walk-in</span>}</td>
                        <td>{sale.lines.length}</td>
                        <td className="sales-manager-tabular">{formatMoney(sale.subtotal)}</td>
                        <td className={toNumber(sale.discount_total) > 0 ? 'sales-manager-negative sales-manager-tabular' : 'sales-manager-tabular'}>
                          {toNumber(sale.discount_total) > 0
                            ? `-${formatMoney(sale.discount_total)}`
                            : formatMoney('0')}
                        </td>
                        <td className="sales-manager-total sales-manager-tabular">
                          {formatMoney(sale.total)}
                        </td>
                        <td>
                          {sale.payments[0] ? (
                            <span className="mm-badge mm-badge--muted">
                              {PAYMENT_METHODS[sale.payments[0].method] ?? sale.payments[0].method}
                            </span>
                          ) : (
                            <span className="mm-muted">—</span>
                          )}
                        </td>
                        <td className="sales-manager-created" title={absoluteTime(sale.created_at)}>
                          {relativeTime(sale.created_at)}
                        </td>
                        <td className="col-actions" onClick={(e) => e.stopPropagation()}>
                          {sale.status === 'open' || sale.status === 'pending' ? (
                            <button
                              type="button"
                              className="mm-btn mm-btn--danger mm-btn--sm"
                              data-testid="action-void"
                              onClick={() => openVoid(sale)}
                              title="Void this sale"
                            >
                              Void
                            </button>
                          ) : null}
                          {sale.status === 'completed' ? (
                            <button
                              type="button"
                              className="mm-btn mm-btn--danger mm-btn--sm"
                              data-testid="action-refund"
                              onClick={() => openRefund(sale)}
                              title="Refund this sale"
                            >
                              Refund
                            </button>
                          ) : null}
                          {sale.status === 'open' || sale.status === 'pending' ? (
                            <button
                              type="button"
                              className="mm-btn mm-btn--outline mm-btn--sm"
                              data-testid="action-reassign"
                              onClick={() => openReassign(sale)}
                              title="Reassign cashier"
                            >
                              Reassign
                            </button>
                          ) : null}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </>
        )}
      </section>

      {drawerSale && (
        <SaleDrawer
          sale={drawerSale}
          onClose={closeDrawer}
          onDiscount={openDiscount}
        />
      )}

      {voidTarget && (
        <ActionModal
          title={`Void sale #${voidTarget.number}?`}
          description="Voiding removes an open sale and records a required audit reason."
          confirmLabel="Void sale"
          busyLabel="Voiding…"
          busy={busy}
          error={actionError}
          onConfirm={() => void voidSale(voidTarget)}
          onClose={() => setVoidTarget(null)}
        >
          <label className="mm-field">
            <span className="mm-label">Reason (required)</span>
            <input
              className="mm-input"
              type="text"
              value={voidReason}
              onChange={(e) => setVoidReason(e.target.value)}
              placeholder="Why is this sale being voided?"
              autoFocus
            />
          </label>
        </ActionModal>
      )}

      {refundTarget && (
        <ActionModal
          title={`Refund sale #${refundTarget.number}?`}
          description="Enter a reason. The refund reverses the sale and its payments."
          confirmLabel="Refund sale"
          busyLabel="Refunding…"
          busy={busy}
          error={actionError}
          onConfirm={() => void refundSale(refundTarget)}
          onClose={() => setRefundTarget(null)}
        >
          <label className="mm-field">
            <span className="mm-label">Reason (required)</span>
            <input
              className="mm-input"
              type="text"
              value={refundReason}
              onChange={(e) => setRefundReason(e.target.value)}
              placeholder="Why is this sale being refunded?"
              autoFocus
            />
          </label>
        </ActionModal>
      )}

      {reassignTarget && (
        <ActionModal
          title={`Reassign sale #${reassignTarget.number}`}
          description="Assign this open sale to another cashier."
          confirmLabel="Reassign"
          busyLabel="Reassigning…"
          busy={busy}
          error={actionError}
          onConfirm={() => void reassignSale(reassignTarget)}
          onClose={() => setReassignTarget(null)}
        >
          <label className="mm-field">
            <span className="mm-label">Cashier</span>
            <select
              className="mm-select"
              data-testid="reassign-select"
              value={reassignCashier}
              onChange={(e) => setReassignCashier(e.target.value)}
            >
              <option value="">Select a cashier…</option>
              {cashiers.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.username}
                </option>
              ))}
            </select>
          </label>
        </ActionModal>
      )}

      {discountLine && (
        <ActionModal
          title={`Override discount — ${discountLine.line.product_name}`}
          description="Apply a manager discount to this line."
          confirmLabel="Apply discount"
          busyLabel="Applying…"
          busy={busy}
          error={actionError}
          onConfirm={() => void setLineDiscount()}
          onClose={() => setDiscountLine(null)}
        >
          <div className="sales-manager-discount-rows">
            <div className="sales-manager-discount-row">
              <label className="mm-field sales-manager-discount-type">
                <span className="mm-label">Type</span>
                <select
                  className="mm-select"
                  value={discountType}
                  onChange={(e) => setDiscountType(e.target.value)}
                >
                  <option value="percent">Percent (%)</option>
                  <option value="fixed">Fixed ($)</option>
                </select>
              </label>
              <label className="mm-field">
                <span className="mm-label">Value</span>
                <input
                  className="mm-input"
                  type="number"
                  min="0"
                  step="0.01"
                  value={discountValue}
                  onChange={(e) => setDiscountValue(e.target.value)}
                  placeholder={discountType === 'percent' ? '0–100' : '0.00'}
                />
              </label>
            </div>
            <p className="mm-muted sales-manager-discount-hint">
              Authorised by {me?.user.username ?? 'manager'} from this session.
            </p>
          </div>
        </ActionModal>
      )}

      <Toasts toasts={toasts} onDismiss={dismissToast} />
    </div>
  );
}
