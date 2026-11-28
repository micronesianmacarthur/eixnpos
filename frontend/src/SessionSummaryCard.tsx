import { Icon } from './Icon';
import type { DashboardOpenSession, DashboardSession } from './DashboardTypes';
import {
  businessDayDifference,
  daysLabel,
  formatDateTime,
  formatMoney,
} from './dashboardFormat';
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
 *
 * Sessions are closed MANUALLY only. A shift left open at midnight stays open
 * on the business day it started, so the card also lists `previous_day_sessions`
 * with a "Close & reconcile" link into the close flow. Without that list the
 * card would claim "No open shift" while yesterday's drawer is still open.
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
      <a className="mm-btn mm-btn--sm dashboard-session-close" href="/close">
        Close &amp; reconcile
      </a>
    </div>
  );
}

function previousDaySessionRow(
  session: DashboardOpenSession,
  today: string,
  businessTimezone?: string | null,
) {
  const openedLabel = formatDateTime(session.opened_at_local, businessTimezone);
  const age = businessDayDifference(session.business_day, today);
  return (
    <li key={session.session_id} className="dashboard-session-stale-item">
      <div className="dashboard-session-stale-text">
        <span className="dashboard-session-stale-register">
          Register {session.register_name}
        </span>
        <span className="mm-muted dashboard-session-stale-meta">
          Business day {session.business_day} · opened {openedLabel}
          {age > 0 ? ` · ${daysLabel(age)} ago` : ''}
          {session.opened_by?.username
            ? ` · Opener ${session.opened_by.username}`
            : ''}
        </span>
      </div>
      <a
        className="mm-btn mm-btn--sm dashboard-session-close-link"
        href={`/close?session=${session.session_id}`}
      >
        Close &amp; reconcile
      </a>
    </li>
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
  const previousDaySessions = session?.previous_day_sessions ?? [];
  const today = session?.business_day ?? '';
  const hasOpen = !!session?.open && sessions.length > 0;

  return (
    <section
      className="mm-card dashboard-session"
      aria-label={hasOpen ? 'Open shifts' : 'Register sessions'}
    >
      {hasOpen ? (
        sessions.length === 1 ? (
          singleSessionRow(sessions[0], businessTimezone)
        ) : (
          <>
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
          </>
        )
      ) : (
        <div className="dashboard-session-header">
          <span className="mm-icon-tile">
            <Icon name="close" size={22} />
          </span>
          <div className="dashboard-session-title-wrapper">
            <h2 className="dashboard-session-title">No open shift</h2>
            <p className="mm-muted dashboard-session-subtitle">
              {previousDaySessions.length > 0
                ? "Today's register is closed, but an earlier shift is still open. Close and reconcile it below."
                : 'Open a register to start selling.'}
            </p>
          </div>
        </div>
      )}

      {previousDaySessions.length > 0 && (
        <div className="dashboard-session-stale">
          <h3 className="dashboard-session-stale-title">
            {previousDaySessions.length === 1
              ? 'Unclosed shift from an earlier day'
              : `${previousDaySessions.length} unclosed shifts from earlier days`}
          </h3>
          <p className="dashboard-session-stale-help">
            Shifts stay open until a cashier, manager, or admin closes and
            reconciles them — they never close automatically at midnight.
          </p>
          <ul className="dashboard-session-stale-list">
            {previousDaySessions.map((s) =>
              previousDaySessionRow(s, today, businessTimezone),
            )}
          </ul>
        </div>
      )}

      {hasOpen && (
        <p className="dashboard-session-hint">
          Close and reconcile the drawer when the shift ends. A shift stays open
          — even overnight — until someone closes it manually.
        </p>
      )}
    </section>
  );
}
