import BusinessInfoForm from './BusinessInfoForm';
import { useAuth } from './authContext';
import { Icon, type IconName } from './Icon';
import './DashboardPage.css';

interface DashboardCard {
  readonly title: string;
  readonly body: string;
  readonly icon: IconName;
  readonly tone: 'primary' | 'secondary';
}

const CARDS: readonly DashboardCard[] = [
  {
    title: 'Sale',
    body: 'Start a new sale from the sale screen.',
    icon: 'sales',
    tone: 'primary',
  },
  {
    title: 'Inventory',
    body: 'Review stock levels and receive new stock.',
    icon: 'inventory',
    tone: 'secondary',
  },
  {
    title: 'Reports',
    body: 'Close a shift and review the Z-report.',
    icon: 'sales-manager',
    tone: 'primary',
  },
];

export default function DashboardPage() {
  const { me, refresh } = useAuth();

  return (
    <div className="mm-page">
      <header className="mm-page-header">
        <h1 className="mm-page-title">Dashboard</h1>
      </header>

      {!me?.business_info_complete && (
        <section className="dashboard-setup">
          <p>Complete your business information so it appears on receipts.</p>
          <BusinessInfoForm onSaved={refresh} />
        </section>
      )}

      <div className="dashboard-grid">
        {CARDS.map((card) => (
          <article key={card.title} className="mm-card dashboard-card">
            <span
              className={`mm-icon-tile${card.tone === 'secondary' ? ' mm-icon-tile--secondary' : ''}`}
            >
              <Icon name={card.icon} size={22} />
            </span>
            <div>
              <h2 className="mm-card-title">{card.title}</h2>
              <p className="dashboard-card-text">{card.body}</p>
            </div>
          </article>
        ))}
      </div>
    </div>
  );
}
