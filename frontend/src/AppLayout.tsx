import { Outlet, useNavigate } from 'react-router-dom';
import Sidebar from './Sidebar';
import { useAuth } from './authContext';
import { apiFetch } from './api';
import './AppLayout.css';

export default function AppLayout() {
  const { me, refresh } = useAuth();
  const navigate = useNavigate();

  const handleLogout = async () => {
    try {
      await apiFetch('/api/auth/logout/', { method: 'POST' });
    } catch {
      // Ignore logout transport errors and clear the local session anyway.
    }
    await refresh();
    navigate('/login', { replace: true });
  };

  return (
    <div className="app-shell">
      <Sidebar />
      <div className="app-main">
        <header className="app-header">
          <div className="app-user">
            <span className="app-user-name">{me?.user.username}</span>
            <span className="app-user-role mm-badge mm-badge--primary">
              {me?.user.role}
            </span>
          </div>
          <button
            type="button"
            className="mm-btn mm-btn--ghost"
            onClick={() => void handleLogout()}
          >
            Logout
          </button>
        </header>
        <main className="app-page">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
