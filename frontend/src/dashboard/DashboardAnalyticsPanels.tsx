import TopItemsPanel from './TopItemsPanel';
import PerformancePanel from './PerformancePanel';
import LowStockPanel from './LowStockPanel';
import { useAuth } from '../authContext';
import './DashboardPanels.css';

/**
 * Dashboard analytics panels: top items sold (Today/Week/Month), a monthly
 * performance comparison chart, and low-stock alerts. Each panel owns its own
 * loading / error / empty states and fetches from /api/dashboard/*.
 *
 * These are manager-only surfaces (the /api/dashboard/* endpoints require
 * manager/admin authority, contract §7), so non-managers see nothing here
 * rather than firing 4xx requests for data they cannot read.
 *
 * This is a pure composition so the page can mount the three independently;
 * the sibling work for the headline KPI cards + session summary lives on the
 * same dashboard page and is wired by the integration step.
 */
export default function DashboardAnalyticsPanels() {
  const { me } = useAuth();
  const role = me?.user.role;
  const canManage = role === 'manager' || role === 'admin';

  if (!canManage) return null;

  return (
    <section
      className="dashboard-panels"
      aria-label="Dashboard analytics"
    >
      <TopItemsPanel />
      <PerformancePanel />
      <LowStockPanel />
    </section>
  );
}
