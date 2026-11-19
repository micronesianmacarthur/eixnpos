import { useState, type FormEvent } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { Logo } from './Logo';
import { apiFetch, ApiError } from './api';
import type { LoginResponse } from './authContext';
import './AuthPages.css';

function LoginPage() {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const navigate = useNavigate();
  const location = useLocation();
  const notice = (location.state as { notice?: string } | null)?.notice;

  const handleSubmit = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    setLoading(true);
    setError('');
    try {
      // Trim the username before submitting: the backend authenticates the
      // exact string, so a stray leading/trailing space (paste, autofill,
      // accidental trailing space) would otherwise reject a *valid* account
      // with a misleading "invalid credentials". Passwords are sent verbatim.
      const data = await apiFetch<LoginResponse>('/api/auth/login/', {
        method: 'POST',
        body: JSON.stringify({ username: username.trim(), password }),
      });
      const changeRequired =
        data.password_change_required ?? data.must_change_password ?? false;
      navigate(changeRequired ? '/change-password' : '/', { replace: true });
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
          <h1 className="auth-title">Welcome back</h1>
          <p className="auth-subtitle">Sign in to open your point of sale.</p>
        </header>

        {notice && (
          <div className="mm-alert mm-alert--info auth-alert" role="status">
            {notice}
          </div>
        )}

        {error && (
          <div className="mm-alert mm-alert--danger auth-alert" role="alert">
            {error}
          </div>
        )}

        <form className="auth-form" onSubmit={handleSubmit}>
          <div className="mm-field">
            <label className="mm-label" htmlFor="login-username">
              Username:
            </label>
            <input
              id="login-username"
              className="mm-input"
              type="text"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              required
              disabled={loading}
              autoComplete="username"
            />
          </div>

          <div className="mm-field">
            <label className="mm-label" htmlFor="login-password">
              Password:
            </label>
            <input
              id="login-password"
              className="mm-input"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              disabled={loading}
              autoComplete="current-password"
            />
          </div>

          <button
            type="submit"
            className="mm-btn mm-btn--primary auth-submit"
            disabled={loading}
          >
            {loading ? 'Logging in...' : 'Login'}
          </button>
        </form>

        <p className="auth-note">Secure local access to your store.</p>
      </main>
    </div>
  );
}

export default LoginPage;
