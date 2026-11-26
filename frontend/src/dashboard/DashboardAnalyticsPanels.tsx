import TopItemsPanel from './TopItemsPanel';
import PerformancePanel from './PerformancePanel';
import LowStockPanel from './LowStockPanel';
import './DashboardPanels.css';

/**
 * Dashboard analytics panels: top items sold (Today/Week/Month), a monthly
 * performance comparison chart, and low-stock alerts. Each panel owns its own
 * loading / error / empty states and fetches from /api/dashboard/*.
 *
 * These are dashboard content open to any authenticated user (the
 * /api/dashboard/* read endpoints require only authentication), so cashiers,
 * managers, and admins all see them.
 *
 * This is a pure composition so the page can mount the three independently;
 * the sibling work for the headline KPI cards + session summary lives on the
 * same dashboard page and is wired by the integration step.
 */
export default function DashboardAnalyticsPanels() {
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
