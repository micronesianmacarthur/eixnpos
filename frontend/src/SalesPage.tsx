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
import './SalesPage.css';

interface Product {
  id: number;
  name: string;
  price: string;
  quantity: number;
  is_low_stock: boolean;
  non_stock: boolean;
  active: boolean;
}

interface SaleLine {
  id: number;
  product: number;
  product_name: string;
  quantity: number;
  unit_price: string;
  discount_type: string;
  discount_value: string;
  discount_amount: string;
  line_total: string;
}

interface Payment {
  id: number;
  method: string;
  amount: string;
  tendered: string | null;
  change_due: string;
  manager_approved: boolean;
}

interface Sale {
  id: number;
  number: number;
  cashier: number;
  customer: number | null;
  status: string;
  subtotal: string;
  discount_total: string;
  total: string;
  lines: SaleLine[];
  payments: Payment[];
  note?: string;
  needs_review?: boolean;
  created_at?: string;
  completed_at?: string | null;
}

interface Customer {
  id: number;
  name: string;
  balance: string;
  credit_limit: string;
}

interface BusinessInfo {
  company_name: string;
  phone: string;
  email: string;
  address: string;
  website: string;
}

const currency = new Intl.NumberFormat('en-US', {
  style: 'currency',
  currency: 'USD',
});

function toNumber(value: string | number | null | undefined): number {
  const parsed = Number(value ?? 0);
  return Number.isFinite(parsed) ? parsed : 0;
}

function formatMoney(value: string | number): string {
  return currency.format(toNumber(value));
}

function isManagerRole(role: string): boolean {
  return role === 'manager' || role === 'admin';
}

export default function SalesPage() {
  const navigate = useNavigate();
  const { me } = useAuth();
  const isManager = me ? isManagerRole(me.user.role) : false;

  const [products, setProducts] = useState<Product[]>([]);
  const [customers, setCustomers] = useState<Customer[]>([]);
  const [business, setBusiness] = useState<BusinessInfo | null>(null);

  const [productSearch, setProductSearch] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const [sale, setSale] = useState<Sale | null>(null);
  const [busy, setBusy] = useState(false);

  const [discountLineId, setDiscountLineId] = useState<number | null>(null);
  const [discountType, setDiscountType] = useState('percent');
  const [discountValue, setDiscountValue] = useState('');
  const [discountManagerUser, setDiscountManagerUser] = useState('');
  const [discountManagerPass, setDiscountManagerPass] = useState('');
  const [discountError, setDiscountError] = useState('');
  const [discountSaving, setDiscountSaving] = useState(false);

  const [payMethod, setPayMethod] = useState('cash');
  const [payTendered, setPayTendered] = useState('');
  const [payCustomer, setPayCustomer] = useState('');
  const [payManagerUser, setPayManagerUser] = useState('');
  const [payManagerPass, setPayManagerPass] = useState('');
  const [payError, setPayError] = useState('');
  const [paySaving, setPaySaving] = useState(false);
  const [paymentRecorded, setPaymentRecorded] = useState(false);
  const [saleNote, setSaleNote] = useState('');
  const [changeDue, setChangeDue] = useState('0');
  const [changeModalOpen, setChangeModalOpen] = useState(false);

  const [receiptSale, setReceiptSale] = useState<Sale | null>(null);

  const loadProducts = useCallback(async () => {
    try {
      const data = await apiFetch<Product[] | { results: Product[] }>(
        '/api/products/',
      );
      const list = Array.isArray(data) ? data : data.results;
      setProducts(list.filter((p) => p.active !== false));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to load products');
    }
  }, []);

  const loadCustomers = useCallback(async () => {
    try {
      const data = await apiFetch<Customer[] | { results: Customer[] }>(
        '/api/customers/',
      );
      const list = Array.isArray(data) ? data : data.results;
      setCustomers(list);
    } catch {
      // non-fatal
    }
  }, []);

  const loadBusiness = useCallback(async () => {
    try {
      const data = await apiFetch<BusinessInfo>('/api/business/');
      setBusiness(data);
    } catch {
      // non-fatal
    }
  }, []);

  useEffect(() => {
    Promise.all([loadProducts(), loadCustomers(), loadBusiness()]).finally(() =>
      setLoading(false),
    );
  }, [loadProducts, loadCustomers, loadBusiness]);

  const filteredProducts = useMemo(() => {
    const q = productSearch.trim().toLowerCase();
    if (!q) return products;
    return products.filter((p) => p.name.toLowerCase().includes(q));
  }, [products, productSearch]);

  const ensureSale = useCallback(async (): Promise<Sale> => {
    if (sale && sale.status === 'open') return sale;
    const created = await apiFetch<Sale>('/api/sales/', { method: 'POST' });
    setSale(created);
    return created;
  }, [sale]);

  const refreshSale = useCallback(async (id: number) => {
    const updated = await apiFetch<Sale>(`/api/sales/${id}/`);
    setSale(updated);
    setPayTendered(updated.total);
    return updated;
  }, []);

  const addToCart = useCallback(
    async (product: Product) => {
      setBusy(true);
      setError('');
      try {
        const current = await ensureSale();
        const existing = current.lines.find((l) => l.product === product.id);
        if (existing) {
          await apiFetch(`/api/sales/${current.id}/lines/${existing.id}/`, {
            method: 'PATCH',
            body: JSON.stringify({ quantity: existing.quantity + 1 }),
          });
        } else {
          await apiFetch(`/api/sales/${current.id}/add_line/`, {
            method: 'POST',
            body: JSON.stringify({ product: product.id, quantity: 1 }),
          });
        }
        await refreshSale(current.id);
      } catch (err) {
        setError(err instanceof ApiError ? err.message : 'Failed to add item');
      } finally {
        setBusy(false);
      }
    },
    [ensureSale, refreshSale],
  );

  const updateQty = useCallback(
    async (line: SaleLine, qty: number) => {
      if (!sale) return;
      setBusy(true);
      setError('');
      try {
        if (qty < 1) {
          await apiFetch(`/api/sales/${sale.id}/lines/${line.id}/`, {
            method: 'DELETE',
          });
        } else {
          await apiFetch(`/api/sales/${sale.id}/lines/${line.id}/`, {
            method: 'PATCH',
            body: JSON.stringify({ quantity: qty }),
          });
        }
        await refreshSale(sale.id);
      } catch (err) {
        setError(err instanceof ApiError ? err.message : 'Failed to update line');
      } finally {
        setBusy(false);
      }
    },
    [sale, refreshSale],
  );

  const openDiscount = (line: SaleLine) => {
    setDiscountLineId(line.id);
    setDiscountType(line.discount_type === 'none' ? 'percent' : line.discount_type);
    setDiscountValue(line.discount_value && toNumber(line.discount_value) > 0 ? line.discount_value : '');
    setDiscountManagerUser('');
    setDiscountManagerPass('');
    setDiscountError('');
  };

  const closeDiscount = () => {
    if (discountSaving) return;
    setDiscountLineId(null);
  };

  const handleDiscountSubmit = async (event: FormEvent) => {
    event.preventDefault();
    if (!sale || discountLineId === null) return;
    const value = discountValue.trim();
    if (value === '' || toNumber(value) < 0) {
      setDiscountError('Enter a valid discount value.');
      return;
    }
    setDiscountSaving(true);
    setDiscountError('');
    const payload: Record<string, unknown> = {
      discount_type: discountType,
      discount_value: value,
    };
    if (discountManagerUser || discountManagerPass) {
      payload.manager_username = discountManagerUser;
      payload.manager_password = discountManagerPass;
    }
    try {
      await apiFetch(`/api/sales/${sale.id}/lines/${discountLineId}/discount/`, {
        method: 'POST',
        body: JSON.stringify(payload),
      });
      await refreshSale(sale.id);
      setDiscountLineId(null);
    } catch (err) {
      setDiscountError(err instanceof ApiError ? err.message : 'Failed to apply discount');
    } finally {
      setDiscountSaving(false);
    }
  };

  const recordPayment = async (method: string) => {
    setPayMethod(method);
    if (!sale) return;
    const tendered = payTendered.trim();
    if (toNumber(tendered) <= 0) {
      setPayError('Enter an amount greater than zero.');
      return;
    }
    // Record the full tendered amount (over-tender included); the backend
    // reconciles the change_due against the sale's outstanding balance.
    const appliedAmount = toNumber(tendered);
    setPaySaving(true);
    setPayError('');
    const payload: Record<string, unknown> = {
      method,
      amount: appliedAmount > 0 ? appliedAmount.toFixed(2) : tendered,
    };
    if (method === 'cash') {
      payload.tendered = tendered;
    }
    if (method === 'invoice') {
      if (!payCustomer) {
        setPayError('Select a customer.');
        setPaySaving(false);
        return;
      }
      payload.customer = Number(payCustomer);
      if (payManagerUser || payManagerPass) {
        payload.manager_username = payManagerUser;
        payload.manager_password = payManagerPass;
      }
    }
    try {
      await apiFetch(`/api/sales/${sale.id}/set_payment/`, {
        method: 'POST',
        body: JSON.stringify(payload),
      });
      const updated = await refreshSale(sale.id);
      setPaymentRecorded(true);
      // reset manager creds
      setPayManagerUser('');
      setPayManagerPass('');
      void updated;
    } catch (err) {
      setPayError(err instanceof ApiError ? err.message : 'Failed to record payment');
    } finally {
      setPaySaving(false);
    }
  };

  const exactAmount = () => {
    const recordedTotal = sale?.payments.reduce((sum, payment) => sum + toNumber(payment.amount), 0) ?? 0;
    const remaining = Math.max(0, toNumber(sale?.total ?? 0) - recordedTotal);
    setPayTendered(remaining > 0 ? remaining.toFixed(2) : (sale?.total ?? '0'));
  };

  const completeSale = useCallback(async () => {
    if (!sale || sale.lines.length === 0) {
      setError('Add at least one item before completing.');
      return;
    }
    setBusy(true);
    setError('');
    try {
      if (sale && toNumber(sale.total) > 0 && (!paymentRecorded || sale.payments.length === 0)) {
        // auto-record payment with current settings
        const method = payMethod;
        const tendered = payTendered.trim() || sale.total;
        const payload: Record<string, unknown> = {
          method,
          amount: tendered,
        };
        if (method === 'cash') {
          payload.tendered = tendered;
        }
        if (method === 'invoice') {
          if (!payCustomer) throw new Error('Select a customer for invoice payment.');
          payload.customer = Number(payCustomer);
          if (payManagerUser || payManagerPass) {
            payload.manager_username = payManagerUser;
            payload.manager_password = payManagerPass;
          }
        }
        await apiFetch(`/api/sales/${sale.id}/set_payment/`, {
          method: 'POST',
          body: JSON.stringify(payload),
        });
      }
      const completed = await apiFetch<Sale & { change_due?: string }>(`/api/sales/${sale.id}/complete/`, {
        method: 'POST',
        body: JSON.stringify({ note: saleNote.trim() }),
      });
      setReceiptSale(completed);
      setChangeDue(completed.change_due ?? '0');
      setChangeModalOpen(true);
      setSale(null);
      setPaymentRecorded(false);
      setPayTendered('');
      setSaleNote('');
      await loadProducts();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : err instanceof Error ? err.message : 'Failed to complete sale');
    } finally {
      setBusy(false);
    }
  }, [sale, paymentRecorded, payMethod, payTendered, payCustomer, payManagerUser, payManagerPass, loadProducts]);

  const voidSale = useCallback(async () => {
    if (!sale) {
      return;
    }
    const confirmed = window.confirm('Void this sale?');
    if (!confirmed) return;
    setBusy(true);
    setError('');
    try {
      await apiFetch(`/api/sales/${sale.id}/void/`, { method: 'POST' });
      setSale(null);
      setPaymentRecorded(false);
      setPayTendered('');
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to void sale');
    } finally {
      setBusy(false);
    }
  }, [sale]);

  const closeReceipt = () => {
    setReceiptSale(null);
  };

  const closeChange = () => {
    setChangeModalOpen(false);
  };

  const cartLines = sale?.lines ?? [];
  const subtotal = toNumber(sale?.subtotal);
  const discountTotal = toNumber(sale?.discount_total);
  const total = toNumber(sale?.total);

  const selectedCustomer = useMemo(
    () => customers.find((c) => c.id === Number(payCustomer)) ?? null,
    [customers, payCustomer],
  );

  const availableCredit = selectedCustomer
    ? toNumber(selectedCustomer.credit_limit) - toNumber(selectedCustomer.balance)
    : 0;

  useEffect(() => {
    if (sale) {
      setPayTendered(sale.total);
    }
  }, [sale?.id, sale?.total]);

  const discountLine = useMemo(
    () => cartLines.find((l) => l.id === discountLineId) ?? null,
    [cartLines, discountLineId],
  );

  return (
    <section className="mm-page sales-page">
      <header className="mm-page-header sales-heading">
        <div>
          <h1 className="mm-page-title">Sale</h1>
          {sale && (
            <p className="mm-page-subtitle">

              Sale #{sale.number} · {sale.status}
            </p>
          )}
        </div>
        <div className="sales-heading-actions">
          <button
            type="button"
            className="mm-btn mm-btn--ghost"
            onClick={() => navigate('/sales-manager')}
          >
            Sales manager
          </button>
          {sale && (
            <button
              type="button"
              className="mm-btn mm-btn--danger"              onClick={() => void voidSale()}
              disabled={busy}
            >
              Void cart
            </button>
          )}
        </div>
      </header>

      {error && (
        <p className="mm-alert mm-alert--danger" role="alert">
          {error}
        </p>
      )}

      <div className="sales-layout">
        {/* Products */}
        <div className="mm-card products-panel">          <div className="product-search">
            <input className="mm-input"
              type="search"
              value={productSearch}
              onChange={(e) => setProductSearch(e.target.value)}
              placeholder="Search products"
              aria-label="Search products"
            />
          </div>
          {loading && <div className="mm-loading" role="status">Loading products...</div>}
          <div className="product-grid">
            {filteredProducts.map((product) => (
              <button
                key={product.id}
                type="button"
                className="mm-card product-card"                onClick={() => void addToCart(product)}
                disabled={busy || (!product.non_stock && product.quantity <= 0)}
              >
                <span className="product-name">{product.name}</span>
                <span className="product-meta">
                  {product.quantity} in stock
                  {product.is_low_stock ? ' · low' : ''}
                </span>
                <span className="product-price">{formatMoney(product.price)}</span>
              </button>
            ))}
          </div>
          {!loading && filteredProducts.length === 0 && (
            <div className="mm-empty"><p className="mm-empty-text">No products found.</p></div>
          )}
        </div>

        {/* Cart */}
        <div className="mm-card cart-panel">          <div className="cart-header">
            <h2>Cart</h2>
            <span className="mm-muted">
              {cartLines.reduce((s, l) => s + l.quantity, 0)} items
            </span>
          </div>

          <div className="cart-lines">
            {cartLines.length === 0 && (
              <p className="muted empty-cart">Cart is empty</p>
            )}
            {cartLines.map((line) => (
              <div key={line.id} className="cart-line">
                <div className="cart-line-main">
                  <span className="cart-line-name">{line.product_name}</span>
                  <span className="cart-line-meta">
                    {formatMoney(line.unit_price)} each
                    {toNumber(line.discount_amount) > 0
                      ? ` · disc ${formatMoney(line.discount_amount)}`
                      : ''}
                  </span>
                </div>
                <div className="qty-controls">
                  <button
                    type="button"
                    onClick={() => void updateQty(line, line.quantity - 1)}
                    disabled={busy}
                  >
                    -
                  </button>
                  <span>{line.quantity}</span>
                  <button
                    type="button"
                    onClick={() => void updateQty(line, line.quantity + 1)}
                    disabled={busy}
                  >
                    +
                  </button>
                </div>
                <span className="cart-line-total">
                  {formatMoney(line.line_total)}
                </span>
                <button
                  type="button"
                  className="line-action"
                  onClick={() => openDiscount(line)}
                  disabled={busy}
                  title="Discount"
                >
                  %
                </button>
                <button
                  type="button"
                  className="line-action danger"
                  onClick={() => void updateQty(line, 0)}
                  disabled={busy}
                  title="Remove"
                >
                  ×
                </button>
              </div>
            ))}
          </div>

          <div className="cart-totals">
            <div className="total-row">
              <span>Subtotal</span>
              <span>{formatMoney(subtotal)}</span>
            </div>
            <div className="total-row">
              <span>Discount</span>
              <span>-{formatMoney(discountTotal)}</span>
            </div>
            <div className="total-row grand">
              <span>Total</span>
              <span>{formatMoney(total)}</span>
            </div>
          </div>

          {/* Payment */}
          <div className="payment-section">
            {/*<label className="mm-field">
              <span>Payment method</span>
              <select className="mm-select"
                value={payMethod}
                onChange={(e) => setPayMethod(e.target.value)}
              >
                {METHODS.map((m) => (
                  <option key={m.value} value={m.value}>
                    {m.label}
                  </option>
                ))}
              </select>
            </label>*/}

            <label className="mm-field">
              <span>Customer{/* (for invoice)*/}</span>
              <select className="mm-select"
                value={payCustomer}
                onChange={(e) => setPayCustomer(e.target.value)}
              >
                <option value="">Walk-in customer</option>
                {customers.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name} (owes {formatMoney(c.balance)}, limit{' '}
                    {formatMoney(c.credit_limit)})
                  </option>
                ))}
              </select>
            </label>
            {selectedCustomer && (
              <p className="mm-muted small">
                Available credit: {formatMoney(availableCredit)}
                {availableCredit < 0 ? ' (over limit)' : ''}
              </p>
            )}
            {!isManager && payCustomer && (
              <>
                <label className="mm-field">
                  <span>Manager username</span>
                  <input className="mm-input"
                    type="text"
                    value={payManagerUser}
                    onChange={(e) => setPayManagerUser(e.target.value)}
                    placeholder="Manager approval required for invoice"
                  />
                </label>
                <label className="mm-field">
                  <span>Manager password</span>
                  <input className="mm-input"
                    type="password"
                    value={payManagerPass}
                    onChange={(e) => setPayManagerPass(e.target.value)}
                  />
                </label>
              </>
            )}

            <div className="mm-field">
              <span>Tendered</span>
              <div className="field-row" style={{ gridTemplateColumns: '1fr auto', gap: '0.5rem' }}>
                <input className="mm-input"
                  type="number"
                  step="0.01"
                  min="0.01"
                  value={payTendered}
                  onChange={(e) => setPayTendered(e.target.value)}
                  aria-label="Tendered"
                />
                <button type="button" className="mm-btn mm-btn--ghost" onClick={exactAmount}>
                  Exact amount
                </button>
              </div>
            </div>

            {payMethod === 'cash' && (
              <div className="change-due">
                Change due: {formatMoney(Math.max(0, toNumber(payTendered) - toNumber(sale?.total ?? 0)))}
              </div>
            )}

            <label className="mm-field">
              <span>Sale note</span>
              <textarea
                className="mm-textarea"
                value={saleNote}
                onChange={(e) => setSaleNote(e.target.value)}
                placeholder="Required for zero sales"
              />
            </label>

            {payError && (
              <p className="mm-alert mm-alert--danger" role="alert">
                {payError}
              </p>
            )}

            <div className="payment-actions">
              {['cash', 'card', 'invoice'].map((method) => (
                <button
                  key={method}
                  type="button"
                  className="mm-btn mm-btn--primary"
                  onClick={() => void recordPayment(method)}
                  disabled={busy || paySaving || cartLines.length === 0 || toNumber(payTendered) <= 0}
                >
                  {paySaving ? 'Recording...' : method.charAt(0).toUpperCase() + method.slice(1)}
                </button>
              ))}
            </div>

            {sale?.payments && sale.payments.length > 0 && (
              <div className="recorded-payments">
                <h4>Recorded payments</h4>
                {sale.payments.map((p) => (
                  <div key={p.id} className="recorded-payment">
                    <span>{p.method.charAt(0).toUpperCase() + p.method.slice(1)}</span>
                    <span>
                      {p.method === 'cash' && p.tendered != null
                        ? formatMoney(p.tendered)
                        : formatMoney(p.amount)}
                    </span>
                    {p.method === 'cash' && toNumber(p.change_due) > 0 && (
                      <span className="mm-muted small">
                        change {formatMoney(p.change_due)}
                      </span>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>

          <div className="cart-actions">
            <button
              type="button"
              className="mm-btn mm-btn--success"
              onClick={() => void completeSale()}
              disabled={
                busy ||
                cartLines.length === 0 ||
                (toNumber(sale?.total ?? 0) > 0 &&
                  (sale?.payments ?? []).reduce((sum, payment) => sum + toNumber(payment.amount), 0) <
                    toNumber(sale?.total ?? 0)) ||
                (toNumber(sale?.total ?? 0) === 0 && !saleNote.trim())
              }
            >
              {busy ? 'Processing...' : 'Complete sale'}
            </button>
          </div>
        </div>
      </div>

      {/* Discount modal */}
      {discountLineId !== null && discountLine && (
        <div
          className="mm-modal-backdrop"
          onMouseDown={(e) => {
            if (e.target === e.currentTarget) closeDiscount();
          }}
        >
          <div className="mm-modal" role="dialog" aria-modal="true">
            <header className="mm-modal-header">
              <h2>Line discount</h2>
              <button
                type="button"
                className="mm-modal-close"
                onClick={closeDiscount}
              >
                ×
              </button>
            </header>
            <form className="mm-modal-body" onSubmit={handleDiscountSubmit}>
              <p className="modal-context">
                {discountLine.product_name} — line total{' '}
                {formatMoney(discountLine.unit_price)} ×{' '}
                {discountLine.quantity}
              </p>
              {discountError && (
                <p className="mm-alert mm-alert--danger" role="alert">
                  {discountError}
                </p>
              )}
              <label className="mm-field">
                <span>Type</span>
                <select className="mm-select"
                  value={discountType}
                  onChange={(e) => setDiscountType(e.target.value)}
                >
                  <option value="percent">Percent</option>
                  <option value="fixed">Fixed amount</option>
                  <option value="none">None (clear)</option>
                </select>
              </label>
              <label className="mm-field">
                <span>Value</span>
                <input className="mm-input"
                  type="number"
                  step="0.01"
                  min="0"
                  value={discountValue}
                  onChange={(e) => setDiscountValue(e.target.value)}
                  autoFocus
                />
              </label>
              {!isManager && (
                <>
                  <label className="mm-field">
                    <span>Manager username</span>
                    <input className="mm-input"
                      type="text"
                      value={discountManagerUser}
                      onChange={(e) => setDiscountManagerUser(e.target.value)}
                      placeholder="Required for cashier"
                    />
                  </label>
                  <label className="mm-field">
                    <span>Manager password</span>
                    <input className="mm-input"
                      type="password"
                      value={discountManagerPass}
                      onChange={(e) => setDiscountManagerPass(e.target.value)}
                    />
                  </label>
                </>
              )}
              <footer className="mm-modal-footer">
                <button
                  type="button"
                  className="mm-btn mm-btn--ghost"
                  onClick={closeDiscount}
                  disabled={discountSaving}
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  className="mm-btn mm-btn--primary"
                  disabled={discountSaving}
                >
                  {discountSaving ? 'Applying...' : 'Apply discount'}
                </button>
              </footer>
            </form>
          </div>
        </div>
      )}

      {/* Receipt modal */}
      {receiptSale && (
        <div className="mm-modal-backdrop">
          <div className="mm-modal receipt-modal" role="dialog" aria-modal="true">
            <header className="mm-modal-header">
              <h2>Receipt</h2>
              <button type="button" className="mm-modal-close" onClick={closeReceipt}>
                ×
              </button>
            </header>
            <pre className="receipt-text">
              {(() => {
                const s = receiptSale;
                const lines = s.lines
                  .map(
                    (l) =>
                      `${l.product_name.padEnd(20)} ${String(l.quantity).padStart(2)}  ${formatMoney(l.line_total).padStart(8)}`,
                  )
                  .join('\n');
                const payLines = s.payments
                  .map(
                    (p) =>
                      `${p.method.toUpperCase()} ${formatMoney(p.amount)}${
                        p.method === 'cash'
                          ? ` (tendered ${formatMoney(p.tendered ?? 0)}, change ${formatMoney(p.change_due)})`
                          : ''
                      }`,
                  )
                  .join('\n');
                return `${business?.company_name || 'Store'}\n${business?.address || ''}\n${business?.phone || ''} ${business?.email || ''}\n${business?.website || ''}\n${'='.repeat(34)}\n${new Date(s.completed_at ?? s.created_at ?? '').toLocaleString(undefined, { hour12: false })}\nCashier: user #${s.cashier}   Sale #${s.number}\n${'='.repeat(34)}\n${lines}\n${'='.repeat(34)}\nSubtotal: ${formatMoney(s.subtotal).padStart(22)}\nDiscount: -${formatMoney(s.discount_total).padStart(21)}\nTOTAL:    ${formatMoney(s.total).padStart(22)}\n${'='.repeat(34)}\n${s.note ? `Note: ${s.note}\n` : ''}${payLines}\n${'='.repeat(34)}\nThank you for your visit!\n`;
              })()}
            </pre>
            <footer className="mm-modal-footer">
              <button type="button" className="mm-btn mm-btn--primary" onClick={closeReceipt}>
                Close
              </button>
            </footer>
          </div>
        </div>
      )}

      {changeModalOpen && (
        <div className="mm-modal-backdrop" onMouseDown={(e) => { if (e.target === e.currentTarget) closeChange(); }}>
          <div className="mm-modal" role="dialog" aria-modal="true">
            <header className="mm-modal-header">
              <h2>Change due</h2>
              <button type="button" className="mm-modal-close" onClick={closeChange}>×</button>
            </header>
            <div className="mm-modal-body">
              <p className="mm-muted">Change to return to customer:</p>
              <p className="mm-page-title" style={{ fontSize: '5rem' }}>{formatMoney(changeDue)}</p>
            </div>
            <footer className="mm-modal-footer">
              <button type="button" className="mm-btn mm-btn--primary" onClick={closeChange}>Close</button>
            </footer>
          </div>
        </div>
      )}
    </section>
  );
}
