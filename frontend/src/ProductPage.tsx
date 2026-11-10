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

interface ProductForm {
  name: string;
  sku: string;
  description: string;
  price: string;
  cost: string;
  min_stock: string;
  supplier: string;
  active: boolean;
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
};

function toNumber(value: string | number | null | undefined): number {
  const parsed = Number(value ?? 0);
  return Number.isFinite(parsed) ? parsed : 0;
}

export default function ProductPage() {
  const navigate = useNavigate();
  const { id } = useParams<{ id: string }>();
  const { me } = useAuth();
  const isManagerOrAdmin = me ? (me.user.role === 'manager' || me.user.role === 'admin') : false;

  const isEdit = id !== undefined;
  const productId = isEdit ? parseInt(id, 10) : null;

  const [suppliers, setSuppliers] = useState<Supplier[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
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
      });
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to load product');
    } finally {
      setLoading(false);
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

  const handleChange = (field: keyof ProductForm, value: string | boolean) => {
    setForm((prev) => ({ ...prev, [field]: value }));
  };

  const validateForm = (): string | null => {
    if (!form.name.trim()) return 'Name is required.';
    if (toNumber(form.price) < 0) return 'Price cannot be negative.';
    if (toNumber(form.cost) < 0) return 'Cost cannot be negative.';
    if (toNumber(form.min_stock) < 0) return 'Min stock cannot be negative.';
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
      } else {
        await apiFetch<Product>('/api/products/', {
          method: 'POST',
          body: JSON.stringify(payload),
        });
      }
      navigate('/inventory', { replace: true });
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
    </section>
  );
}