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

const TYPE_COLORS: Record<StockMovement['movement_type'], string> = {
  sale: 'type-sale',
  receive: 'type-receive',
  damage: 'type-damage',
  adjustment: 'type-adjustment',
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
      <section className="product-page">
        <p className="loading">Loading...</p>
      </section>
    );
  }

  return (
    <section className="product-page">
      <header className="product-heading">
        <div>
          <h1>{isEdit ? 'Edit product' : 'New product'}</h1>
          <p className="page-subtitle">
            {isEdit ? 'Update product details' : 'Add a new product to the catalog'}
          </p>
        </div>
        {isEdit && isManagerOrAdmin && (
          <button
            type="button"
            className="btn btn-danger-ghost"
            onClick={handleDelete}
            disabled={saving}
          >
            Delete product
          </button>
        )}
      </header>

      {error && <p className="error" role="alert">{error}</p>}

      <form className="product-form" onSubmit={handleSubmit}>
        <div className="form-section">
          <h2>Basic info</h2>

          <label className="field">
            <span>Name <span className="required">*</span></span>
            <input
              type="text"
              value={form.name}
              onChange={(e) => handleChange('name', e.target.value)}
              required
              autoFocus
              maxLength={200}
              placeholder="Product name"
            />
          </label>

          <label className="field">
            <span>SKU</span>
            <input
              type="text"
              value={form.sku}
              onChange={(e) => handleChange('sku', e.target.value)}
              maxLength={64}
              placeholder="Optional, e.g. COF-REG-001"
            />
          </label>

          <label className="field">
            <span>Description (category)</span>
            <input
              type="text"
              value={form.description}
              onChange={(e) => handleChange('description', e.target.value)}
              maxLength={200}
              placeholder="Optional, e.g. Drinks, Food, Snacks"
            />
          </label>

          <label className="field">
            <span>Supplier</span>
            <select
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
        </div>

        <div className="form-section">
          <h2>Pricing & stock</h2>

          <div className="field-row">
            <label className="field">
              <span>Price <span className="required">*</span></span>
              <input
                type="number"
                step="0.01"
                min="0"
                value={form.price}
                onChange={(e) => handleChange('price', e.target.value)}
                required
              />
            </label>

            <label className="field">
              <span>Cost <span className="required">*</span></span>
              <input
                type="number"
                step="0.01"
                min="0"
                value={form.cost}
                onChange={(e) => handleChange('cost', e.target.value)}
                required
              />
            </label>

            <label className="field">
              <span>Min stock <span className="required">*</span></span>
              <input
                type="number"
                step="1"
                min="0"
                value={form.min_stock}
                onChange={(e) => handleChange('min_stock', e.target.value)}
                required
              />
            </label>

            {!isEdit && (
              <label className="field">
                <span>Initial stock</span>
                <input
                  type="number"
                  step="1"
                  min="0"
                  value={form.initial_stock}
                  onChange={(e) => handleChange('initial_stock', e.target.value)}
                  placeholder="Optional, e.g. 50"
                />
              </label>
            )}
          </div>
        </div>

        <div className="form-section">
          <h2>Status</h2>

          <label className="field checkbox-field">
            <input
              type="checkbox"
              checked={form.active}
              onChange={(e) => handleChange('active', e.target.checked)}
            />
            <span>Active (visible in sale screen)</span>
          </label>
        </div>

        <footer className="form-actions">
          <button type="button" className="btn btn-ghost" onClick={handleCancel} disabled={saving}>
            Cancel
          </button>
          <button type="submit" className="btn btn-primary" disabled={saving}>
            {saving ? 'Saving...' : isEdit ? 'Save changes' : 'Create product'}
          </button>
        </footer>
      </form>

      {isEdit && (
        <section className="movements-section">
          <h2>Stock Movements</h2>
          {movementLoading && <p className="muted">Loading...</p>}
          {movementError && <p className="error" role="alert">{movementError}</p>}
          {!movementLoading && !movementError && movements.length === 0 && (
            <p className="muted">No stock movements recorded yet.</p>
          )}
          {movements.length > 0 && (
            <div className="table-wrap">
              <table>
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
                        <span className={`movement-type ${TYPE_COLORS[movement.movement_type]}`}>
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
      )}
    </section>
  );
}