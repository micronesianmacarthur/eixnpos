import {
  useCallback,
  useEffect,
  useState,
  type FormEvent,
} from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { apiFetch, ApiError } from './api';
import { useAuth } from './authContext';
import './ProductPage.css';

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
  non_stock: boolean;
}

interface Supplier {
  id: number;
  name: string;
  contact_name: string;
  email: string;
  phone: string;
  address: string;
  notes: string;
  active: boolean;
  created_at: string;
}

interface StockMovement {
  id: number;
  product: number;
  product_name: string;
  movement_type: 'sale' | 'receive' | 'damage' | 'adjustment';
  quantity: number;
  reference: string;
  note: string;
  created_at: string;
}

interface ProductForm {
  name: string;
  sku: string;
  description: string;
  price: string;
  cost: string;
  min_stock: string;
  supplier: string;
  active: boolean;
  non_stock: boolean;
  initial_stock: string;
}

const EMPTY_FORM: ProductForm = {
  name: '',
  sku: '',
  description: '',
  price: '0.00',
  cost: '0.00',
  min_stock: '0',
  supplier: '',
  active: true,
  non_stock: false,
  initial_stock: '0',
};

function toNumber(value: string | number | null | undefined): number {
  const parsed = Number(value ?? 0);
  return Number.isFinite(parsed) ? parsed : 0;
}

const TYPE_LABELS: Record<StockMovement['movement_type'], string> = {
  sale: 'Sale',
  receive: 'Receive',
  damage: 'Damage',
  adjustment: 'Adjustment',
};

const TYPE_BADGES: Record<StockMovement['movement_type'], string> = {
  sale: 'mm-badge--info',
  receive: 'mm-badge--success',
  damage: 'mm-badge--danger',
  adjustment: 'mm-badge--warning',
};

function formatDate(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

function normalizeList<T>(data: T[] | { results: T[] }): T[] {
  return Array.isArray(data) ? data : data.results;
}

export default function ProductPage() {
  const navigate = useNavigate();
  const { id } = useParams<{ id: string }>();
  const { me } = useAuth();
  const isManagerOrAdmin = me ? (me.user.role === 'manager' || me.user.role === 'admin') : false;

  const isEdit = id !== undefined;
  const productId = isEdit ? parseInt(id, 10) : null;

  const [suppliers, setSuppliers] = useState<Supplier[]>([]);
  const [movements, setMovements] = useState<StockMovement[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [movementLoading, setMovementLoading] = useState(false);
  const [movementError, setMovementError] = useState('');
  const [error, setError] = useState('');

  const [form, setForm] = useState<ProductForm>(EMPTY_FORM);

  const loadSuppliers = useCallback(async () => {
    try {
      const data = await apiFetch<Supplier[] | { results: Supplier[] }>('/api/suppliers/');
      const list = Array.isArray(data) ? data : data.results;
      setSuppliers(list.filter((s) => s.active !== false));
    } catch {
      // non-fatal
    }
  }, []);

  const loadProduct = useCallback(async () => {
    if (!isEdit || !productId) return;
    try {
      const product = await apiFetch<Product>(`/api/products/${productId}/`);
      setForm({
        name: product.name,
        sku: product.sku || '',
        description: product.description,
        price: product.price,
        cost: product.cost,
        min_stock: String(product.min_stock),
        supplier: product.supplier ? String(product.supplier) : '',
        active: product.active,
        non_stock: product.non_stock,
        initial_stock: '0',
      });
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to load product');
    } finally {
      setLoading(false);
    }
  }, [isEdit, productId]);

  const loadMovements = useCallback(async () => {
    if (!isEdit || !productId) return;
    setMovementLoading(true);
    setMovementError('');
    try {
      const data = await apiFetch<StockMovement[] | { results: StockMovement[] }>(
        `/api/inventory/?product=${productId}`
      );
      setMovements(normalizeList(data));
    } catch (err) {
      setMovementError(err instanceof ApiError ? err.message : 'Failed to load stock movements');
    } finally {
      setMovementLoading(false);
    }
  }, [isEdit, productId]);

  useEffect(() => {
    Promise.all([loadSuppliers(), loadProduct()]);
  }, [loadSuppliers, loadProduct]);

  useEffect(() => {
    if (!isEdit) {
      setLoading(false);
    }
  }, [isEdit]);

  useEffect(() => {
    if (isEdit && productId) {
      loadMovements();
    }
  }, [isEdit, productId, loadMovements]);

  const handleChange = (field: keyof ProductForm, value: string | boolean) => {
    setForm((prev) => ({ ...prev, [field]: value }));
  };

  const validateForm = (): string | null => {
    if (!form.name.trim()) return 'Name is required.';
    if (toNumber(form.price) < 0) return 'Price cannot be negative.';
    if (toNumber(form.cost) < 0) return 'Cost cannot be negative.';
    if (toNumber(form.min_stock) < 0) return 'Min stock cannot be negative.';
    if (!isEdit && toNumber(form.initial_stock) < 0) return 'Initial stock cannot be negative.';
    return null;
  };

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();

    const validationError = validateForm();
    if (validationError) {
      setError(validationError);
      return;
    }

    setSaving(true);
    setError('');

    const payload = {
      name: form.name.trim(),
      sku: form.sku.trim() || null,
      description: form.description.trim(),
      price: form.price.trim() || '0',
      cost: form.cost.trim() || '0',
      min_stock: parseInt(form.min_stock, 10) || 0,
      supplier: form.supplier ? parseInt(form.supplier, 10) : null,
      active: form.active,
      non_stock: form.non_stock,
    };

    try {
      if (isEdit && productId) {
        await apiFetch<Product>(`/api/products/${productId}/`, {
          method: 'PATCH',
          body: JSON.stringify(payload),
        });
        navigate('/inventory', { replace: true });
      } else {
        const saved = await apiFetch<Product>('/api/products/', {
          method: 'POST',
          body: JSON.stringify(payload),
        });
        const initialQty = parseInt(form.initial_stock, 10) || 0;
        if (initialQty > 0) {
          await apiFetch(`/api/products/${saved.id}/receive/`, {
            method: 'POST',
            body: JSON.stringify({ quantity: initialQty, note: 'Initial stock' }),
          });
        }
        navigate('/inventory', { replace: true });
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to save product');
    } finally {
      setSaving(false);
    }
  };

  const handleCancel = () => {
    navigate('/inventory');
  };

  const handleDelete = async () => {
    if (!isEdit || !productId) return;
    const confirmed = window.confirm(
      'Delete this product? This cannot be undone.'
    );
    if (!confirmed) return;

    setSaving(true);
    setError('');
    try {
      await apiFetch(`/api/products/${productId}/`, { method: 'DELETE' });
      navigate('/inventory', { replace: true });
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to delete product');
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return (
      <div className="mm-page">
        <div className="mm-loading">
          <span className="mm-spinner" aria-hidden="true" />
          Loading...
        </div>
      </div>
    );
  }

  const formFields = (
    <>
      <section className="form-section">
        <h2 className="mm-card-title">Basic info</h2>

        <label className="mm-field">
          <span className="mm-label">
            Name <span className="required" aria-hidden="true">*</span>
          </span>
          <input
            type="text"
            className="mm-input"
            value={form.name}
            onChange={(e) => handleChange('name', e.target.value)}
            required
            autoFocus
            maxLength={200}
            placeholder="Product name"
          />
        </label>

        <label className="mm-field">
          <span className="mm-label">SKU</span>
          <input
            type="text"
            className="mm-input"
            value={form.sku}
            onChange={(e) => handleChange('sku', e.target.value)}
            maxLength={64}
            placeholder="Optional, e.g. COF-REG-001"
          />
        </label>

        <label className="mm-field">
          <span className="mm-label">Description (category)</span>
          <input
            type="text"
            className="mm-input"
            value={form.description}
            onChange={(e) => handleChange('description', e.target.value)}
            maxLength={200}
            placeholder="Optional, e.g. Drinks, Food, Snacks"
          />
        </label>

        <label className="mm-field">
          <span className="mm-label">Supplier</span>
          <select
            className="mm-select"
            value={form.supplier}
            onChange={(e) => handleChange('supplier', e.target.value)}
          >
            <option value="">— No supplier —</option>
            {suppliers.map((supplier) => (
              <option key={supplier.id} value={String(supplier.id)}>
                {supplier.name}
              </option>
            ))}
          </select>
        </label>
      </section>

      <section className="form-section">
        <h2 className="mm-card-title">Pricing &amp; stock</h2>

        <div className="field-row">
          <label className="mm-field">
            <span className="mm-label">
              Price <span className="required" aria-hidden="true">*</span>
            </span>
            <input
              type="number"
              className="mm-input"
              step="0.01"
              min="0"
              value={form.price}
              onChange={(e) => handleChange('price', e.target.value)}
              required
            />
          </label>

          <label className="mm-field">
            <span className="mm-label">
              Cost <span className="required" aria-hidden="true">*</span>
            </span>
            <input
              type="number"
              className="mm-input"
              step="0.01"
              min="0"
              value={form.cost}
              onChange={(e) => handleChange('cost', e.target.value)}
              required
            />
          </label>

          <label className="mm-field">
            <span className="mm-label">
              Min stock <span className="required" aria-hidden="true">*</span>
            </span>
            <input
              type="number"
              className="mm-input"
              step="1"
              min="0"
              value={form.min_stock}
              onChange={(e) => handleChange('min_stock', e.target.value)}
              required
            />
          </label>

          {!isEdit && (
            <label className="mm-field">
              <span className="mm-label">Initial stock</span>
              <input
                type="number"
                className="mm-input"
                step="1"
                min="0"
                value={form.initial_stock}
                onChange={(e) => handleChange('initial_stock', e.target.value)}
                placeholder="Optional, e.g. 50"
              />
            </label>
          )}
        </div>
      </section>

      <section className="form-section">
        <h2 className="mm-card-title">Status</h2>

        <label className="checkbox-field">
          <input
            type="checkbox"
            checked={form.active}
            onChange={(e) => handleChange('active', e.target.checked)}
          />
          <span className="mm-label">Active (visible in sale screen)</span>
        </label>

        <label className="checkbox-field">
          <input
            type="checkbox"
            checked={form.non_stock}
            onChange={(e) => handleChange('non_stock', e.target.checked)}
          />
          <span className="mm-label">Non-stock product (service, no inventory tracking)</span>
        </label>
      </section>

      <footer className="mm-card-footer product-form-actions">
        <button
          type="button"
          className="mm-btn mm-btn--ghost"
          onClick={handleCancel}
          disabled={saving}
        >
          Cancel
        </button>
        <button type="submit" className="mm-btn mm-btn--primary" disabled={saving}>
          {saving ? 'Saving...' : isEdit ? 'Save changes' : 'Create product'}
        </button>
      </footer>
    </>
  );

  return (
    <div className="mm-page product-page">
      <header className="mm-page-header product-heading">
        <div className="product-heading-copy">
          <button
            type="button"
            className="mm-btn mm-btn--ghost mm-btn--sm product-back"
            onClick={handleCancel}
          >
            Back to inventory
          </button>
          <h1 className="mm-page-title">{isEdit ? 'Edit product' : 'New product'}</h1>
          <p className="mm-page-subtitle">
            {isEdit ? 'Update product details' : 'Add a new product to the catalog'}
          </p>
        </div>
        {isEdit && isManagerOrAdmin && (
          <button
            type="button"
            className="mm-btn mm-btn--danger"
            onClick={handleDelete}
            disabled={saving}
          >
            Delete product
          </button>
        )}
      </header>

      {error && (
        <div className="mm-alert mm-alert--danger" role="alert">
          {error}
        </div>
      )}

      {isEdit ? (
        <div className="edit-layout">
          <form className="mm-card product-form" onSubmit={handleSubmit}>
            {formFields}
          </form>

          <section className="mm-card movements-section">
            <header className="mm-card-header">
              <div>
                <h2 className="mm-card-title">Stock movements</h2>
                <p className="mm-card-subtitle">
                  History of stock changes for this product.
                </p>
              </div>
              {!movementLoading && !movementError && movements.length > 0 && (
                <span className="mm-badge">{movements.length} entries</span>
              )}
            </header>

            {movementLoading && (
              <div className="mm-loading">
                <span className="mm-spinner" aria-hidden="true" />
                Loading...
              </div>
            )}

            {movementError && (
              <div className="mm-alert mm-alert--danger" role="alert">
                {movementError}
              </div>
            )}

            {!movementLoading && !movementError && movements.length === 0 && (
              <div className="mm-empty movements-empty">
                <h3 className="mm-empty-title">No stock movements yet</h3>
                <p className="mm-empty-text">
                  Sales, received stock, damage, and adjustments will appear here.
                </p>
              </div>
            )}

            {movements.length > 0 && (
              <div className="mm-table-wrap">
                <table className="mm-table product-movements">
                  <thead>
                    <tr>
                      <th>Date</th>
                      <th>Type</th>
                      <th className="num">Qty</th>
                      <th>Reference</th>
                      <th>Note</th>
                    </tr>
                  </thead>
                  <tbody>
                    {movements.map((movement) => (
                      <tr key={movement.id}>
                        <td>{formatDate(movement.created_at)}</td>
                        <td>
                          <span
                            className={`mm-badge ${TYPE_BADGES[movement.movement_type]}`}
                          >
                            {TYPE_LABELS[movement.movement_type]}
                          </span>
                        </td>
                        <td className="num">
                          {movement.quantity > 0 ? '+' : ''}{movement.quantity}
                        </td>
                        <td>{movement.reference || '—'}</td>
                        <td>{movement.note || '—'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>
        </div>
      ) : (
        <form className="mm-card product-form" onSubmit={handleSubmit}>
          {formFields}
        </form>
      )}
    </div>
  );
}
