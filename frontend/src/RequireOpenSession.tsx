import { useCallback, useEffect, useState } from 'react';
import { Outlet, useNavigate } from 'react-router-dom';
import { useAuth } from './authContext';
import { apiFetch } from './api';

/**
 * Route guard for the `/sales` screen (the session-state contract in
 * `docs/sales-session-api-contract.md`).
 *
 * On mount (and whenever the authenticated `me` changes) it asks the backend
 * `GET /api/sessions/current/` whether the current cashier has an open shift:
 *   - `open: true`  -> render the child `<Outlet />` (the Sales screen).
 *   - `open: false` -> replace-navigate to `/` (the dashboard) so a cashier
 *                      with no open shift never lands on the Sales screen.
 *
 * The session can be closed from another tab or window (e.g. a manager closes
 * the shift elsewhere), so the guard also re-validates whenever the tab
 * regains visibility (`visibilitychange` -> visible) or the window regains
 * `focus`. If that re-check reports `open: false`, it replace-navigates to
 * `/`, unmounting the Sales screen.
 *
 * Availability takes precedence over strictness: a transient failure of the
 * current-session check (network, 403 unauthenticated, 404) never redirects
 * and never blocks the Sales screen — the guard fails open so a reachable
 * Sales screen is preferred to a wrongly-empty one.
 */

interface CurrentSessionResponse {
  open: boolean;
  sessionId?: string;
  startedAt?: string;
  startingCash?: string;
}

/**
 * Fetch the current session for the signed-in cashier and resolve whether
 * access is allowed. Any error resolves to `true` (fail-open): a transient
 * check failure must not evict the cashier from an otherwise working Sales
 * screen. On the initial mount this is applied to the phase state; on a
 * re-validation it either confirms access or redirects away.
 */
async function fetchOpen(): Promise<boolean> {
  try {
    const data = await apiFetch<CurrentSessionResponse>('/api/sessions/current/');
    return data.open === true;
  } catch {
    return true;
  }
}

export function RequireOpenSession() {
  const { me, loading } = useAuth();
  const navigate = useNavigate();
  const [open, setOpen] = useState<boolean | null>(null);

  // Re-check the session and act on the result. Used both for the initial
  // mount check and for every focus/visibility re-validation.
  const revalidate = useCallback(async () => {
    const isOpen = await fetchOpen();
    setOpen(isOpen);
    if (!isOpen) {
      // The open shift is gone — send the cashier back to the dashboard and
      // unmount the Sales screen (this component's <Outlet />).
      navigate('/', { replace: true });
    }
  }, [navigate]);

  // Initial check: run on mount and whenever the signed-in user changes.
  useEffect(() => {
    if (loading || !me) return;
    void revalidate();
  }, [me, loading, revalidate]);

  // Re-validate when the cashier returns to the tab ('visibilitychange' to
  // visible) or the window regains focus. Close the session in another tab,
  // come back here, and the guard redirects to '/' automatically.
  useEffect(() => {
    const onVisibilityChange = () => {
      if (!document.hidden) void revalidate();
    };
    const onFocus = () => void revalidate();

    document.addEventListener('visibilitychange', onVisibilityChange);
    window.addEventListener('focus', onFocus);
    return () => {
      document.removeEventListener('visibilitychange', onVisibilityChange);
      window.removeEventListener('focus', onFocus);
    };
  }, [revalidate]);

  // A document-level Escape listener (not on the dialog div, which is not
  // focusable) so that dismissing works regardless of where focus sits while
  // the start-shift modal is shown. It is attached only while the modal is up
  // and is withheld while a start POST is in flight, mirroring the disabled
  // close button so dismissal cannot race the request.
  useEffect(() => {
    if (phase !== 'needs-session' || starting) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setPhase('redirecting');
    };
    document.addEventListener('keydown', onKeyDown);
    return () => document.removeEventListener('keydown', onKeyDown);
  }, [phase, starting]);

  const handleStartSession = async (event: FormEvent) => {
    event.preventDefault();
    const validation = startingCashError(cash);
    if (validation !== null) {
      setStartError(validation);
      return;
    }

    setStarting(true);
    setStartError('');
    try {
      await apiFetch<unknown>('/api/sessions/', {
        method: 'POST',
        body: JSON.stringify({ starting_cash: Number(cash).toFixed(2) }),
      });
      // Session started: the Sales screen is now reachable.
      setPhase('ready');
    } catch (error) {
      setStartError(startingCashErrorMessage(error));
    } finally {
      setStarting(false);
    }
  };

  if (phase === 'loading') {
    return <div className="mm-loading" role="status">Checking for an open session…</div>;
  }

  if (phase === 'redirecting') {
    return <Navigate to="/" replace />;
  }

  if (phase === 'needs-session') {
    return (
      <div
        className="mm-modal-backdrop"
        onMouseDown={(event) => {
          // Dismissing without completing the starting-cash set-up sends the
          // cashier back to the dashboard rather than trapping them on /sales.
          // Ignore backdrop clicks while a start POST is pending, consistent
          // with the disabled close button.
          if (starting) return;
          if (event.target === event.currentTarget) setPhase('redirecting');
        }}
      >
        <div
          className="mm-modal"
          role="dialog"
          aria-modal="true"
          aria-labelledby="start-shift-title"
        >
          <header className="mm-modal-header">
            <h2 id="start-shift-title">Start your shift</h2>
            <button
              type="button"
              className="mm-modal-close"
              aria-label="Close"
              onClick={() => setPhase('redirecting')}
              disabled={starting}
            >
              &times;
            </button>
          </header>
          <form
            className="mm-modal-body"
            onSubmit={(e) => void handleStartSession(e)}
            noValidate
          >
            <p className="mm-muted">
              There is no open session. Enter the cash you are placing in the
              drawer to begin the shift.
            </p>
            {startError && (
              <div className="mm-alert mm-alert--danger" role="alert">
                {startError}
              </div>
            )}
            <label className="mm-field">
              <span>Starting cash</span>
              <input
                className="mm-input"
                type="number"
                min={0}
                step="0.01"
                inputMode="decimal"
                value={cash}
                onChange={(e) => setCash(e.target.value)}
                placeholder="0.00"
                aria-label="Starting cash"
                aria-required="true"
                autoFocus
              />
            </label>
            <footer className="mm-modal-footer">
              <button
                type="submit"
                className="mm-btn mm-btn--primary"
                disabled={starting}
              >
                {starting ? 'Starting…' : 'Start shift'}
              </button>
            </footer>
          </form>
        </div>
      </div>
    );
  }

  // open === true (or a fail-open transient error resolved the check as true).
  return <Outlet />;
}
