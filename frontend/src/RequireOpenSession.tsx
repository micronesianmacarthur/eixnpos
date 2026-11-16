import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react';
import { Outlet, useNavigate } from 'react-router-dom';
import { useAuth } from './authContext';
import { apiFetch, ApiError } from './api';

/**
 * Route guard for the transaction screens (`/sales`, `/sales-manager`,
 * `/close`) wrapped by `<Route element={<RequireOpenSession />}>` — the
 * session-state contract in `docs/sales-session-api-contract.md`.
 *
 * It asks the backend `GET /api/sessions/current/` whether the current
 * cashier has an open shift:
 *   - `open: true`  -> render the child `<Outlet />` (the guarded screen).
 *   - `open: false` on first arrival -> show the "start your shift" modal
 *                      that collects the starting cash and POSTs it to
 *                      `/api/sessions/`; only then does the guarded screen
 *                      render. If the cashier dismisses the modal without
 *                      completing the starting-cash set-up, redirect to the
 *                      home/dashboard route `/`. This is what lets a cashier
 *                      who clicks, say, "Sales" while no shift is open get the
 *                      modal instead of being silently bounced to the
 *                      dashboard.
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
const MAX_STARTING_CASH = 1000;

const currency = new Intl.NumberFormat('en-US', {
  style: 'currency',
  currency: 'USD',
});

interface CurrentSessionResponse {
  open: boolean;
  sessionId?: string;
  startedAt?: string;
  startingCash?: string;
}

/** `loading` -> first check in flight; otherwise see the named phase. */
type Phase = 'loading' | 'needs-session' | 'ready';

/**
 * Extract a human-readable `starting_cash` validation message from the DRF
 * error payload. DRF returns per-field errors as an array of `ErrorDetail`
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

  const [cash, setCash] = useState('');
  const [starting, setStarting] = useState(false);
  const [startError, setStartError] = useState('');

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
  // (success), both leave the guard decision to the caller. Dismissal without a
  // completed start returns the cashier to the dashboard.
  const dismiss = useCallback(() => {
    if (starting) return;
    navigate('/', { replace: true });
  }, [navigate, starting]);

  // A document-level Escape listener (not on the non-focusable .mm-modal div)
  // so dismissing works regardless of where focus sits while the start-shift
  // modal is shown. Attached only while the modal is up and withheld while a
  // start POST is in flight, mirroring the disabled close button so dismissal
  // cannot race the request.
  useEffect(() => {
    if (phase !== 'needs-session' || starting) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') dismiss();
    };
    document.addEventListener('keydown', onKeyDown);
    return () => document.removeEventListener('keydown', onKeyDown);
  }, [phase, starting, dismiss]);

  const handleStartSession = async (event: FormEvent<HTMLFormElement>) => {
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
      // Session started: the guarded screen is now reachable.
      setPhase('ready');
      entered.current = true;
    } catch (error) {
      setStartError(startingCashErrorMessage(error));
    } finally {
      setStarting(false);
    }
  };

  if (loading || !me || phase === 'loading') {
    return (
      <div className="mm-loading" role="status">
        Checking for an open session…
      </div>
    );
  }

  if (phase === 'needs-session') {
    return (
      <div
        className="mm-modal-backdrop"
        onMouseDown={(event) => {
          // Dismissing without completing the starting-cash set-up sends the
          // cashier back to the dashboard rather than trapping them on the
          // guarded screen. Ignore backdrop clicks while a start POST is
          // pending, consistent with the disabled close button.
          if (starting) return;
          if (event.target === event.currentTarget) dismiss();
        }}
      >
        <div
          className="mm-modal"
          role="dialog"
          aria-modal="true"
          aria-labelledby="start-shift-title"
        >
          <header className="mm-modal-header">
            <h2 id="start-shift-title" className="mm-modal-title">
              Start your shift
            </h2>
            <button
              type="button"
              className="mm-modal-close"
              aria-label="Close"
              onClick={dismiss}
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
