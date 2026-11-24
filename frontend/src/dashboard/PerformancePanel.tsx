import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  fetchMonthly,
  formatMomDelta,
  formatMomPct,
  formatMoney,
  momTone,
  type MonthlyPayload,
} from './data';

const MONTHS_SHOWN = 12;

interface PerformancePanelProps {
  /** Opt-in test seam: seed data and skip the network effect. */
  initialData?: MonthlyPayload;
}

/** Chart geometry (viewBox units). */
const CHART_W = 680;
const CHART_H = 240;
const PAD_L = 64;
const PAD_R = 16;
const PAD_T = 16;
const PAD_B = 40;
const PLOT_W = CHART_W - PAD_L - PAD_R;
const PLOT_H = CHART_H - PAD_T - PAD_B;
const BAR_MAX = 0.72; // fraction of plot height a full bar may occupy
const TICKS = 4;

/**
 * Monthly shop-performance comparison — revenue per trailing calendar month
 * (month-over-month). The sibling run builds the headline KPI cards; this
 * panel answers "how are we doing vs last month?" with an accessible bar
 * chart plus a data table. Money is formatted (never computed) client-side.
 */
export default function PerformancePanel({ initialData }: PerformancePanelProps) {
  const [data, setData] = useState<MonthlyPayload | null>(initialData ?? null);
  const [loading, setLoading] = useState(!initialData);
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      setData(await fetchMonthly(MONTHS_SHOWN));
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load performance');
      setData(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!initialData) void load();
  }, [initialData, load]);

  // Scale the bars to the max revenue so the tallest bar fills BAR_MAX.
  const { bars, maxRevenue } = useMemo(() => {
    const periods = data?.periods ?? [];
    const max = Math.max(
      0,
      ...periods.map((p) => Number(p.revenue) || 0),
    );
    const scaled = periods.map((p) => ({
      ...p,
      barPct: max > 0 ? ((Number(p.revenue) || 0) / max) * 100 : 0,
    }));
    return { bars: scaled, maxRevenue: max };
  }, [data]);

  const yTicks = useMemo(() => {
    if (maxRevenue <= 0) return [];
    const tickStep = maxRevenue / TICKS;
    return Array.from({ length: TICKS + 1 }, (_, i) => maxRevenue - tickStep * i);
  }, [maxRevenue]);

  let content: React.ReactNode;

  if (loading) {
    content = (
      <div className="mm-loading dashboard-panel-loading" role="status">
        <span className="mm-spinner" aria-hidden="true" />
        Loading performance…
      </div>
    );
  } else if (error) {
    content = (
      <div className="mm-alert mm-alert--danger" role="alert">
        <span>Failed to load performance data: {error}</span>{' '}
        <button type="button" className="dashboard-panel-retry" onClick={() => void load()}>
          Retry
        </button>
      </div>
    );
  } else if (!data || !Array.isArray(data.periods) || data.periods.length === 0) {
    content = (
      <div className="mm-empty dashboard-panel-empty">
        <span className="mm-empty-title">No sales data yet</span>
        <span className="mm-empty-text">
          Monthly revenue will appear here after the first completed sale.
        </span>
      </div>
    );
  } else if (bars.every((b) => Number(b.revenue) === 0)) {
    content = (
      <div className="mm-empty dashboard-panel-empty">
        <span className="mm-empty-title">No revenue recorded</span>
        <span className="mm-empty-text">
          Completed sales in the trailing {MONTHS_SHOWN} months were zero.
        </span>
      </div>
    );
  } else {
    const barGap = PLOT_W / bars.length;
    const barWidth = Math.max(barGap * 0.55, 8);
    const chartSummary =
      'Bar chart of monthly revenue across the last ' +
      `${bars.length} month${bars.length === 1 ? '' : 's'}, comparing ` +
      'current shop performance to the previous month.';
    content = (
      <>
        <div className="performance-chart" role="img" aria-label={chartSummary}>
          <svg
            viewBox={`0 0 ${CHART_W} ${CHART_H}`}
            className="performance-chart-svg"
            aria-hidden="true"
            focusable="false"
          >
            {/* Y-axis gridlines + tick labels */}
            {yTicks.map((tick, i) => {
              const y = PAD_T + (PLOT_H * i) / TICKS;
              return (
                <g key={tick}>
                  {i > 0 && (
                    <line
                      x1={PAD_L}
                      y1={y}
                      x2={CHART_W - PAD_R}
                      y2={y}
                      className="performance-chart-grid"
                    />
                  )}
                  <text
                    x={PAD_L - 8}
                    y={y + 4}
                    textAnchor="end"
                    className="performance-chart-tick"
                  >
                    {maxRevenue >= 1000 ? `$${(tick / 1000).toFixed(1)}k` : `$${Math.round(tick)}`}
                  </text>
                </g>
              );
            })}
            {/* Bars */}
            {bars.map((p, idx) => {
              const x = PAD_L + idx * barGap + (barGap - barWidth) / 2;
              const zero = Number(p.revenue) === 0;
              // A zero month still renders a visible baseline "0" tick (never
              // a missing bar). Bars are capped at BAR_MAX, matching the axis.
              const barH = zero ? 0 : (Math.max(Math.min(p.barPct, 100), 0) / 100) * (PLOT_H * BAR_MAX);
              const y = PAD_T + PLOT_H - barH;
              return (
                <g key={p.period}>
                  <rect
                    x={x}
                    y={y}
                    width={barWidth}
                    height={barH}
                    rx={3}
                    className={
                      zero
                        ? 'performance-chart-bar is-zero'
                        : 'performance-chart-bar'
                    }
                  />
                  <text
                    x={x + barWidth / 2}
                    y={y - 6}
                    textAnchor="middle"
                    className="performance-chart-bar-label"
                  >
                    {zero ? '0' : formatMoney(p.revenue)}
                  </text>
                  <text
                    x={x + barWidth / 2}
                    y={CHART_H - PAD_B + 18}
                    textAnchor="middle"
                    className="performance-chart-xlabel"
                  >
                    {p.label}
                  </text>
                </g>
              );
            })}
            {/* Y axis line */}
            <line
              x1={PAD_L}
              y1={PAD_T}
              x2={PAD_L}
              y2={PAD_T + PLOT_H}
              className="performance-chart-axis"
            />
          </svg>
        </div>

        {/* Accessible data-table alternative for the same series. */}
        <div className="mm-table-wrap dashboard-table-wrap">
          <table className="mm-table performance-table">
            <caption className="mm-visually-hidden">
              Monthly revenue comparison, including change vs the previous month
            </caption>
            <thead>
              <tr>
                <th scope="col">Month</th>
                <th scope="col" className="num">Sales</th>
                <th scope="col" className="num">Revenue</th>
                <th scope="col" className="num">Avg basket</th>
                <th scope="col" className="num">Vs last month</th>
              </tr>
            </thead>
            <tbody>
              {bars.map((p) => (
                <tr key={p.period}>
                  <td>{p.label}</td>
                  <td className="num">{p.sale_count}</td>
                  <td className="num">{formatMoney(p.revenue)}</td>
                  <td className="num">{formatMoney(p.avg_basket)}</td>
                  <td className="num">
                    {p.mom_revenue_pct === null ? (
                      '—'
                    ) : (
                      <span
                        className={`performance-mom performance-mom--${momTone(
                          p.mom_revenue_pct,
                        )}`}
                      >
                        {formatMomPct(p.mom_revenue_pct)}
                        <span className="mm-muted">
                          {' '}
                          ({formatMomDelta(p.mom_revenue)})
                        </span>
                      </span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="dashboard-panel-note">
          Revenue from completed sales, per calendar{' '}
          {bars.length === 1 ? 'month' : 'months'}; “Vs last month” compares
          each settled month to the one before it.
        </p>
      </>
    );
  }

  return (
    <section
      className="mm-card dashboard-panel dashboard-panel--performance"
      aria-labelledby="performance-title"
    >
      <header className="mm-card-header">
        <div>
          <h2 className="mm-card-title" id="performance-title">
            Shop Performance
          </h2>
          <p className="mm-card-subtitle">
            Monthly revenue vs the previous month, trailing {MONTHS_SHOWN} months
          </p>
        </div>
        <button type="button" className="mm-btn mm-btn--outline mm-btn--sm" onClick={() => void load()}>
          Refresh
        </button>
      </header>
      {content}
    </section>
  );
}
