import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from 'react';
import {
  EMPTY_USER_FORM,
  USER_ROLES,
  buildUserSaveRequest,
  userFormFromUser,
  type User,
  type UserForm,
} from './users';

const FOCUSABLE_SELECTOR =
  'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

interface UserFormModalProps {
  mode: 'create' | 'edit';
  /** The user being edited; null for create. Used to prefill and to diff the PATCH. */
  user: User | null;
  saving: boolean;
  error: string;
  /** Id of the heading that titles this dialog (kept unique per mount). */
  titleId: string;
  onSubmit: (payload: Record<string, string>) => void;
  onClose: () => void;
}

/**
 * Shared create/edit user modal used by both the Settings page and the Users
 * page. Owns the form state, validation and payload building, and manages modal
 * dialog focus: initial focus into the dialog, Tab containment, and restoring
 * focus to the trigger element on close.
 */
export default function UserFormModal({
  mode,
  user,
  saving,
  error,
  titleId,
  onSubmit,
  onClose,
}: UserFormModalProps) {
  const [form, setForm] = useState<UserForm>(() =>
    mode === 'edit' && user ? userFormFromUser(user) : { ...EMPTY_USER_FORM },
  );
  const [localError, setLocalError] = useState('');

  const dialogRef = useRef<HTMLDivElement>(null);
  const restoreFocusRef = useRef<HTMLElement | null>(null);

  const updateField = (field: keyof UserForm, value: string) => {
    setForm((current) => ({ ...current, [field]: value }));
    setLocalError('');
  };

  // Remember the element that opened the dialog and restore focus on close.
  useEffect(() => {
    restoreFocusRef.current =
      document.activeElement instanceof HTMLElement ? document.activeElement : null;

    // Move focus into the dialog, preferring the first enabled form control so
    // keyboard and screen-reader users land on the field they should edit. The
    // username field is disabled in edit mode, so that falls through to the
    // next control instead of leaving focus on the backdrop.
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

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const result = buildUserSaveRequest(mode, form, user);
    if (result.error) {
      setLocalError(result.error);
      return;
    }
    setLocalError('');
    onSubmit(result.payload);
  };

  const showError = localError || error;

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
          <h2 id={titleId}>
            {mode === 'create' ? 'New user' : 'Edit user'}
          </h2>
          <button
            type="button"
            className="mm-modal-close"
            aria-label="Close"
            onClick={onClose}
            disabled={saving}
          >
            &times;
          </button>
        </header>
        <form className="mm-modal-body" onSubmit={handleSubmit}>
          {showError && (
            <p className="mm-alert mm-alert--danger" role="alert">
              {showError}
            </p>
          )}
          <label className="mm-field">
            <span className="mm-label">Username</span>
            <input
              className="mm-input"
              type="text"
              value={form.username}
              onChange={(event) => updateField('username', event.target.value)}
              required
              maxLength={150}
              disabled={mode === 'edit'}
            />
            {mode === 'edit' && (
              <span className="mm-hint">Username cannot be changed</span>
            )}
          </label>
          <label className="mm-field">
            <span className="mm-label">Email</span>
            <input
              className="mm-input"
              type="email"
              value={form.email}
              onChange={(event) => updateField('email', event.target.value)}
              maxLength={254}
            />
          </label>
          <div className="mm-field-row">
            <label className="mm-field">
              <span className="mm-label">First name</span>
              <input
                className="mm-input"
                type="text"
                value={form.first_name}
                onChange={(event) => updateField('first_name', event.target.value)}
                required={mode === 'create'}
                maxLength={150}
              />
            </label>
            <label className="mm-field">
              <span className="mm-label">Last name</span>
              <input
                className="mm-input"
                type="text"
                value={form.last_name}
                onChange={(event) => updateField('last_name', event.target.value)}
                maxLength={150}
                placeholder="Optional"
              />
            </label>
          </div>
          <label className="mm-field">
            <span className="mm-label">Role</span>
            <select
              className="mm-select"
              value={form.role}
              onChange={(event) => updateField('role', event.target.value)}
              required
            >
              {USER_ROLES.map((role) => (
                <option key={role} value={role}>
                  {role.charAt(0).toUpperCase() + role.slice(1)}
                </option>
              ))}
            </select>
          </label>
          <label className="mm-field">
            <span className="mm-label">Password</span>
            <input
              className="mm-input"
              type="password"
              value={form.password}
              onChange={(event) => updateField('password', event.target.value)}
              minLength={8}
              maxLength={128}
              placeholder={
                mode === 'edit'
                  ? 'Leave blank to keep current password'
                  : 'At least 8 characters'
              }
              required={mode === 'create'}
            />
          </label>
          <footer className="mm-modal-footer">
            <button
              type="button"
              className="mm-btn mm-btn--ghost"
              onClick={onClose}
              disabled={saving}
            >
              Cancel
            </button>
            <button type="submit" className="mm-btn mm-btn--primary" disabled={saving}>
              {saving ? 'Saving...' : mode === 'create' ? 'Create user' : 'Save changes'}
            </button>
          </footer>
        </form>
      </div>
    </div>
  );
}
