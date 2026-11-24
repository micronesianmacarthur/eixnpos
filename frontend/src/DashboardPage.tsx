import { useCallback, useEffect, useState } from 'react';
import BusinessInfoForm from './BusinessInfoForm';
import { useAuth } from './authContext';
import { DashboardKpiRow } from './DashboardKpis';
import { SessionSummaryCard } from './SessionSummaryCard';
import type { DashboardOverview, DashboardSession, TodayKpi } from './DashboardTypes';
import { apiFetch, ApiError } from './api';
import { Icon } from './Icon';
import { formatDateTime } from './dashboardFormat';
import DashboardAnalyticsPanels from './dashboard/DashboardAnalyticsPanels';
import LastBackupPanel from './dashboard/LastBackupPanel';
import './DashboardPage.css';

/**
 * Manager dashboard landing page (`/`).
 *
 * Shows the headline numbers a manager needs at a glance — sales today,
 * today's revenue, cash sales, invoices issued, and the current open shift
 * (opened when + by whom) — all backed by the server aggregation service
 * (`/api/dashboard/`, contract §5.7). The server is the sole authority on
 * "today" and all money/date formatting follows the contract primitives.
 *
 * Contract §8.9 staleness: the header shows a "Data from <time>" pill and a
 * manual refresh; every 30s the page re-requests the composite so the numbers
 * never silently go stale (S6).
 *
 * The route is the app landing for every role, but the underlying endpoints
 * are manager-gated (`IsDashboardReader`). Cashiers therefore see a friendly
 * non-manager notice instead of hitting an API that would 403 them.
 */

/** Roles allowed to read the manager dashboard data (contract §7.2). */
const MANAGER_ROLES = ['manager', 'admin'];

/** Auto-refresh interval (ms); matches contract §8.9's 30s staleness window. */
const REFRESH_MS = 30_000;

export default function DashboardPage() {
  const { me, refresh } = useAuth();
  const isManager = !!me && MANAGER_ROLES.includes(me.user.role);

  const [overview, setOverview] = useState<DashboardOverview | null>(null);
  const [loading, setLoading] = useState(isManager);
  // One tri-state per section so a single bad endpoint doesn't blank the page.
  const [todayError, setTodayError] = useState<string | null>(null);
  const [sessionError, setSessionError] = useState<string | null>(null);
  const [lastUpdatedAt, setLastUpdatedAt] = useState<string | null>(null);

  const lowStockCount = overview?.inventory?.low_stock_count ?? 0;

  const load = useCallback(() => {
    if (!isManager) return;
    setLoading(true);
    setTodayError(null);
    setSessionError(null);
    setLastUpdatedAt(
      (prev) => prev ?? new Date().toISOString(),
    );
    apiFetch<DashboardOverview>('/api/dashboard/')
      .then((data) => {
        setOverview(data);
        setLastUpdatedAt(data.generated_at);
      })
      .catch((err: unknown) => {
        const message =
          err instanceof ApiError ? err.message : 'Failed to load the dashboard.';
        // The composite endpoint surfaces today + session together; if the
        // whole call fails, surface it on the KPI row (primary) and session.
        setTodayError(message);
        setSessionError(message);
      })
      .finally(() => setLoading(false));
  }, [isManager]);

  useEffect(() => {
    load();
  }, [load]);

  // Contract §8.9: keep the numbers fresh — re-request every 30s while the
  // dashboard is mounted. A manual refresh is also offered in the header.
  useEffect(() => {
    if (!isManager) return;
    const id = window.setInterval(load, REFRESH_MS);
    return () => window.clearInterval(id);
  }, [isManager, load]);

  const scrollToLowStock = useCallback(() => {
    document
      .getElementById('low-stock-panel')
      ?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }, []);

  const today: TodayKpi | null = overview?.today ?? null;
  const session: DashboardSession | null =
    overview && (overview.session ?? null);
  const businessTimezone = overview?.business_timezone ?? me?.business_timezone ?? null;

  return (
    <div className="mm-page">
      <header className="mm-page-header">
        <h1 className="mm-page-title">Dashboard</h1>
        {me?.business_info_complete === false ? (
          <p className="mm-page-subtitle">
            Complete your business information so it appears on receipts.
          </p>
        ) : (
          <p className="mm-page-subtitle">
            {overview?.business_day
              ? `Business day ${overview.business_day}`
              : 'A live overview of your shop.'}
          </p>
        )}
      </header>

      {!me?.business_info_complete && (
        <section className="dashboard-setup">
          <BusinessInfoForm onSaved={refresh} />
        </section>
      )}

      {isManager ? (
        <>
          <DashboardKpiRow
            today={today}
            loading={loading && !overview}
            error={todayError}
            lowStockCount={lowStockCount}
            onLowStockClick={scrollToLowStock}
          />
          {todayError && (
            <p className="dashboard-error-message mm-alert mm-alert--banner" role="alert">
              {todayError}
            </p>
          )}

          {/* Empty-sales banner (contract §8.7 / S9): only when the payload
              actually loaded AND there are zero completed sales today — never
              shown for a load failure (that's the error row above). */}
          {!loading && today && today.sales_count === 0 && (
            <section className="dashboard-empty-sales mm-alert mm-alert--info" role="status">
              <Icon name="sparkles" size={18} />
              <span>No completed sales today — start a sale or check open shifts.</span>
            </section>
          )}

          <SessionSummaryCard
            session={session}
            loading={loading && !overview}
            error={sessionError}
            onRetry={load}
            businessTimezone={businessTimezone}
          />

          {/* Last backup status + quick action (B1): "when was the system
              last backed up", with a one-click Backup now button. Placed
              below the KPI row, above the analytics panels. */}
          <LastBackupPanel businessTimezone={businessTimezone} />

          {/* Analytics panels (t_bf2c1a7e): top items, monthly comparison,
              low stock. Each handles its own loading/error/empty states. */}
          <DashboardAnalyticsPanels />
        </>
      ) : (
        <section className="mm-card dashboard-nonmanager">
          <span className="mm-icon-tile mm-icon-tile--info">
            <Icon name="dashboard" size={22} />
          </span>
          <div>
            <h2 className="mm-card-title">Manager dashboard</h2>
            <p className="mm-muted">
              Live sales, cash, invoice, and session overviews are available to
              managers and admins.
            </p>
          </div>
        </section>
      )}

      {isManager && (
        <div className="dashboard-stale">
          <p className="dashboard-stale-copy">
            {lastUpdatedAt
              ? `Data from ${formatDateTime(lastUpdatedAt, businessTimezone)}`
              : 'Data not yet loaded'}
          </p>
          <button type="button" className="mm-btn mm-btn--outline mm-btn--sm" onClick={load}>
            Refresh
          </button>
        </div>
      )}
    </div>
  );
}
