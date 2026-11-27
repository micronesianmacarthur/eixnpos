import { useCallback, useEffect, useMemo, useState } from 'react';
import { fetchMonthly, formatMoney, type MonthlyPayload } from './data';

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
const TICKS = 4;

const MONTH_ABBR = [
  'Jan',
  'Feb',
  'Mar',
  'Apr',
  'May',
  'Jun',
  'Jul',
  'Aug',
  'Sep',
  'Oct',
  'Nov',
  'Dec',
];

/**
 * X-axis tick label for a `YYYY-MM` period: the month abbreviation only.
 * The year is deliberately dropped — the panel already states its trailing
 * window in the subtitle, and 12 short labels stay legible where
 * "Mon YYYY" would collide.
 */
function monthTickLabel(period: string, fallback: string): string {
  const match = /^\d{4}-(\d{2})$/.exec(period);
  const monthIndex = match ? Number(match[1]) - 1 : NaN;
  if (monthIndex >= 0 && monthIndex < MONTH_ABBR.length) {
    return MONTH_ABBR[monthIndex];
  }
  return fallback.replace(/\s+\d{4}$/, '');
}

/** Base-10 compact money for axis ticks: $950, $4.3K, $1.2M (never 1e+06). */
function formatAxisMoney(value: number): string {
  const abs = Math.abs(value);
  const compact = (scaled: number) => {
    const rounded = Math.round(scaled * 10) / 10;
    return Number.isInteger(rounded) ? String(rounded) : rounded.toFixed(1);
  };
  if (abs >= 1_000_000) return `$${compact(value / 1_000_000)}M`;
  if (abs >= 1_000) return `$${compact(value / 1_000)}K`;
  return `$${Math.round(value).toLocaleString('en-US')}`;
}

/**
 * Monthly shop-performance comparison — revenue per trailing calendar month
 * (month-over-month). The sibling run builds the headline KPI cards; this
 * panel answers "how are we doing vs last month?" with an accessible area
 * chart. Money is formatted (never computed) client-side.
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

  // Scale the area to the max revenue so the peak sits on the top gridline.
  const { months, maxRevenue } = useMemo(() => {
    const periods = data?.periods ?? [];
    const max = Math.max(0, ...periods.map((p) => Number(p.revenue) || 0));
    const scaled = periods.map((p) => ({
      ...p,
      tickLabel: monthTickLabel(p.period, p.label),
    }));
    return { months: scaled, maxRevenue: max };
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
  } else if (months.every((b) => Number(b.revenue) === 0)) {
    content = (
      <div className="mm-empty dashboard-panel-empty">
        <span className="mm-empty-title">No revenue recorded</span>
        <span className="mm-empty-text">
          Completed sales in the trailing {MONTHS_SHOWN} months were zero.
        </span>
      </div>
    );
  } else {
    const baseY = PAD_T + PLOT_H;
    const stepX = months.length > 1 ? PLOT_W / (months.length - 1) : 0;
    const points = months.map((p, idx) => {
      const revenue = Number(p.revenue) || 0;
      const ratio = maxRevenue > 0 ? Math.min(Math.max(revenue / maxRevenue, 0), 1) : 0;
      return {
        ...p,
        x: months.length > 1 ? PAD_L + idx * stepX : PAD_L + PLOT_W / 2,
        y: baseY - ratio * PLOT_H,
      };
    });
    const linePath = points
      .map((p, idx) => `${idx === 0 ? 'M' : 'L'}${p.x.toFixed(2)} ${p.y.toFixed(2)}`)
      .join(' ');
    const areaPath = `${linePath} L${points[points.length - 1].x.toFixed(2)} ${baseY.toFixed(
      2,
    )} L${points[0].x.toFixed(2)} ${baseY.toFixed(2)} Z`;
    const chartSummary =
      'Area chart of monthly revenue across the last ' +
      `${months.length} month${months.length === 1 ? '' : 's'}, comparing ` +
      'current shop performance to the previous month.';
    content = (
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
                  {formatAxisMoney(tick)}
                </text>
              </g>
            );
          })}
          {/* Revenue area + line */}
          <path d={areaPath} className="performance-chart-area" />
          <path d={linePath} className="performance-chart-line" />
          {/* Point markers carry the per-month value tooltip. */}
          {points.map((p) => (
            <circle
              key={p.period}
              cx={p.x}
              cy={p.y}
              r={3.5}
              className="performance-chart-point"
            >
              <title>{`${p.tickLabel}: ${formatMoney(p.revenue)}`}</title>
            </circle>
          ))}
          {/* X-axis month labels (no year) */}
          {points.map((p) => (
            <text
              key={p.period}
              x={p.x}
              y={CHART_H - PAD_B + 18}
              textAnchor="middle"
              className="performance-chart-xlabel"
            >
              {p.tickLabel}
            </text>
          ))}
          {/* Y axis line */}
          <line
            x1={PAD_L}
            y1={PAD_T}
            x2={PAD_L}
            y2={baseY}
            className="performance-chart-axis"
          />
        </svg>
      </div>
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
            {/*Shop */}Performance
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
