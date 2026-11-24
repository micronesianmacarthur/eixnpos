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
}: {
  label: string;
  value: string;
  hint: string;
  icon: IconName;
  tone?: string;
  testId: string;
  loading: boolean;
}) {
  return (
    <article className={`mm-card dashboard-kpi${loading ? ' dashboard-kpi--loading' : ''}`}>
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
    </article>
  );
}

/**
 * The headline KPI row: sales today, revenue today, cash sales today, and
 * invoices issued with their total. Each tile shows a skeleton while `loading`
 * is true.
 */
export function DashboardKpiRow({
  today,
  loading,
}: {
  today: TodayKpi | null;
  loading: boolean;
}) {
  const money = (v?: string) => (typeof v === 'string' && v ? formatMoney(v) : undefined);

  // Invoice tile shows "count · total" when the count is positive; a zero
  // count shows just the count with the "no invoices issued" hint text.
  const invoiceValue =
    today && today.invoice_count > 0
      ? `${today.invoice_count} · ${money(today.invoice_total)}`
      : `${today?.invoice_count ?? 0}`;

  return (
    <div
      className="dashboard-kpis"
      aria-label="Today's performance summary"
      aria-busy={loading}
    >
      <KpiCard
        testId="kpi-sales-today"
        label="Sales"
        value={loading ? '' : `${today?.sales_count ?? 0}`}
        hint="completed sales today"
        icon="sales"
        tone="mm-icon-tile--info"
        loading={loading}
      />
      <KpiCard
        testId="kpi-revenue-today"
        label="Revenue"
        value={loading ? '' : money(today?.revenue) ?? '$0.00'}
        hint="today's completed sales"
        icon="sales-manager"
        tone="mm-icon-tile--success"
        loading={loading}
      />
      <KpiCard
        testId="kpi-cash-today"
        label="Cash sales"
        value={loading ? '' : money(today?.cash_sales) ?? '$0.00'}
        hint="cash payments today"
        icon="sparkles"
        tone="mm-icon-tile--warning"
        loading={loading}
      />
      <KpiCard
        testId="kpi-invoices-today"
        label="Invoices"
        value={loading ? '' : invoiceValue}
        hint={
          loading || !today || today.invoice_count === 0
            ? 'no invoice payments today'
            : 'invoice payments today'
        }
        icon="customers"
        tone="mm-icon-tile--secondary"
        loading={loading}
      />
    </div>
  );
}
