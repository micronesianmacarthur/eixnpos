import { Outlet } from 'react-router-dom';
import Sidebar from './Sidebar';
import ThemeToggle from './ThemeToggle';
import { useAuth } from './authContext';
import './AppLayout.css';

export default function AppLayout() {
  const { me } = useAuth();

  return (
    <div className="app-shell">
      <Sidebar />
      <div className="app-main">
        <header className="app-header">
          <div className="app-user">
            <span className="app-user-name">{me?.user.username}</span>
            <span className="app-user-role">{me?.user.role}</span>
          </div>
          <ThemeToggle />
        </header>
        <main className="app-page">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
