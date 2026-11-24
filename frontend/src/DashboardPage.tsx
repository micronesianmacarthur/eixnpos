import { useCallback, useEffect, useState } from 'react';
import BusinessInfoForm from './BusinessInfoForm';
import { useAuth } from './authContext';
import { DashboardKpiRow } from './DashboardKpis';
import { SessionSummaryCard } from './SessionSummaryCard';
import type { DashboardOverview, DashboardSession, TodayKpi } from './DashboardTypes';
import { apiFetch, ApiError } from './api';
import { Icon } from './Icon';
import DashboardAnalyticsPanels from './dashboard/DashboardAnalyticsPanels';
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
 * The route is the app landing for every role, but the underlying endpoints
 * are manager-gated (`IsDashboardReader`). Cashiers therefore see a friendly
 * non-manager notice instead of hitting an API that would 403 them.
 */

/** Roles allowed to read the manager dashboard data (contract §7.2). */
const MANAGER_ROLES = ['manager', 'admin'];

export default function DashboardPage() {
  const { me, refresh } = useAuth();
  const isManager = !!me && MANAGER_ROLES.includes(me.user.role);

  const [overview, setOverview] = useState<DashboardOverview | null>(null);
  const [loading, setLoading] = useState(isManager);
  // One tri-state per section so a single bad endpoint doesn't blank the page.
  const [todayError, setTodayError] = useState<string | null>(null);
  const [sessionError, setSessionError] = useState<string | null>(null);

  const load = useCallback(() => {
    if (!isManager) return;
    setLoading(true);
    setTodayError(null);
    setSessionError(null);
    apiFetch<DashboardOverview>('/api/dashboard/')
      .then((data) => {
        setOverview(data);
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

  const today: TodayKpi | null = overview?.today ?? null;
  const session: DashboardSession | null =
    overview && (overview.session ?? null);

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

          <DashboardKpiRow today={today} loading={loading && !overview} />
          {todayError && (
            <p className="dashboard-error-message mm-alert mm-alert--banner" role="alert">
              {todayError}
            </p>
          )}

          <SessionSummaryCard
            session={session ?? null}
            loading={loading && !overview}
            error={sessionError}
            onRetry={load}
          />

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
>>>>>>> wt/t_f57598cd
    </div>
  );
}
