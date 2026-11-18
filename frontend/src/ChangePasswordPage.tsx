import { useState, type FormEvent } from 'react';
import { useNavigate } from 'react-router-dom';
import { Logo } from './Logo';
import { apiFetch, ApiError } from './api';
import { useAuth } from './authContext';
import './AuthPages.css';

function ChangePasswordPage() {
  const { me, refresh } = useAuth();
  const navigate = useNavigate();
  const [currentPassword, setCurrentPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  const username = me?.user?.username ?? '';

  const handleSubmit = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    if (loading) {
      return;
    }

    setError('');

    if (newPassword !== confirmPassword) {
      setError('New password and confirmation do not match.');
      return;
    }

    setLoading(true);
    try {
      await apiFetch('/api/auth/change-password/', {
        method: 'POST',
        body: JSON.stringify({
          current_password: currentPassword,
          new_password: newPassword,
        }),
      });
      // The server invalidates the current session on success, so refresh so the
      // client observes the logged-out state, then send the user back to log in.
      await refresh();
      navigate('/login', {
        replace: true,
        state: { notice: 'Password updated. Please log in with your new password.' },
      });
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Network error');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="auth-page">
      <main className="mm-card auth-card">
        <div className="auth-brand" aria-label="eixnPOS">
          <Logo size={24} />
          <span className="auth-brand-accent">eixn</span>POS
        </div>

        <header className="auth-header">
          <span className="auth-eyebrow">Security</span>
          <h1 className="auth-title">Change your password</h1>
          <p className="auth-subtitle">
            {username ? `Signed in as ${username}. ` : ''}You must set a new password
            before continuing.
          </p>
        </header>

        {error && (
          <div className="mm-alert mm-alert--danger auth-alert" role="alert">
            {error}
          </div>
        )}

        <form className="auth-form" onSubmit={handleSubmit}>
          <div className="mm-field">
            <label className="mm-label" htmlFor="change-current-password">
              Current password:
            </label>
            <input
              id="change-current-password"
              className="mm-input"
              type="password"
              value={currentPassword}
              onChange={(e) => setCurrentPassword(e.target.value)}
              required
              disabled={loading}
              autoComplete="current-password"
            />
          </div>

          <div className="mm-field">
            <label className="mm-label" htmlFor="change-new-password">
              New password:
            </label>
            <input
              id="change-new-password"
              className="mm-input"
              type="password"
              value={newPassword}
              onChange={(e) => setNewPassword(e.target.value)}
              required
              disabled={loading}
              autoComplete="new-password"
            />
          </div>

          <div className="mm-field">
            <label className="mm-label" htmlFor="change-confirm-password">
              Confirm new password:
            </label>
            <input
              id="change-confirm-password"
              className="mm-input"
              type="password"
              value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)}
              required
              disabled={loading}
              autoComplete="new-password"
            />
          </div>

          <button
            type="submit"
            className="mm-btn mm-btn--primary auth-submit"
            disabled={loading}
          >
            {loading ? 'Updating password...' : 'Update password'}
          </button>
        </form>
      </main>
    </div>
  );
}

export default ChangePasswordPage;
