import { useEffect, useState } from 'react';
import { Outlet, useLocation, useNavigate } from 'react-router-dom';
import { apiFetch } from './api';
import StartSessionModal from './StartSessionModal';

/** The subset of a Session the guard reads from the list endpoint. */
interface SessionRecord {
  id: number;
  status: string;
}

/**
 * Route guard for transaction-capable screens (Sales, Sales Manager, Close).
 *
 * Mounts when navigation to any of the wrapped routes occurs and checks whether
 * the current cashier has an open session. The authoritative check is the
 * session list filtered by `status === 'open'` (the `/api/sessions/current/`
 * endpoint does not exist in the current tree).
 *
 * - Session open → render the requested screen (`<Outlet/>`).
 * - No open session → render `StartSessionModal` *instead* of the target
 *   screen, so the transaction screen never opens. Starting a session flips
 *   to the requested screen (continues the original navigation); dismissing
 *   the modal navigates back to the screen the user came from.
 *
 * Wrapping all transaction routes under this single guard is what centralizes
 * the gate: any future transaction menu added inside AppRouter's guarded block
 * is covered with no extra wiring.
 */
export default function RequireOpenSession() {
  const navigate = useNavigate();
  const location = useLocation();
  const [checking, setChecking] = useState(true);
  const [open, setOpen] = useState(false);

  // Re-check whenever the active guarded route changes (e.g. the cashier
  // closed the session on /close, then navigates to /sales) so stale state
  // never lets a transaction screen open without an open session.
  useEffect(() => {
    let cancelled = false;
    setChecking(true);
    apiFetch<SessionRecord[]>('/api/sessions/')
      .then((sessions) => {
        if (cancelled) return;
        setOpen(sessions.some((s) => s.status === 'open'));
      })
      .catch(() => {
        // Fail open: if the check cannot reach the backend we must not lock a
        // cashier out of sales. The backend authoritatively rejects mutations
        // when no session is open anyway.
        if (cancelled) return;
        setOpen(true);
      })
      .finally(() => {
        if (!cancelled) setChecking(false);
      });
    return () => {
      cancelled = true;
    };
  }, [location.pathname]);

  const handleClose = () => {
    // Keep the user on the screen they were on: go back in history when we
    // have prior history, otherwise fall back to the dashboard.
    const idx = (window.history.state as { idx?: number } | null)?.idx;
    if (typeof idx === 'number' && idx > 0) {
      navigate(-1);
    } else {
      navigate('/');
    }
  };

  if (checking) {
    return <p style={{ padding: '2rem' }}>Checking for an open session…</p>;
  }

  if (open) {
    return <Outlet />;
  }

  return <StartSessionModal onSuccess={() => setOpen(true)} onClose={handleClose} />;
}
