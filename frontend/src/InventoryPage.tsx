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

function stockStatus(product: Product): { label: string; className: string } {
  if (product.quantity === 0) return { label: 'Out', className: 'status-out' };
  if (product.is_low_stock) return { label: 'Low', className: 'status-low' };
  return { label: 'OK', className: 'status-ok' };
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
    <section className="inventory-page">
      <header className="inventory-heading">
        <div>
          <h1>Inventory</h1>
          <p className="page-subtitle">
            {products.length} product{products.length === 1 ? '' : 's'} ·
            {lowStockCount} low stock · {outOfStockCount} out of stock
          </p>
        </div>
        {isManagerOrAdmin && (
          <button type="button" className="btn btn-primary" onClick={() => navigate('/inventory/new')}>
            + New product
          </button>
        )}
      </header>

      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}

      <div className="product-list-panel-full">
        <div className="product-toolbar">
          <div className="product-search">
            <input
              type="search"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search by name or SKU"
              aria-label="Search products"
            />
          </div>
          <div className="product-filters">
            <select
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
            <select
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

        <div className="product-list" role="listbox" aria-label="Products">
          {loading && <p className="product-list-message">Loading products...</p>}
          {error && !loading && (
            <p className="product-list-message error" role="alert">
              {error}
            </p>
          )}
          {!loading && !error && filteredProducts.length === 0 && (
            <p className="product-list-message">
              {products.length === 0
                ? 'No products yet. Create the first one.'
                : 'No products match your filters.'}
            </p>
          )}

          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>ID</th>
                  <th>Product</th>
                  <th>Stock</th>
                  <th>Min</th>
                  <th>Status</th>
                  <th>Supplier</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {filteredProducts.map((product) => {
                  const status = stockStatus(product);
                  const supplierName = product.supplier_name || '—';
                  return (
                    <tr
                      key={product.id}
                      onClick={() => handleRowClick(product)}
                      role="option"
                    >
                      <td className="col-id">{product.id}</td>
                      <td className="col-name">
                        <span className="product-name">{product.name}</span>
                        {product.sku && (
                          <span className="product-sku">{product.sku}</span>
                        )}
                      </td>
                      <td className="col-stock">{product.quantity}</td>
                      <td className="col-min">{product.min_stock}</td>
                      <td className="col-status">
                        <span className={`status-badge ${status.className}`}>{status.label}</span>
                      </td>
                      <td className="col-supplier">{supplierName}</td>
                      <td className="col-actions">
                        {isManagerOrAdmin && (
                          <button
                            type="button"
                            className="btn-icon receive-btn"
                            onClick={(e) => {
                              e.stopPropagation();
                              openReceive(product);
                            }}
                            title="Receive stock"
                            disabled={receiveSaving}
                          >
                            ↓
                          </button>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      </div>

      {/* Receive stock modal */}
      {receiveOpen && receiveProduct && (
        <div
          className="modal-backdrop"
          onMouseDown={(e) => {
            if (e.target === e.currentTarget) closeReceive();
          }}
        >
          <div className="modal" role="dialog" aria-modal="true" aria-labelledby="receive-title">
            <header className="modal-header">
              <h2 id="receive-title">Receive stock</h2>
              <button type="button" className="icon-button" onClick={closeReceive} aria-label="Close">
                ×
              </button>
            </header>
            <form className="modal-form" onSubmit={handleReceiveSubmit}>
              <p className="modal-context">
                Add stock for <strong>{receiveProduct.name}</strong>. Current stock:{' '}
                <strong>{receiveProduct.quantity}</strong> (min: {receiveProduct.min_stock}).
              </p>

              {receiveError && (
                <p className="error" role="alert">{receiveError}</p>
              )}

              <label className="field">
                <span>Quantity to receive</span>
                <input
                  type="number"
                  min="1"
                  step="1"
                  value={receiveForm.quantity}
                  onChange={(e) => setReceiveForm((c) => ({ ...c, quantity: e.target.value }))}
                  required
                  autoFocus
                />
              </label>

              <label className="field">
                <span>Note</span>
                <input
                  type="text"
                  value={receiveForm.note}
                  onChange={(e) => setReceiveForm((c) => ({ ...c, note: e.target.value }))}
                  maxLength={200}
                  placeholder="Optional, e.g. PO #1234"
                />
              </label>

              <footer className="modal-actions">
                <button
                  type="button"
                  className="btn btn-ghost"
                  onClick={closeReceive}
                  disabled={receiveSaving}
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  className="btn btn-primary"
                  disabled={receiveSaving}
                >
                  {receiveSaving ? 'Receiving...' : 'Confirm receive'}
                </button>
              </footer>
            </form>
          </div>
        </div>
      )}
    </section>
  );
}