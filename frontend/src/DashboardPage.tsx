import BusinessInfoForm from './BusinessInfoForm';
import { useAuth } from './authContext';

const CARDS = [
  { title: 'Sale', body: 'Start a new sale from the sale screen.' },
  { title: 'Inventory', body: 'Review stock levels and receive new stock.' },
  { title: 'Reports', body: 'Close a shift and review the Z-report.' },
];

export default function DashboardPage() {
  const { me, refresh } = useAuth();

  return (
    <section>
      <h1>Dashboard</h1>

      {!me?.business_info_complete && (
        <section style={{ marginBottom: '2rem' }}>
          <p>Complete your business information so it appears on receipts.</p>
          <BusinessInfoForm onSaved={refresh} />
        </section>
      )}

      <div className="dashboard-grid">
        {CARDS.map((card) => (
          <div key={card.title} className="dashboard-card">
            <h2>{card.title}</h2>
            <p>{card.body}</p>
          </div>
        ))}
      </div>
    </section>
  );
}
