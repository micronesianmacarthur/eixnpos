import { Icon, type IconName } from './Icon';
import type { TodayKpi } from './DashboardTypes';
import { formatMoney } from './dashboardFormat';
import './DashboardPage.css';

/** A single KPI tile (value + label + hint), with a loading skeleton. */
export function KpiCard({
  label,
  value,
  hint,
  icon,
  tone = 'mm-icon-tile--info',
  testId,
  loading,
  onClick,
}: {
  label: string;
  value: string;
  hint: string;
  icon: IconName;
  tone?: string;
  testId: string;
  loading: boolean;
  /** When set, the tile renders as a button (drill-down target, S9). */
  onClick?: () => void;
}) {
  const inner = (
    <>
      <span className={`mm-icon-tile ${tone}`}>
        <Icon name={icon} size={22} />
      </span>
      <div className="dashboard-kpi-text">
        {loading ? (
          <span
            className="dashboard-kpi-skeleton"
            data-testid={`${testId}-skeleton`}
            aria-hidden="true"
          />
        ) : (
          <span
            className="dashboard-kpi-value"
            data-testid={testId}
            aria-label={`${label}: ${value}`}
          >
            {value}
          </span>
        )}
        <span className="dashboard-kpi-label">{label}</span>
        <span className="dashboard-kpi-hint">{hint}</span>
      </div>
    </>
  );

  const className = `mm-card dashboard-kpi${loading ? ' dashboard-kpi--loading' : ''}`;
  if (onClick && !loading) {
    return (
      <button
        type="button"
        className={`${className} dashboard-kpi--link`}
        onClick={onClick}
        aria-label={`${label}: ${value}. ${hint}`}
      >
        {inner}
      </button>
    );
  }
  return <article className={className}>{inner}</article>;
}

/**
 * The headline KPI row: sales today, revenue today, cash sales today, invoices
 * issued (count + total), and low stock. Each tile shows a skeleton while
 * `loading`. On a failed composite (`error && !today`) tiles render "—" so an
 * operator never mistakes a load failure for a quiet trading day (S7).
 *
 * `lowStockCount` / `onLowStockClick` power the 5th tile's drill-down (S9):
 * clicking it scrolls the page to the low-stock panel.
 */
export function DashboardKpiRow({
  today,
  loading,
  error = null,
  lowStockCount,
  onLowStockClick,
}: {
  today: TodayKpi | null;
  loading: boolean;
  error?: string | null;
  lowStockCount?: number;
  onLowStockClick?: () => void;
}) {
  const money = (v?: string) => (typeof v === 'string' && v ? formatMoney(v) : undefined);
  const failed = Boolean(error) && !today && !loading;
  // On failure the tiles must read "unknown", never "0 sales today".
  const dash = failed ? '—' : '';

  // Invoice tile shows "count · total" when the count is positive; a zero
  // count shows just the count with the "no invoices issued" hint text.
  const invoiceValue =
    today && today.invoice_count > 0
      ? `${today.invoice_count} · ${money(today.invoice_total)}`
      : `${today?.invoice_count ?? 0}`;

  const lowStockValue = today ? `${lowStockCount ?? 0}` : dash;

  return (
    <div
      className="dashboard-kpis"
      aria-label="Today's performance summary"
      aria-busy={loading}
    >
      <KpiCard
        testId="kpi-sales-today"
        label="Sales"
        value={loading ? '' : failed ? dash : `${today?.sales_count ?? 0}`}
        hint="completed sales today"
        icon="sales"
        tone="mm-icon-tile--info"
        loading={loading}
      />
      <KpiCard
        testId="kpi-revenue-today"
        label="Revenue"
        value={loading ? '' : failed ? dash : money(today?.revenue) ?? '$0.00'}
        hint="today's completed sales"
        icon="sales-manager"
        tone="mm-icon-tile--success"
        loading={loading}
      />
      <KpiCard
        testId="kpi-cash-today"
        label="Cash sales"
        value={loading ? '' : failed ? dash : money(today?.cash_sales) ?? '$0.00'}
        hint="cash payments today"
        icon="sparkles"
        tone="mm-icon-tile--warning"
        loading={loading}
      />
      <KpiCard
        testId="kpi-invoices-today"
        label="Invoices"
        value={loading ? '' : failed ? dash : invoiceValue}
        hint={
          loading || !today || today.invoice_count === 0
            ? 'no invoice payments today'
            : 'invoice payments today'
        }
        icon="customers"
        tone="mm-icon-tile--secondary"
        loading={loading}
      />
      <KpiCard
        testId="kpi-low-stock"
        label="Low stock"
        value={loading ? '' : lowStockValue}
        hint="items at/below threshold"
        icon="inventory"
        tone="mm-icon-tile--danger"
        loading={loading}
        onClick={onLowStockClick}
      />
    </div>
  );
}
