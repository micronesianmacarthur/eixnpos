import { Icon } from './Icon';
import type { DashboardSession } from './DashboardTypes';
import { formatDateTime, formatMoney } from './dashboardFormat';
import './DashboardPage.css';

/**
 * Current open shift card.
 *
 * Shows when the session was opened and by whom (the manager-facing question
 * "when was the session opened and who opened it"). Rendered only for the
 * manager dashboard (the API that feeds it is manager-gated).
 */
export function SessionSummaryCard({
  session,
  loading,
  error,
  onRetry,
  businessTimezone,
}: {
  session: DashboardSession | null;
  loading: boolean;
  error: string | null;
  onRetry: () => void;
  /** IANA zone name used to format the "open since" wall clock (S2). */
  businessTimezone?: string | null;
}) {
  if (error && !session) {
    return (
      <section className="mm-card dashboard-session" role="alert">
        <p className="dashboard-session-error">Could not load the current session.</p>
        <button type="button" className="mm-btn mm-btn--sm" onClick={onRetry}>
          Retry
        </button>
      </section>
    );
  }

  if (loading) {
    return (
      <section className="mm-card dashboard-session" aria-busy="true">
        <div className="dashboard-session-header">
          <span className="mm-icon-tile mm-icon-tile--success">
            <Icon name="close" size={22} />
          </span>
          <div className="dashboard-session-title-wrapper">
            <span className="dashboard-session-skeleton" aria-hidden="true" />
          </div>
        </div>
      </section>
    );
  }

  if (!session) {
    return (
      <section className="mm-card dashboard-session">
        <div className="dashboard-session-header">
          <span className="mm-icon-tile">
            <Icon name="close" size={22} />
          </span>
          <div className="dashboard-session-title-wrapper">
            <h2 className="dashboard-session-title">No open shift</h2>
            <p className="mm-muted dashboard-session-subtitle">
              Open a register to start selling.
            </p>
          </div>
        </div>
      </section>
    );
  }

  if (!session.open) {
    return (
      <section className="mm-card dashboard-session">
        <div className="dashboard-session-header">
          <span className="mm-icon-tile">
            <Icon name="close" size={22} />
          </span>
          <div className="dashboard-session-title-wrapper">
            <h2 className="dashboard-session-title">No open shift</h2>
            <p className="mm-muted dashboard-session-subtitle">
              Open a register to start selling.
            </p>
          </div>
        </div>
      </section>
    );
  }

  const openedLabel = formatDateTime(session.started_at_local, businessTimezone);
  return (
    <section className="mm-card dashboard-session">
      <div className="dashboard-session-header">
        <span className="mm-icon-tile mm-icon-tile--success">
          <Icon name="close" size={22} />
        </span>
        <div className="dashboard-session-title-wrapper">
          <h2 className="dashboard-session-title">
            Open since {openedLabel}
          </h2>
          <p className="mm-muted dashboard-session-subtitle">
            Register {session.register?.identifier ?? session.register?.name ?? ''}
            · Opener {session.cashier?.username ?? '—'}
            {session.starting_cash
              ? ` · Starting cash ${formatMoney(session.starting_cash)}`
              : ''}
          </p>
        </div>
      </div>
    </section>
  );
}
