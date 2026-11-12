import {
  useCallback,
  useEffect,
  useMemo,
  useState,
  type FormEvent,
} from 'react';
import { useNavigate } from 'react-router-dom';
import { apiFetch, ApiError } from './api';
import { useAuth } from './authContext';
import { Icon } from './Icon';
import './InventoryPage.css';

interface Product {
  id: number;
  name: string;
  sku: string | null;
  description: string;
  price: string;
  cost: string;
  min_stock: number;
  supplier: number | null;
  supplier_name: string | null;
  active: boolean;
  created_at: string;
  updated_at: string;
  quantity: number;
  is_low_stock: boolean;
}

interface ReceiveForm {
  quantity: string;
  note: string;
}

const EMPTY_RECEIVE: ReceiveForm = { quantity: '1', note: '' };

function stockStatus(product: Product): { label: string; badge: string } {
  if (product.quantity === 0) {
    return { label: 'Out', badge: 'mm-badge mm-badge--danger' };
  }
  if (product.is_low_stock) {
    return { label: 'Low', badge: 'mm-badge mm-badge--warning' };
  }
  return { label: 'OK', badge: 'mm-badge mm-badge--success' };
}

function normalizeList<T>(data: T[] | { results: T[] }): T[] {
  return Array.isArray(data) ? data : data.results;
}

export default function InventoryPage() {
  const navigate = useNavigate();
  const { me } = useAuth();
  const isManagerOrAdmin = me ? (me.user.role === 'manager' || me.user.role === 'admin') : false;

  const [products, setProducts] = useState<Product[]>([]);
  const [search, setSearch] = useState('');
  const [categoryFilter, setCategoryFilter] = useState('All');
  const [statusFilter, setStatusFilter] = useState('All');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const [receiveOpen, setReceiveOpen] = useState(false);
  const [receiveProduct, setReceiveProduct] = useState<Product | null>(null);
  const [receiveForm, setReceiveForm] = useState<ReceiveForm>(EMPTY_RECEIVE);
  const [receiveError, setReceiveError] = useState('');
  const [receiveSaving, setReceiveSaving] = useState(false);

  const loadProducts = useCallback(async () => {
    try {
      const data = await apiFetch<Product[] | { results: Product[] }>('/api/products/');
      const list = normalizeList(data);
      setProducts(list.filter((p) => p.active !== false));
      setError('');
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to load products');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadProducts();
  }, [loadProducts]);

  useEffect(() => {
    if (receiveOpen || receiveProduct) {
      const handleKeyDown = (event: KeyboardEvent) => {
        if (event.key === 'Escape') {
          setReceiveOpen(false);
          setReceiveProduct(null);
        }
      };
      window.addEventListener('keydown', handleKeyDown);
      return () => window.removeEventListener('keydown', handleKeyDown);
    }
  }, [receiveOpen, receiveProduct]);

  const categories = useMemo(() => {
    const cats = new Set(products.map((p) => p.description || 'Uncategorized').filter(Boolean));
    return ['All', ...Array.from(cats).sort()];
  }, [products]);

  const filteredProducts = useMemo(() => {
    const query = search.trim().toLowerCase();
    return products.filter((p) => {
      const matchesSearch =
        !query ||
        p.name.toLowerCase().includes(query) ||
        (p.sku && p.sku.toLowerCase().includes(query));
      const matchesCategory = categoryFilter === 'All' || (p.description || 'Uncategorized') === categoryFilter;
      const status = stockStatus(p);
      const matchesStatus = statusFilter === 'All' || status.label === statusFilter;
      return matchesSearch && matchesCategory && matchesStatus;
    });
  }, [products, search, categoryFilter, statusFilter]);

  const lowStockCount = useMemo(
    () => products.filter((p) => p.is_low_stock && p.quantity > 0).length,
    [products]
  );

  const outOfStockCount = useMemo(
    () => products.filter((p) => p.quantity === 0).length,
    [products]
  );

  const openReceive = (product: Product) => {
    setReceiveProduct(product);
    setReceiveForm(EMPTY_RECEIVE);
    setReceiveError('');
    setReceiveOpen(true);
  };

  const closeReceive = () => {
    if (receiveSaving) return;
    setReceiveOpen(false);
    setReceiveProduct(null);
  };

  const handleReceiveSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!receiveProduct) return;

    const qty = parseInt(receiveForm.quantity, 10);
    if (!Number.isFinite(qty) || qty < 1) {
      setReceiveError('Enter a quantity greater than zero.');
      return;
    }

    setReceiveSaving(true);
    setReceiveError('');

    try {
      await apiFetch(`/api/products/${receiveProduct.id}/receive/`, {
        method: 'POST',
        body: JSON.stringify({ quantity: qty, note: receiveForm.note.trim() }),
      });
      await loadProducts();
      setReceiveOpen(false);
      setReceiveProduct(null);
    } catch (err) {
      setReceiveError(err instanceof ApiError ? err.message : 'Failed to receive stock');
    } finally {
      setReceiveSaving(false);
    }
  };

  const handleRowClick = (product: Product) => {
    navigate(`/inventory/product/${product.id}`);
  };

  return (
    <div className="mm-page inventory-page">
      <header className="mm-page-header">
        <div>
          <h1 className="mm-page-title">Inventory</h1>
          <p className="mm-page-subtitle">
            {products.length} product{products.length === 1 ? '' : 's'} ·{' '}
            {lowStockCount} low stock · {outOfStockCount} out of stock
          </p>
        </div>
        {isManagerOrAdmin && (
          <button
            type="button"
            className="mm-btn mm-btn--primary"
            onClick={() => navigate('/inventory/new')}
          >
            + New product
          </button>
        )}
      </header>

      {error && (
        <div className="mm-alert mm-alert--danger" role="alert">
          {error}
        </div>
      )}

      <section className="mm-card inventory-panel" aria-label="Products">
        <div className="inventory-toolbar">
          <div className="inventory-field inventory-search">
            <label className="mm-visually-hidden" htmlFor="inventory-search">
              Search products
            </label>
            <input
              id="inventory-search"
              className="mm-input"
              type="search"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search by name or SKU"
              aria-label="Search products"
            />
          </div>
          <div className="inventory-filters">
            <div className="inventory-field">
              <label className="mm-visually-hidden" htmlFor="inventory-category">
                Filter by category
              </label>
              <select
                id="inventory-category"
                className="mm-select"
                value={categoryFilter}
                onChange={(e) => setCategoryFilter(e.target.value)}
                aria-label="Filter by category"
              >
                {categories.map((cat) => (
                  <option key={cat} value={cat}>
                    {cat}
                  </option>
                ))}
              </select>
            </div>
            <div className="inventory-field">
              <label className="mm-visually-hidden" htmlFor="inventory-status">
                Filter by stock status
              </label>
              <select
                id="inventory-status"
                className="mm-select"
                value={statusFilter}
                onChange={(e) => setStatusFilter(e.target.value)}
                aria-label="Filter by stock status"
              >
                <option value="All">All status</option>
                <option value="OK">OK</option>
                <option value="Low">Low</option>
                <option value="Out">Out</option>
              </select>
            </div>
          </div>
        </div>

        {loading && (
          <div className="mm-loading" role="status">
            <span className="mm-spinner" aria-hidden="true" />
            Loading products...
          </div>
        )}

        {!loading && !error && filteredProducts.length === 0 && (
          <div className="mm-empty inventory-empty">
            <p className="mm-empty-title">
              {products.length === 0 ? 'No products yet' : 'No matching products'}
            </p>
            <p className="mm-empty-text">
              {products.length === 0
                ? 'Create the first product to start tracking inventory.'
                : 'Try a different search term, category, or stock status.'}
            </p>
          </div>
        )}

        {!loading && filteredProducts.length > 0 && (
          <div className="mm-table-wrap inventory-table-wrap">
            <table className="mm-table inventory-table">
              <thead>
                <tr>
                  <th>ID</th>
                  <th>Product</th>
                  <th className="num">Stock</th>
                  <th className="num">Min</th>
                  <th>Status</th>
                  <th className="col-supplier">Supplier</th>
                  <th>
                    <span className="mm-visually-hidden">Actions</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {filteredProducts.map((product) => {
                  const status = stockStatus(product);
                  const supplierName = product.supplier_name || '—';
                  return (
                    <tr
                      key={product.id}
                      className="inventory-row"
                      onClick={() => handleRowClick(product)}
                    >
                      <td className="col-id">{product.id}</td>
                      <td className="col-name">
                        <span className="product-name">{product.name}</span>
                        {product.sku && (
                          <span className="product-sku">{product.sku}</span>
                        )}
                      </td>
                      <td className="col-stock num">{product.quantity}</td>
                      <td className="col-min num">{product.min_stock}</td>
                      <td className="col-status">
                        <span className={status.badge}>{status.label}</span>
                      </td>
                      <td className="col-supplier">{supplierName}</td>
                      <td className="col-actions">
                        {isManagerOrAdmin && (
                          <button
                            type="button"
                            className="mm-btn mm-btn--outline mm-btn--sm"
                            onClick={(e) => {
                              e.stopPropagation();
                              openReceive(product);
                            }}
                            aria-label={`Receive stock for ${product.name}`}
                            title="Receive stock"
                            disabled={receiveSaving}
                          >
                            Receive
                          </button>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {/* Receive stock modal */}
      {receiveOpen && receiveProduct && (
        <div
          className="mm-modal-backdrop"
          onMouseDown={(e) => {
            if (e.target === e.currentTarget) closeReceive();
          }}
        >
          <div
            className="mm-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="receive-title"
          >
            <header className="mm-modal-header">
              <h2 id="receive-title" className="mm-modal-title">
                Receive stock
              </h2>
              <button
                type="button"
                className="mm-modal-close"
                onClick={closeReceive}
                aria-label="Close"
              >
                <Icon name="x" size={20} />
              </button>
            </header>
            <form className="receive-form" onSubmit={handleReceiveSubmit}>
              <div className="mm-modal-body receive-body">
                <p className="mm-muted">
                  Add stock for <strong>{receiveProduct.name}</strong>. Current
                  stock: <strong>{receiveProduct.quantity}</strong> (min:{' '}
                  {receiveProduct.min_stock}).
                </p>

                {receiveError && (
                  <div className="mm-alert mm-alert--danger" role="alert">
                    {receiveError}
                  </div>
                )}

                <label className="mm-field">
                  <span className="mm-label">Quantity to receive</span>
                  <input
                    className="mm-input"
                    type="number"
                    min="1"
                    step="1"
                    value={receiveForm.quantity}
                    onChange={(e) =>
                      setReceiveForm((c) => ({ ...c, quantity: e.target.value }))
                    }
                    required
                    autoFocus
                  />
                </label>

                <label className="mm-field">
                  <span className="mm-label">Note</span>
                  <input
                    className="mm-input"
                    type="text"
                    value={receiveForm.note}
                    onChange={(e) =>
                      setReceiveForm((c) => ({ ...c, note: e.target.value }))
                    }
                    maxLength={200}
                    placeholder="Optional, e.g. PO #1234"
                  />
                </label>
              </div>

              <footer className="mm-modal-footer">
                <button
                  type="button"
                  className="mm-btn mm-btn--ghost"
                  onClick={closeReceive}
                  disabled={receiveSaving}
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  className="mm-btn mm-btn--primary"
                  disabled={receiveSaving}
                >
                  {receiveSaving ? 'Receiving...' : 'Confirm receive'}
                </button>
              </footer>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
