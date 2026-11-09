import {
  useCallback,
  useEffect,
  useState,
  type ReactNode,
} from 'react';
import { Navigate, Outlet } from 'react-router-dom';
import { apiFetch, ApiError } from './api';
import { AuthContext, useAuth, type MeResponse, type Role } from './authContext';

export function AuthProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<MeResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const refresh = useCallback(() => {
    apiFetch<MeResponse>('/api/auth/me/')
      .then((data) => {
        setMe(data);
        setError('');
      })
      .catch((err) => {
        setMe(null);
        if (err instanceof ApiError && (err.status === 401 || err.status === 403)) {
          setError('');
          return;
        }
        setError(err instanceof ApiError ? err.message : 'Failed to load session');
      })
      .finally(() => {
        setLoading(false);
      });
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  return (
    <AuthContext.Provider value={{ me, loading, error, refresh }}>
      {children}
    </AuthContext.Provider>
  );
}

export function RequireAuth() {
  const { me, loading, error, refresh } = useAuth();

  if (loading) {
    return <p style={{ padding: '2rem' }}>Loading...</p>;
  }

  if (error) {
    return (
      <div style={{ padding: '2rem' }}>
        <p style={{ color: 'red' }}>{error}</p>
        <button onClick={refresh}>Retry</button>
      </div>
    );
  }

  if (!me) {
    return <Navigate to="/login" replace />;
  }

  return <Outlet />;
}

export function RequireRole({ roles }: { roles: Role[] }) {
  const { me } = useAuth();

  if (!me) {
    return <Navigate to="/login" replace />;
  }

  if (!roles.includes(me.user.role)) {
    return <Navigate to="/" replace />;
  }

  return <Outlet />;
}
