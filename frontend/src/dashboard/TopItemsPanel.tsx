import { useCallback, useEffect, useState } from 'react';
import {
  fetchTopItems,
  formatMoney,
  TOP_SCOPE_HINTS,
  TOP_SCOPE_LABELS,
  TOP_SCOPES,
  type TopScope,
  type TopItemsPayload,
} from './data';

interface TopItemsPanelProps {
  /** Opt-in test seam: seed data and skip the network effect. */
  initialData?: TopItemsPayload;
  /** Opt-in test seam: resolve initial scope. */
  initialScope?: TopScope;
}

/**
 * Top 5 items sold — a table with Rank / Product / Units / Revenue / Avg
 * unit price for the Today, Week, or Month bucket. Unit-based ranking with a
 * deterministic name tie-break comes from the server (contract §4.3); the
 * client only refreshes on scope change and formats money for display.
 */
export default function TopItemsPanel({
  initialData,
  initialScope = 'today',
}: TopItemsPanelProps) {
  const [scope, setScope] = useState<TopScope>(initialScope);
  const [data, setData] = useState<TopItemsPayload | null>(initialData ?? null);
  const [loading, setLoading] = useState(!initialData);
  const [error, setError] = useState('');

  const load = useCallback(
    async (target: TopScope) => {
      setLoading(true);
      setError('');
      try {
        const payload = await fetchTopItems(target);
        setData(payload);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to load top items');
        setData(null);
      } finally {
        setLoading(false);
      }
    },
    [],
  );

  useEffect(() => {
    // Seeded mode renders the given data for the initial scope without
    // touching the network; switching scope still fetches.
    if (!initialData) void load(scope);
  }, [scope, initialData, load]);

  const switchScope = (next: TopScope) => {
    setScope(next);
    // A user switching scope always fetches — even in seeded (test) mode,
    // where only the initial render is supplied without a network call.
    void load(next);
  };

  const headingLabel = `Top items — ${TOP_SCOPE_LABELS[scope]}`;

  let content: React.ReactNode;
  if (loading) {
    content = (
      <div className="mm-loading dashboard-panel-loading" role="status">
        <span className="mm-spinner" aria-hidden="true" />
        Loading top items…
      </div>
    );
  } else if (error) {
    content = (
      <div className="mm-alert mm-alert--danger" role="alert">
        <span>Failed to load top items: {error}</span>{' '}
        <button
          type="button"
          className="dashboard-panel-retry"
          onClick={() => void load(scope)}
        >
          Retry
        </button>
      </div>
    );
  } else if (!data || data.top_items.length === 0) {
    content = (
      <div className="mm-empty dashboard-panel-empty">
        <span className="mm-empty-title">No items sold in this period</span>
        <span className="mm-empty-text">
          Completed sales created in the {TOP_SCOPE_LABELS[scope].toLowerCase()}{' '}
          period will appear here as best sellers.
        </span>
      </div>
    );
  } else {
    const totalItems = data.total_items;
    const moreCount = Math.max(0, totalItems - data.top_items.length);
    content = (
      <>
        <div className="mm-table-wrap dashboard-table-wrap">
          <table className="mm-table">
            <caption className="mm-visually-hidden">
              Top {data.top_items.length} items by units sold — {headingLabel}
            </caption>
            <thead>
              <tr>
                <th scope="col">Rank</th>
                <th scope="col">Product</th>
                <th scope="col" className="num">Units</th>
                <th scope="col" className="num">Revenue</th>
                <th scope="col" className="num">Avg price</th>
              </tr>
            </thead>
            <tbody>
              {data.top_items.map((item) => (
                <tr key={item.rank}>
                  <td className="num">{item.rank}</td>
                  <td>{item.product_name}</td>
                  <td className="num">{item.units_sold}</td>
                  <td className="num">{formatMoney(item.revenue)}</td>
                  <td className="num">{formatMoney(item.avg_unit_price)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {moreCount > 0 && (
          <p className="dashboard-panel-note">
            Showing top {data.top_items.length} of {totalItems} items sold —{' '}
            {moreCount} more not shown.
          </p>
        )}
      </>
    );
  }

  return (
    <section
      className="mm-card dashboard-panel dashboard-panel--top-items"
      aria-labelledby="top-items-title"
    >
      <header className="mm-card-header">
        <div>
          <h2 className="mm-card-title" id="top-items-title">
            Top Items
          </h2>
          <p className="mm-card-subtitle">{TOP_SCOPE_HINTS[scope]}</p>
        </div>
        <div
          className="dashboard-tabs"
          role="tablist"
          aria-label="Top items period"
        >
          {TOP_SCOPES.map((value) => {
            const selected = value === scope;
            return (
              <button
                key={value}
                type="button"
                role="tab"
                aria-selected={selected}
                aria-controls="top-items-panel"
                className={`dashboard-tab${selected ? ' is-active' : ''}`}
                onClick={() => switchScope(value)}
              >
                {TOP_SCOPE_LABELS[value]}
              </button>
            );
          })}
        </div>
      </header>
      <div id="top-items-panel" role="tabpanel">
        {content}
      </div>
    </section>
  );
}
