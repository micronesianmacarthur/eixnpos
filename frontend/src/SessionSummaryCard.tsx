import { Icon } from './Icon';
import type { DashboardOpenSession, DashboardSession } from './DashboardTypes';
import { formatDateTime, formatMoney } from './dashboardFormat';
import './DashboardPage.css';

/**
 * Current open shift(s) card.
 *
 * Shows the open shift(s) for the business day — one card per open register
 * (opened when + by whom), or a compact "No open shift" state when none is
 * open. The card renders three states: zero sessions, a single session (the
 * historical single-card layout), and multiple sessions (a list/accordion).
 * The dashboard is available to every authenticated role: managers and admins
 * see shifts on all registers, while cashiers see only registers they may
 * operate (`IsDashboardReader` requires only authentication).
 */
function singleSessionRow(session: DashboardOpenSession, businessTimezone?: string | null) {
  const openedLabel = formatDateTime(
    session.opened_at_local,
    businessTimezone,
  );
  const openers = (session.cashiers ?? []).slice().sort().join(', ');
  return (
    <div className="dashboard-session-header">
      <span className="mm-icon-tile mm-icon-tile--success">
        <Icon name="close" size={22} />
      </span>
      <div className="dashboard-session-title-wrapper">
        <h2 className="dashboard-session-title">Open since {openedLabel}</h2>
        <p className="mm-muted dashboard-session-subtitle">
          Register {session.register_name}
          {session.opened_by?.username
            ? ` · Opener ${session.opened_by.username}`
            : ''}
          {openers ? ` · Cashiers ${openers}` : ''}
          {session.starting_cash
            ? ` · Starting cash ${formatMoney(session.starting_cash)}`
            : ''}
        </p>
      </div>
    </div>
  );
}

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

  const sessions = session?.sessions ?? [];
  const hasOpen = !!session?.open && sessions.length > 0;

  if (!session || !hasOpen) {
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

  // Single open session: keep the historical single-card presentation.
  if (sessions.length === 1) {
    return (
      <section className="mm-card dashboard-session">
        {singleSessionRow(sessions[0], businessTimezone)}
      </section>
    );
  }

  // Multiple open shifts: one row per open register.
  return (
    <section className="mm-card dashboard-session" aria-label="Open shifts">
      <div className="dashboard-session-header">
        <span className="mm-icon-tile mm-icon-tile--success">
          <Icon name="close" size={22} />
        </span>
        <div className="dashboard-session-title-wrapper">
          <h2 className="dashboard-session-title">
            {sessions.length} open shifts
          </h2>
          <p className="mm-muted dashboard-session-subtitle">
            Across {sessions.length} registers
          </p>
        </div>
      </div>
      <ul className="dashboard-session-list">
        {sessions.map((s) => (
          <li key={s.session_id} className="dashboard-session-list-item">
            {singleSessionRow(s, businessTimezone)}
          </li>
        ))}
      </ul>
    </section>
  );
}
