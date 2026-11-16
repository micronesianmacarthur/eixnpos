import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from 'react';
import { apiFetch, ApiError } from './api';

const FOCUSABLE_SELECTOR =
  'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

/**
 * Validate starting cash in the browser. Returns an error message, or null
 * when the value is acceptable.
 *
 * This is the single client-side validation source for the starting-cash
 * flow. Numeric bounds wider than negative/empty are enforced by the backend
 * (`SessionSerializer.starting_cash` is a DecimalField with up to 12 digits
 * and 2 decimal places, minimum 0.00), which is left as the authority for the
 * upper boundary.
 */
function startingCashError(value: string): string | null {
  const trimmed = value.trim();
  if (trimmed === '') return 'Enter a starting cash amount.';
  const parsed = Number(trimmed);
  if (!Number.isFinite(parsed)) return 'Starting cash must be a number.';
  if (parsed < 0) return 'Starting cash cannot be negative.';
  return null;
}

/**
 * Extract a human-readable `starting_cash` validation message from a DRF
 * error payload. DRF returns per-field errors as an array of `ErrorDetail`
 * (either `[{ "message": ..., "code": ... }]` or plain strings), which the
 * generic `apiFetch`-produced message does not surface fully.
 */
function startingCashApiMessage(error: unknown): string {
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
 *
 * Accessibility: focus is moved into the dialog on open and restored on close,
 * Tab is trapped inside the dialog, and Escape dismisses from a document-level
 * listener so it works regardless of where focus sits.
 */
export default function StartSessionModal({
  titleId = 'start-shift-title',
  onSuccess,
  onClose,
}: StartSessionModalProps) {
  const [cashInput, setCashInput] = useState('');
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState('');

  const dialogRef = useRef<HTMLDivElement>(null);
  const restoreFocusRef = useRef<HTMLElement | null>(null);

  const cash = Number(cashInput);

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

  // A document-level Escape listener (not on the dialog element itself) so
  // dismissing works regardless of where focus sits while the modal is shown.
  // Attached only while the modal is up and withheld while a start POST is in
  // flight, mirroring the disabled close button so dismissal cannot race it.
  useEffect(() => {
    if (starting) return;
    const onKeyDown = (event: globalThis.KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', onKeyDown);
    return () => document.removeEventListener('keydown', onKeyDown);
  }, [starting, onClose]);

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (starting) return;
    const validation = startingCashError(cashInput);
    if (validation !== null) {
      setError(validation);
      return;
    }
    setStarting(true);
    setError('');
    try {
      const created = await apiFetch<StartedSession>('/api/sessions/', {
        method: 'POST',
        body: JSON.stringify({ starting_cash: cash.toFixed(2) }),
      });
      onSuccess(created);
    } catch (err) {
      setError(startingCashApiMessage(err));
      setStarting(false);
    }
  };

  // Trap Tab within the dialog so keyboard users can't tab into the page
  // behind the modal.
  const handleKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
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
        // Dismissing without completing the starting-cash set-up is decided by
        // the caller (usually sending the cashier back to the dashboard).
        // Ignore backdrop clicks while a start POST is pending, consistent
        // with the disabled close button.
        if (starting) return;
        if (event.target === event.currentTarget) onClose();
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
            Start your shift
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
        <form
          className="mm-modal-body"
          onSubmit={handleSubmit}
          noValidate
        >
          <p className="mm-muted">
            There is no open session. Enter the cash you are placing in the
            drawer to begin the shift.
          </p>
          {error && (
            <p className="mm-alert mm-alert--danger" role="alert">
              {error}
            </p>
          )}
          <label className="mm-field">
            <span>Starting cash</span>
            <input
              className="mm-input"
              type="number"
              min={0}
              step="0.01"
              inputMode="decimal"
              value={cashInput}
              onChange={(event) => {
                setCashInput(event.target.value);
                setError('');
              }}
              placeholder="0.00"
              aria-label="Starting cash"
              aria-required="true"
              aria-invalid={error !== ''}
              autoComplete="off"
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
