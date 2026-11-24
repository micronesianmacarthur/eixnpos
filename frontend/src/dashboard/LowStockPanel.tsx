import { useCallback, useEffect, useState } from 'react';
import { fetchLowStock, type LowStockPayload } from './data';

interface LowStockPanelProps {
  /** Opt-in test seam: seed data and skip the network effect. */
  initialData?: LowStockPayload;
}

/** Status label + badge tone for a low-stock item (derived, owner-facing). */
interface StockStatus {
  label: string;
  badge: string;
  /** aria-sort hint or testid suffix; not displayed as a separate label. */
  key: 'out' | 'critically-low' | 'low';
}

function stockStatus(quantity: number, minStock: number): StockStatus {
  if (quantity <= 0) {
    return {
      label: 'Out of stock',
      badge: 'mm-badge mm-badge--danger',
      key: 'out',
    };
  }
  // At or below the threshold but still on hand.
  if (minStock > 0 && quantity <= minStock * 0.5) {
    return {
      label: 'Critically low',
      badge: 'mm-badge mm-badge--warning',
      key: 'critically-low',
    };
  }
  return {
    label: 'Low',
    badge: 'mm-badge mm-badge--warning',
    key: 'low',
  };
}

/**
 * Low-stock alerts — active stock-only products at or below their own
 * reorder threshold, sorted ascending by on-hand quantity (server does the
 * sort; contract §5.6). Shows product, remaining quantity, threshold, and a
 * derived severity badge so a manager can see what needs restocking.
 */
export default function LowStockPanel({ initialData }: LowStockPanelProps) {
  const [data, setData] = useState<LowStockPayload | null>(initialData ?? null);
  const [loading, setLoading] = useState(!initialData);
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      setData(await fetchLowStock());
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load low-stock items');
      setData(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!initialData) void load();
  }, [initialData, load]);

  const items = data?.items ?? [];
  const count = data?.low_stock_count ?? items.length;

  let content: React.ReactNode;
  if (loading) {
    content = (
      <div className="mm-loading dashboard-panel-loading" role="status">
        <span className="mm-spinner" aria-hidden="true" />
        Loading stock levels…
      </div>
    );
  } else if (error) {
    content = (
      <div className="mm-alert mm-alert--danger" role="alert">
        <span>Failed to load low-stock items: {error}</span>{' '}
        <button type="button" className="dashboard-panel-retry" onClick={() => void load()}>
          Retry
        </button>
      </div>
    );
  } else if (items.length === 0) {
    content = (
      <div className="mm-empty dashboard-panel-empty">
        <span className="mm-empty-title">No items below threshold</span>
        <span className="mm-empty-text">
          Everything in stock is above its reorder point right now.
        </span>
      </div>
    );
  } else {
    content = (
      <div className="mm-table-wrap dashboard-table-wrap">
        <table className="mm-table low-stock-table">
          <caption className="mm-visually-hidden">
            {count} item{count === 1 ? '' : 's'} at or below their reorder
            threshold
          </caption>
          <thead>
            <tr>
              <th scope="col">Item</th>
              <th scope="col" className="num">On hand</th>
              <th scope="col" className="num">Threshold</th>
              <th scope="col">Status</th>
            </tr>
          </thead>
          <tbody>
            {items.map((item) => {
              const status = stockStatus(item.quantity_on_hand, item.min_stock);
              return (
                <tr key={item.product_id}>
                  <td>
                    <span className="low-stock-name">{item.product_name}</span>
                    {(item.sku || item.category) && (
                      <span className="mm-muted low-stock-meta">
                        {[item.sku, item.category].filter(Boolean).join(' · ')}
                      </span>
                    )}
                  </td>
                  <td className="num">{item.quantity_on_hand}</td>
                  <td className="num">{item.min_stock}</td>
                  <td>
                    <span className={status.badge} data-low-stock-status={status.key}>
                      {status.label}
                    </span>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    );
  }

  return (
    <section
      className="mm-card dashboard-panel dashboard-panel--low-stock"
      aria-labelledby="low-stock-title"
    >
      <header className="mm-card-header">
        <div>
          <h2 className="mm-card-title" id="low-stock-title">
            Low Stock
          </h2>
          <p className="mm-card-subtitle">
            {count} item{count === 1 ? '' : 's'} at or below restock threshold
          </p>
        </div>
        <button type="button" className="mm-btn mm-btn--outline mm-btn--sm" onClick={() => void load()}>
          Refresh
        </button>
      </header>
      {content}
    </section>
  );
}
