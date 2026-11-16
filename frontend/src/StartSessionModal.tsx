import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from 'react';
import { apiFetch, ApiError } from './api';

const FOCUSABLE_SELECTOR =
  'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

/** The subset of a created Session a caller needs to confirm the start. */
export interface StartedSession {
  id: number;
  starting_cash: string;
}

interface StartSessionModalProps {
  /** Id of the heading that titles this dialog (kept unique per mount). */
  titleId?: string;
  /**
   * Called once the session is successfully created, with the created
   * session. The caller is responsible for unmounting/clearing this modal
   * (i.e. closing it) on success so the flow can proceed.
   */
  onSuccess: (session: StartedSession) => void;
  /** Called when the user cancels or dismisses the modal without a session. */
  onClose: () => void;
}

/**
 * Reusable modal that prompts for starting cash and creates a new session via
 * POST /api/sessions/ when none is open. Owns its form state, validation,
 * submission, and loading/error states, so any menu guard can mount it and
 * rely on `onSuccess`/`onClose` to learn the outcome. Cancelling or dismissing
 * (Escape, backdrop, close button) never creates a session.
 */
export default function StartSessionModal({
  titleId = 'start-session-title',
  onSuccess,
  onClose,
}: StartSessionModalProps) {
  const [cashInput, setCashInput] = useState('');
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState('');

  const dialogRef = useRef<HTMLDivElement>(null);
  const restoreFocusRef = useRef<HTMLElement | null>(null);

  const cash = Number(cashInput);
  // Valid starting cash: a present numeric value that is zero or positive.
  const cashInvalid = cashInput.trim() !== '' && !Number.isFinite(cash);
  const cashNegative = Number.isFinite(cash) && cash < 0;
  const cashValid =
    cashInput.trim() !== '' && Number.isFinite(cash) && cash >= 0;

  // Remember the element that opened the dialog and restore focus on close.
  useEffect(() => {
    restoreFocusRef.current =
      document.activeElement instanceof HTMLElement ? document.activeElement : null;

    // Move focus into the dialog, onto the currency input, so keyboard and
    // screen-reader users land on the field they should fill first.
    const dialog = dialogRef.current;
    if (dialog) {
      const firstControl =
        dialog.querySelector<HTMLElement>('.mm-modal-body')?.querySelector<HTMLElement>(FOCUSABLE_SELECTOR) ??
        dialog.querySelector<HTMLElement>(FOCUSABLE_SELECTOR);
      firstControl?.focus();
    }

    return () => {
      const restore = restoreFocusRef.current;
      if (restore && restore.isConnected) restore.focus();
    };
  }, []);

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!cashValid || starting) return;
    setStarting(true);
    setError('');
    try {
      const created = await apiFetch<StartedSession>('/api/sessions/', {
        method: 'POST',
        body: JSON.stringify({ starting_cash: cash.toFixed(2) }),
      });
      onSuccess(created);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not start the session.');
      setStarting(false);
    }
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key === 'Escape') {
      event.preventDefault();
      onClose();
      return;
    }
    if (event.key !== 'Tab') return;

    const dialog = dialogRef.current;
    if (!dialog) return;
    const focusables = Array.from(
      dialog.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR),
    );
    if (focusables.length === 0) {
      event.preventDefault();
      return;
    }
    const first = focusables[0];
    const last = focusables[focusables.length - 1];
    const active = document.activeElement;
    if (event.shiftKey) {
      if (active === first || !dialog.contains(active)) {
        event.preventDefault();
        last.focus();
      }
    } else if (active === last || !dialog.contains(active)) {
      event.preventDefault();
      first.focus();
    }
  };

  return (
    <div
      className="mm-modal-backdrop"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget && !starting) onClose();
      }}
    >
      <div
        ref={dialogRef}
        className="mm-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        onKeyDown={handleKeyDown}
      >
        <header className="mm-modal-header">
          <h2 id={titleId} className="mm-modal-title">
            Start session
          </h2>
          <button
            type="button"
            className="mm-modal-close"
            aria-label="Close"
            onClick={onClose}
            disabled={starting}
          >
            &times;
          </button>
        </header>
        <form className="mm-modal-body" onSubmit={handleSubmit}>
          <p className="mm-muted">
            No session is open. Enter the starting cash for the drawer to begin
            a shift.
          </p>
          {error && (
            <p className="mm-alert mm-alert--danger" role="alert">
              {error}
            </p>
          )}
          <label className="mm-field">
            <span className="mm-label">Starting cash</span>
            <input
              className="mm-input"
              type="number"
              min={0}
              step="0.01"
              inputMode="decimal"
              value={cashInput}
              placeholder="0.00"
              aria-required="true"
              aria-invalid={cashInvalid || cashNegative}
              autoComplete="off"
              onChange={(event) => {
                setCashInput(event.target.value);
                setError('');
              }}
            />
            {cashInvalid && (
              <span className="mm-error-text" role="alert">
                Enter a valid amount.
              </span>
            )}
            {!cashInvalid && cashNegative && (
              <span className="mm-error-text" role="alert">
                Starting cash cannot be negative.
              </span>
            )}
          </label>
          <footer className="mm-modal-footer">
            <button
              type="button"
              className="mm-btn mm-btn--ghost"
              onClick={onClose}
              disabled={starting}
            >
              Cancel
            </button>
            <button type="submit" className="mm-btn mm-btn--primary" disabled={!cashValid || starting}>
              {starting ? 'Starting…' : 'Start Session'}
            </button>
          </footer>
        </form>
      </div>
    </div>
  );
}
