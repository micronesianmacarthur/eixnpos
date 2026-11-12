import { useState, type FormEvent } from 'react';
import { useNavigate } from 'react-router-dom';
import { apiFetch, ApiError } from './api';
import './AuthPages.css';

function LoginPage() {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const navigate = useNavigate();

  const handleSubmit = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    setLoading(true);
    setError('');
    try {
      await apiFetch('/api/auth/login/', {
        method: 'POST',
        body: JSON.stringify({ username, password }),
      });
      navigate('/', { replace: true });
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Network error');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="auth-page">
      <main className="mm-card auth-card">
        <div className="auth-brand">
          <span className="auth-brand-mark" aria-hidden="true">
            e
          </span>
          <span>
            <span className="auth-brand-accent">eixn</span>POS
          </span>
        </div>

        <header className="auth-header">
          <h1 className="auth-title">Welcome back</h1>
          <p className="auth-subtitle">Sign in to open your point of sale.</p>
        </header>

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
