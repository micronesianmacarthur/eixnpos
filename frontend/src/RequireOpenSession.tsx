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

  // While the first check is in flight (or auth isn't resolved yet), render a
  // lightweight placeholder instead of the guarded screen, so the redirect
  // (or the confirmed allow) lands before the Sales screen mounts.
  if (loading || !me || open === null) {
    return (
      <div className="mm-loading" role="status">
        Checking for an open session…
      </div>
    );
  }

  // open === true (or a fail-open transient error resolved the check as true).
  return <Outlet />;
}
