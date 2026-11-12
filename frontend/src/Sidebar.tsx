import { useEffect, useRef, useState } from 'react';
import { NavLink } from 'react-router-dom';
import { useAuth, type Role } from './authContext';
import { Icon, type IconName } from './Icon';
import './Sidebar.css';

interface MenuItem {
  readonly to: string;
  readonly label: string;
  readonly icon: IconName;
  readonly end?: boolean;
  readonly managerOnly?: boolean;
}

const MENU: readonly MenuItem[] = [
  { to: '/', label: 'Dashboard', icon: 'dashboard', end: true },
  { to: '/sales', label: 'Sales', icon: 'sales' },
  { to: '/sales-manager', label: 'Sales Manager', icon: 'sales-manager' },
  { to: '/customers', label: 'Customers', icon: 'customers' },
  { to: '/suppliers', label: 'Suppliers', icon: 'suppliers' },
  { to: '/inventory', label: 'Inventory', icon: 'inventory' },
  { to: '/users', label: 'Users', icon: 'users', managerOnly: true },
  { to: '/settings', label: 'Settings', icon: 'settings', managerOnly: true },
  { to: '/close', label: 'Close', icon: 'close' },
];

const TABLET_QUERY = '(min-width: 769px) and (max-width: 1023px)';

function isTablet(): boolean {
  return (
    typeof window !== 'undefined' &&
    window.matchMedia(TABLET_QUERY).matches
  );
}

function canManage(role: Role): boolean {
  return role === 'manager' || role === 'admin';
}

function Sidebar() {
  const { me } = useAuth();
  const [collapsed, setCollapsed] = useState(isTablet);
  const [mobileOpen, setMobileOpen] = useState(false);
  const manuallySetCollapsed = useRef(false);

  useEffect(() => {
    const query = window.matchMedia(TABLET_QUERY);
    const onBreakpointChange = (event: MediaQueryListEvent) => {
      // Tablet defaults to the collapsed mini sidebar. Once the user has made
      // an explicit choice, keep it across breakpoint changes.
      if (!manuallySetCollapsed.current) {
        setCollapsed(event.matches);
      }
    };

    query.addEventListener('change', onBreakpointChange);
    return () => query.removeEventListener('change', onBreakpointChange);
  }, []);

  const toggleCollapsed = () => {
    manuallySetCollapsed.current = true;
    setCollapsed((value) => !value);
  };

  const role = me?.user.role ?? 'cashier';
  const items = MENU.filter((item) => !item.managerOnly || canManage(role));

  return (
    <>
      <button
        type="button"
        className="sidebar-mobile-toggle"
        aria-label={mobileOpen ? 'Close menu' : 'Open menu'}
        aria-expanded={mobileOpen}
        onClick={() => setMobileOpen((open) => !open)}
      >
        <Icon name={mobileOpen ? 'x' : 'menu'} size={20} />
      </button>

      {mobileOpen && (
        <div
          className="sidebar-backdrop"
          aria-hidden="true"
          onClick={() => setMobileOpen(false)}
        />
      )}

      <nav
        className={`sidebar${collapsed ? ' collapsed' : ''}${mobileOpen ? ' mobile-open' : ''}`}
        aria-label="Main navigation"
      >
        <div className="sidebar-header">
          <span className="sidebar-brand" aria-label="eixnPOS">
            {collapsed ? (
              <span aria-hidden="true">e</span>
            ) : (
              <>
                <span className="sidebar-brand-accent">eixn</span>POS
              </>
            )}
          </span>
          <button
            type="button"
            className="sidebar-collapse"
            aria-label={collapsed ? 'Expand menu' : 'Collapse menu'}
            aria-expanded={!collapsed}
            onClick={toggleCollapsed}
          >
            <span aria-hidden="true">{collapsed ? '»' : '«'}</span>
          </button>
        </div>

        <ul className="sidebar-menu">
          {items.map((item) => (
            <li key={item.to}>
              <NavLink
                to={item.to}
                end={item.end}
                title={collapsed ? item.label : undefined}
                className={({ isActive }) =>
                  `sidebar-link${isActive ? ' active' : ''}`
                }
                onClick={() => setMobileOpen(false)}
              >
                <Icon name={item.icon} />
                <span className="sidebar-label">{item.label}</span>
              </NavLink>
            </li>
          ))}
        </ul>
      </nav>
    </>
  );
}

export default Sidebar;
