import { useEffect, useState, type FormEvent } from 'react';
import { Navigate, Outlet } from 'react-router-dom';
import { apiFetch, ApiError } from './api';
import { useAuth } from './authContext';

/**
 * Guard for the `/sales` route (the flow defined by t_c661dbef / the
 * session-state contract in `docs/sales-session-api-contract.md`).
 *
 * On mount it asks the backend `GET /api/sessions/current/` whether an open
 * shift already exists:
 *   - open session   -> redirect to the home/dashboard route `/`
 *                       (the Sales screen must not render while a shift is open).
 *   - no open session -> show a "start your shift" modal that collects the
 *                       starting cash and POSTs it to `/api/sessions/`; only
 *                       then does the Sales screen render.
 *   - request failure -> never block or trap the user: fall through and render
 *                       the Sales screen with a non-fatal banner.
 */
const MAX_STARTING_CASH = 1000.0;

const currency = new Intl.NumberFormat('en-US', {
  style: 'currency',
  currency: 'USD',
});

type Phase = 'loading' | 'needs-session' | 'redirecting' | 'ready';

interface CurrentSession {
  open: boolean;
  sessionId?: string;
  startedAt?: string;
  startingCash?: string;
}

/**
 * Extract a human-readable `starting_cash` validation message from the DRF
 * error payload. Drf returns per-field errors as an array of `ErrorDetail`
 * (either `[{ "message": ..., "code": ... }]` or plain strings).
 */
function startingCashErrorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    const data = error.data;
    if (data && typeof data === 'object') {
      const field = (data as Record<string, unknown>).starting_cash;
      if (Array.isArray(field) && field.length > 0) {
        const first = field[0];
        if (first && typeof first === 'object' && 'message' in first) {
          const message = (first as { message: unknown }).message;
          if (typeof message === 'string' && message) return message;
        }
        if (typeof first === 'string' && first) return first;
      }
    }
    return error.message;
  }
  return error instanceof Error ? error.message : 'Could not start the session.';
}

/** Validate starting cash locally; returns an error message or null when valid. */
function startingCashError(value: string): string | null {
  const trimmed = value.trim();
  if (trimmed === '') return 'Enter a starting cash amount.';
  const parsed = Number(trimmed);
  if (!Number.isFinite(parsed)) return 'Starting cash must be a number.';
  if (parsed < 0) return 'Starting cash cannot be negative.';
  if (parsed > MAX_STARTING_CASH) {
    return `Starting cash cannot exceed ${currency.format(MAX_STARTING_CASH)}.`;
  }
  return null;
}

function RequireOpenSession() {
  const { me, loading } = useAuth();
  const [phase, setPhase] = useState<Phase>('loading');
  const [checkWarning, setCheckWarning] = useState('');

  const [cash, setCash] = useState('');
  const [starting, setStarting] = useState(false);
  const [startError, setStartError] = useState('');

  useEffect(() => {
    if (loading || !me) return;
    let cancelled = false;

    const check = async () => {
      try {
        const data = await apiFetch<CurrentSession>('/api/sessions/current/');
        if (cancelled) return;
        setPhase(data.open === true ? 'redirecting' : 'needs-session');
      } catch (error) {
        // A transient failure (403/404/network) must not redirect or trap the
        // user. Fall through to the Sales screen with a non-fatal banner.
        if (cancelled) return;
        setCheckWarning(
          error instanceof ApiError
            ? error.message
            : 'Could not check for an open session. Sales will load anyway.',
        );
        setPhase('ready');
      }
    };

    void check();
    return () => {
      cancelled = true;
    };
  }, [me, loading]);

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
      <div className="mm-modal-backdrop">
        <div className="mm-modal" role="dialog" aria-modal="true" aria-labelledby="start-shift-title">
          <header className="mm-modal-header">
            <h2 id="start-shift-title">Start your shift</h2>
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

  // 'ready': an open session exists (we just started it) or the check failed.
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

export default RequireOpenSession;
