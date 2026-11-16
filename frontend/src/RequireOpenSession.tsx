import { useCallback, useEffect, useRef, useState } from 'react';
import { Outlet, useNavigate } from 'react-router-dom';
import { useAuth } from './authContext';
import { apiFetch, ApiError } from './api';
import StartSessionModal, { type StartedSession } from './StartSessionModal';

/**
 * Route guard for the transaction screens (`/sales`, `/sales-manager`,
 * `/close`) wrapped by `<Route element={<RequireOpenSession />}>` — the
 * session-state contract in `docs/sales-session-api-contract.md`.
 *
 * It asks the backend `GET /api/sessions/current/` whether the current
 * cashier has an open shift:
 *   - `open: true`  -> render the child `<Outlet />` (the guarded screen).
 *   - `open: false` on first arrival -> show the "start your shift" modal
 *                      (the reusable `StartSessionModal`, which collects the
 *                      starting cash and POSTs it to `/api/sessions/`); only
 *                      then does the guarded screen render. If the cashier
 *                      dismisses the modal without completing the
 *                      starting-cash set-up, redirect to the home/dashboard
 *                      route `/`. This is what lets a cashier who clicks, say,
 *                      "Sales" while no shift is open get the modal instead of
 *                      being silently bounced to the dashboard.
 *   - A re-validation that finds a previously-open session now closed (e.g. a
 *     manager closed the shift in another tab) redirects to `/`, unmounting
 *     the guarded screen — the cashier no longer has an open shift and must
 *     not keep using the transaction screens.
 *   - request failure -> never block or trap the user: fall through and
 *                        render the guarded screen with a non-fatal banner.
 *
 * The modal lives here (not on the guarded page) so every wrapped route gets
 * the same no-session treatment and can never render its content without an
 * open shift.
 */
/** `loading` -> first check in flight; otherwise see the named phase. */
type Phase = 'loading' | 'needs-session' | 'ready';

interface CurrentSessionResponse {
  open: boolean;
  sessionId?: string;
  startedAt?: string;
  startingCash?: string;
}

export function RequireOpenSession() {
  const { me, loading } = useAuth();
  const navigate = useNavigate();

  const [phase, setPhase] = useState<Phase>('loading');
  const [checkWarning, setCheckWarning] = useState('');
  // True once the cashier has already rendered the guarded screen with an open
  // session. Before that, "no open session" means prompt for starting cash via
  // the modal; after that, a re-validation reporting a closed session means the
  // shift was ended elsewhere and must evict the cashier to the dashboard.
  const entered = useRef(false);

  // Ask the backend whether an open shift exists. A transient failure (network,
  // 403 unauthenticated, 404) resolves as open (fail-open) with a warning so the
  // guarded screen stays reachable instead of the cashier being wrongly bounced.
  const check = useCallback(async (): Promise<{
    open: boolean;
    error?: string;
  }> => {
    try {
      const data = await apiFetch<CurrentSessionResponse>('/api/sessions/current/');
      return { open: data.open === true };
    } catch (error) {
      return {
        open: true,
        error:
          error instanceof ApiError
            ? error.message
            : 'Could not check for an open session. Sales will load anyway.',
      };
    }
  }, []);

  // Re-check the session and decide the next phase. Used for the initial mount
  // check and for every focus/visibility re-validation.
  const revalidate = useCallback(async () => {
    const result = await check();
    if (result.error) {
      // A transient check failure must never block or bounce the cashier:
      // render the guarded screen with a non-fatal banner instead.
      setCheckWarning(result.error);
      setPhase('ready');
      entered.current = true;
      return;
    }
    if (result.open) {
      setPhase('ready');
      entered.current = true;
      return;
    }
    // No open shift.
    if (entered.current) {
      // The cashier had already passed the gate with an open shift that was
      // since closed elsewhere — evict them to the dashboard so they cannot
      // keep using the transaction screens without an open shift.
      entered.current = false;
      navigate('/', { replace: true });
    } else {
      // First arrival with no open shift: offer the starting-cash modal rather
      // than redirecting, so the guard opens the modal instead of bouncing the
      // cashier straight to the dashboard.
      setPhase('needs-session');
    }
  }, [check, navigate]);

  // Initial check: run once guarded screen first mounts and whenever the
  // signed-in user changes.
  useEffect(() => {
    if (loading || !me) return;
    void revalidate();
  }, [me, loading, revalidate]);

  // Re-validate when the cashier returns to the tab ('visibilitychange' to
  // visible) or the window regains focus. Close the session in another tab,
  // come back here, and the guard evicts to '/' automatically.
  useEffect(() => {
    if (loading || !me) return;
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
  }, [me, loading, revalidate]);

  // Dismissing the start-shift modal, or the modal defining the session start
  // (success), both leave the guard decision to the caller. Dismissal without
  // a completed start returns the cashier to the dashboard.
  const dismiss = useCallback(() => {
    navigate('/', { replace: true });
  }, [navigate]);

  // A successful start (POST /api/sessions/) renders the guarded screen
  // without leaving the route.
  const handleStarted = useCallback((_session: StartedSession) => {
    setPhase('ready');
    entered.current = true;
  }, []);

  if (loading || !me || phase === 'loading') {
    return (
      <div className="mm-loading" role="status">
        Checking for an open session…
      </div>
    );
  }

  if (phase === 'needs-session') {
    // Dismissal without a completed start returns the cashier to the
    // dashboard; a successful start renders the guarded screen in place. All
    // dismissal modes (Escape, backdrop, close) and the disabled-while-starting
    // contract are owned by the reusable modal.
    return <StartSessionModal onSuccess={handleStarted} onClose={dismiss} />;
  }

  // 'ready': an open shift exists (we just started it) or the check failed open.
  return (
    <>
      {checkWarning && (
        <div className="mm-alert mm-alert--danger mm-alert--banner" role="alert">
          {checkWarning}
        </div>
      )}
      <Outlet />
    </>
  );
}
