import {
  useCallback,
  useEffect,
  useMemo,
  useState,
  type FormEvent,
} from 'react';
import { apiFetch, ApiError } from './api';
import './CustomersPage.css';

interface Customer {
  id: number;
  name: string;
  phone: string;
  balance: string;
  credit_limit: string;
  notes: string;
  created_at: string;
  updated_at: string;
}

interface SaleTransaction {
  id: number;
  number: number;
  total: string;
  status: string;
  created_at: string;
}

interface PayOnAccountTransaction {
  id: number;
  customer: number;
  amount: string;
  note: string;
  created_at: string;
}

interface CustomerTransactions {
  sales: SaleTransaction[];
  pay_on_account: PayOnAccountTransaction[];
}

interface CustomerForm {
  name: string;
  phone: string;
  balance: string;
  credit_limit: string;
  notes: string;
}

interface TransactionRow {
  key: string;
  created_at: string;
  type: 'Sale' | 'Payment';
  reference: string;
  amount: number;
  status: string;
}

const EMPTY_FORM: CustomerForm = {
  name: '',
  phone: '',
  balance: '0',
  credit_limit: '0',
  notes: '',
};

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

function formatDate(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString(undefined, { hour12: false });
}

function normalizeList(
  data: Customer[] | { results: Customer[] },
): Customer[] {
  return Array.isArray(data) ? data : data.results;
}

function CustomersPage() {
  const [customers, setCustomers] = useState<Customer[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [listLoading, setListLoading] = useState(true);
  const [listError, setListError] = useState('');
  const [search, setSearch] = useState('');

  const [txnData, setTxnData] = useState<{
    customerId: number;
    data: CustomerTransactions;
  } | null>(null);
  const [txnError, setTxnError] = useState<{
    customerId: number;
    message: string;
  } | null>(null);
  const [txnVersion, setTxnVersion] = useState(0);

  const [formMode, setFormMode] = useState<'closed' | 'create' | 'edit'>(
    'closed',
  );
  const [form, setForm] = useState<CustomerForm>(EMPTY_FORM);
  const [formError, setFormError] = useState('');
  const [formSaving, setFormSaving] = useState(false);

  const [payOpen, setPayOpen] = useState(false);
  const [payAmount, setPayAmount] = useState('');
  const [payNote, setPayNote] = useState('');
  const [payError, setPayError] = useState('');
  const [paySaving, setPaySaving] = useState(false);
  const [deleting, setDeleting] = useState(false);

  const loadCustomers = useCallback(async (preferredId?: number) => {
    try {
      const data = await apiFetch<Customer[] | { results: Customer[] }>(
        '/api/customers/',
      );
      const list = normalizeList(data);
      setCustomers(list);
      setListError('');
      setSelectedId((current) => {
        const target = preferredId ?? current;
        if (target !== null && list.some((c) => c.id === target)) {
          return target;
        }
        return list[0]?.id ?? null;
      });
    } catch (err) {
      setListError(
        err instanceof ApiError ? err.message : 'Failed to load customers',
      );
    } finally {
      setListLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadCustomers();
  }, [loadCustomers]);

  useEffect(() => {
    if (selectedId === null) {
      return;
    }

    let active = true;

    apiFetch<CustomerTransactions>(
      `/api/customers/${selectedId}/transactions/`,
    )
      .then((data) => {
        if (!active) {
          return;
        }
        setTxnData({ customerId: selectedId, data });
        setTxnError(null);
      })
      .catch((err) => {
        if (!active) {
          return;
        }
        setTxnError({
          customerId: selectedId,
          message:
            err instanceof ApiError
              ? err.message
              : 'Failed to load transactions',
        });
      });

    return () => {
      active = false;
    };
  }, [selectedId, txnVersion]);

  useEffect(() => {
    if (formMode === 'closed' && !payOpen) {
      return;
    }

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setFormMode('closed');
        setPayOpen(false);
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [formMode, payOpen]);

  const filteredCustomers = useMemo(() => {
    const query = search.trim().toLowerCase();
    if (!query) {
      return customers;
    }
    return customers.filter(
      (customer) =>
        customer.name.toLowerCase().includes(query) ||
        customer.phone.toLowerCase().includes(query),
    );
  }, [customers, search]);

  const selected = useMemo(
    () => customers.find((customer) => customer.id === selectedId) ?? null,
    [customers, selectedId],
  );

  const selectedTransactions =
    txnData && txnData.customerId === selectedId ? txnData.data : null;
  const selectedTxnError =
    txnError && txnError.customerId === selectedId ? txnError.message : '';
  const txnLoading =
    selectedId !== null && !selectedTransactions && !selectedTxnError;

  const balance = selected ? toNumber(selected.balance) : 0;
  const creditLimit = selected ? toNumber(selected.credit_limit) : 0;
  const availableCredit = creditLimit - balance;
  const overLimit = selected !== null && balance > creditLimit;
  const creditUsage =
    creditLimit > 0
      ? Math.min(100, Math.max(0, (balance / creditLimit) * 100))
      : balance > 0
        ? 100
        : 0;

  const transactionRows = useMemo<TransactionRow[]>(() => {
    if (!selectedTransactions) {
      return [];
    }

    const sales: TransactionRow[] = selectedTransactions.sales.map((sale) => ({
      key: `sale-${sale.id}`,
      created_at: sale.created_at,
      type: 'Sale',
      reference: `Sale #${sale.number}`,
      amount: toNumber(sale.total),
      status: sale.status,
    }));

    const payments: TransactionRow[] =
      selectedTransactions.pay_on_account.map((payment) => ({
        key: `payment-${payment.id}`,
        created_at: payment.created_at,
        type: 'Payment',
        reference: payment.note || 'Pay on account',
        amount: -toNumber(payment.amount),
        status: 'applied',
      }));

    return [...sales, ...payments].sort(
      (a, b) =>
        new Date(b.created_at).getTime() - new Date(a.created_at).getTime(),
    );
  }, [selectedTransactions]);

  const openCreate = () => {
    setForm(EMPTY_FORM);
    setFormError('');
    setFormMode('create');
  };

  const openEdit = (customer: Customer) => {
    setForm({
      name: customer.name,
      phone: customer.phone,
      balance: customer.balance,
      credit_limit: customer.credit_limit,
      notes: customer.notes,
    });
    setFormError('');
    setFormMode('edit');
  };

  const closeForm = () => {
    if (formSaving) {
      return;
    }
    setFormMode('closed');
  };

  const handleFormSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();

    const name = form.name.trim();
    if (!name) {
      setFormError('Name is required.');
      return;
    }

    const creditLimitValue = toNumber(form.credit_limit);
    if (creditLimitValue < 0) {
      setFormError('Credit limit cannot be negative.');
      return;
    }

    setFormSaving(true);
    setFormError('');

    const payload = {
      name,
      phone: form.phone.trim(),
      balance: form.balance.trim() || '0',
      credit_limit: form.credit_limit.trim() || '0',
      notes: form.notes,
    };

    try {
      let saved: Customer;
      if (formMode === 'create') {
        saved = await apiFetch<Customer>('/api/customers/', {
          method: 'POST',
          body: JSON.stringify(payload),
        });
      } else {
        if (selectedId === null) {
          setFormError('No customer selected.');
          return;
        }
        saved = await apiFetch<Customer>(`/api/customers/${selectedId}/`, {
          method: 'PATCH',
          body: JSON.stringify(payload),
        });
      }

      setFormMode('closed');
      setTxnData(null);
      setTxnVersion((version) => version + 1);
      await loadCustomers(saved.id);
    } catch (err) {
      setFormError(
        err instanceof ApiError ? err.message : 'Failed to save customer',
      );
    } finally {
      setFormSaving(false);
    }
  };

  const handleDelete = async (customer: Customer) => {
    const confirmed = window.confirm(
      `Delete ${customer.name}? This cannot be undone.`,
    );
    if (!confirmed) {
      return;
    }

    setDeleting(true);
    setListError('');
    try {
      await apiFetch(`/api/customers/${customer.id}/`, { method: 'DELETE' });
      setTxnData(null);
      await loadCustomers();
    } catch (err) {
      setListError(
        err instanceof ApiError ? err.message : 'Failed to delete customer',
      );
    } finally {
      setDeleting(false);
    }
  };

  const openPay = (customer: Customer) => {
    const owed = toNumber(customer.balance);
    setPayAmount(owed > 0 ? owed.toFixed(2) : '');
    setPayNote('');
    setPayError('');
    setPayOpen(true);
  };

  const closePay = () => {
    if (paySaving) {
      return;
    }
    setPayOpen(false);
  };

  const handlePaySubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();

    if (!selected) {
      return;
    }

    const amount = toNumber(payAmount);
    if (amount <= 0) {
      setPayError('Enter an amount greater than zero.');
      return;
    }

    setPaySaving(true);
    setPayError('');

    try {
      const updated = await apiFetch<Customer>(
        `/api/customers/${selected.id}/pay_on_account/`,
        {
          method: 'POST',
          body: JSON.stringify({
            amount: payAmount.trim(),
            note: payNote.trim(),
          }),
        },
      );

      setCustomers((current) =>
        current.map((customer) =>
          customer.id === updated.id ? updated : customer,
        ),
      );
      setPayOpen(false);
      setTxnData(null);
      setTxnVersion((version) => version + 1);
    } catch (err) {
      setPayError(
        err instanceof ApiError ? err.message : 'Failed to record payment',
      );
    } finally {
      setPaySaving(false);
    }
  };

  return (
    <div className="mm-page customers-page">
      <header className="mm-page-header">
        <div>
          <h1 className="mm-page-title">Customers</h1>
          <p className="mm-page-subtitle">
            {customers.length} customer{customers.length === 1 ? '' : 's'} on
            file
          </p>
        </div>
        <button type="button" className="mm-btn mm-btn--primary" onClick={openCreate}>
          + New customer
        </button>
      </header>

      <div className="customers-layout">
        <aside className="mm-card customer-list-panel">
          <div className="customer-search">
            <input
              type="search"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              className="mm-input"
              placeholder="Search by name or phone"
              aria-label="Search customers"
            />
          </div>

          <div className="customer-list" role="listbox" aria-label="Customers">
            {listLoading && (
              <p className="customer-list-message">Loading customers...</p>
            )}
            {listError && (
              <p className="customer-list-message error" role="alert">
                {listError}
              </p>
            )}
            {!listLoading && !listError && filteredCustomers.length === 0 && (
              <p className="customer-list-message">
                {customers.length === 0
                  ? 'No customers yet. Create the first one.'
                  : 'No customers match your search.'}
              </p>
            )}

            {filteredCustomers.map((customer) => {
              const owed = toNumber(customer.balance);
              return (
                <button
                  key={customer.id}
                  type="button"
                  role="option"
                  aria-selected={customer.id === selectedId}
                  className={`customer-row${customer.id === selectedId ? ' selected' : ''}`}
                  onClick={() => setSelectedId(customer.id)}
                >
                  <span className="customer-row-main">
                    <span className="customer-row-name">{customer.name}</span>
                    <span className="customer-row-phone">
                      {customer.phone || 'No phone'}
                    </span>
                  </span>
                  <span
                    className={`customer-row-balance ${owed > 0 ? 'owed' : 'clear'}`}
                  >
                    {formatMoney(customer.balance)}
                  </span>
                </button>
              );
            })}
          </div>
        </aside>

        <section className="mm-card customer-detail-panel" aria-live="polite">
          {!selected ? (
            <div className="customer-empty">
              <p>Select a customer to view their account.</p>
            </div>
          ) : (
            <>
              <header className="customer-detail-header">
                <div>
                  <h2>{selected.name}</h2>
                  <p className="customer-detail-phone">
                    {selected.phone || 'No phone number'}
                  </p>
                </div>
                <div className="customer-detail-buttons">
                  <button
                    type="button"
                    className="mm-btn mm-btn--ghost"
                    onClick={() => openEdit(selected)}
                  >
                    Edit
                  </button>
                  <button
                    type="button"
                    className="mm-btn mm-btn--danger"
                    onClick={() => void handleDelete(selected)}
                    disabled={deleting}
                  >
                    {deleting ? 'Deleting...' : 'Delete'}
                  </button>
                </div>
              </header>

              <div className="customer-stats">
                <div className="customer-stat">
                  <span className="customer-stat-label">Balance owed</span>
                  <span
                    className={`customer-stat-value ${balance > 0 ? 'owed' : 'clear'}`}
                  >
                    {formatMoney(selected.balance)}
                  </span>
                  {balance < 0 && (
                    <span className="customer-stat-note">
                      In credit by {formatMoney(Math.abs(balance))}
                    </span>
                  )}
                </div>

                <div className="customer-stat">
                  <span className="customer-stat-label">Credit limit</span>
                  <span className="customer-stat-value">
                    {formatMoney(selected.credit_limit)}
                  </span>
                  <span className="customer-stat-note">
                    {creditLimit === 0
                      ? 'No credit allowed'
                      : `${Math.round(creditUsage)}% used`}
                  </span>
                  <span className="credit-meter" aria-hidden="true">
                    <span
                      className={`credit-meter-fill${overLimit ? ' over' : ''}`}
                      style={{ width: `${creditUsage}%` }}
                    />
                  </span>
                </div>

                <div className="customer-stat">
                  <span className="customer-stat-label">Available credit</span>
                  <span
                    className={`customer-stat-value ${availableCredit < 0 ? 'owed' : 'clear'}`}
                  >
                    {formatMoney(availableCredit)}
                  </span>
                  <span className="customer-stat-note">
                    {availableCredit < 0
                      ? 'Over limit'
                      : 'Remaining before limit'}
                  </span>
                </div>
              </div>

              {overLimit && (
                <p className="notice notice-warning" role="status">
                  Over the credit limit by{' '}
                  {formatMoney(Math.abs(availableCredit))}. A manager must
                  approve any further charge to this account.
                </p>
              )}

              {selected.notes && (
                <div className="customer-notes">
                  <span className="customer-notes-label">Notes</span>
                  <p>{selected.notes}</p>
                </div>
              )}

              <div className="customer-actions">
                <button
                  type="button"
                  className="mm-btn mm-btn--success"
                  onClick={() => openPay(selected)}
                >
                  Pay on account
                </button>
                {/*<button
                  type="button"
                  className="mm-btn mm-btn--primary"
                  onClick={() =>
                    navigate(`/sales?customer=${selected.id}`)
                  }
                >
                  Charge to account
                </button>*/}
              </div>

              <section className="transactions">
                <div className="transactions-heading">
                  <h3>Transactions</h3>
                  {txnLoading && <span className="muted">Loading...</span>}
                </div>

                {selectedTxnError && (
                  <p className="error" role="alert">
                    {selectedTxnError}
                  </p>
                )}

                {!txnLoading &&
                  !selectedTxnError &&
                  transactionRows.length === 0 && (
                    <p className="muted empty-transactions">
                      No transactions for this customer yet.
                    </p>
                  )}

                {transactionRows.length > 0 && (
                  <div className="table-wrap">
                    <table>
                      <thead>
                        <tr>
                          <th>Date</th>
                          <th>Type</th>
                          <th>Reference</th>
                          <th className="num">Amount</th>
                          <th>Status</th>
                        </tr>
                      </thead>
                      <tbody>
                        {transactionRows.map((row) => (
                          <tr key={row.key}>
                            <td>{formatDate(row.created_at)}</td>
                            <td>{row.type}</td>
                            <td>{row.reference}</td>
                            <td
                              className={`num ${row.amount < 0 ? 'payment' : 'charge'}`}
                            >
                              {row.amount < 0
                                ? `-${formatMoney(Math.abs(row.amount))}`
                                : formatMoney(row.amount)}
                            </td>
                            <td>
                              <span className={`status status-${row.status}`}>
                                {row.status}
                              </span>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </section>
            </>
          )}
        </section>
      </div>

      {formMode !== 'closed' && (
        <div
          className="mm-modal-backdrop"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) {
              closeForm();
            }
          }}
        >
          <div
            className="mm-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="customer-form-title"
          >
            <header className="mm-modal-header">
              <h2 id="customer-form-title">
                {formMode === 'create' ? 'New customer' : 'Edit customer'}
              </h2>
              <button
                type="button"
                className="mm-modal-close"
                aria-label="Close"
                onClick={closeForm}
              >
                &times;
              </button>
            </header>

            <form className="mm-modal-body" onSubmit={handleFormSubmit}>
              {formError && (
                <p className="error" role="alert">
                  {formError}
                </p>
              )}

              <label className="field">
                <span>Name</span>
                <input
                  type="text"
                  value={form.name}
                  onChange={(event) =>
                    setForm((current) => ({
                      ...current,
                      name: event.target.value,
                    }))
                  }
                  required
                  autoFocus
                  maxLength={200}
                />
              </label>

              <label className="field">
                <span>Phone</span>
                <input
                  type="tel"
                  value={form.phone}
                  onChange={(event) =>
                    setForm((current) => ({
                      ...current,
                      phone: event.target.value,
                    }))
                  }
                  maxLength={50}
                  placeholder="Optional"
                />
              </label>

              <div className="field-row">
                <label className="field">
                  <span>Balance owed</span>
                  <input
                    type="number"
                    step="0.01"
                    value={form.balance}
                    onChange={(event) =>
                      setForm((current) => ({
                        ...current,
                        balance: event.target.value,
                      }))
                    }
                  />
                </label>

                <label className="field">
                  <span>Credit limit</span>
                  <input
                    type="number"
                    min="0"
                    step="0.01"
                    value={form.credit_limit}
                    onChange={(event) =>
                      setForm((current) => ({
                        ...current,
                        credit_limit: event.target.value,
                      }))
                    }
                  />
                </label>
              </div>

              <label className="field">
                <span>Notes</span>
                <textarea
                  rows={3}
                  value={form.notes}
                  onChange={(event) =>
                    setForm((current) => ({
                      ...current,
                      notes: event.target.value,
                    }))
                  }
                  placeholder="Optional"
                />
              </label>

              <footer className="mm-modal-footer">
                <button
                  type="button"
                  className="mm-btn mm-btn--ghost"
                  onClick={closeForm}
                  disabled={formSaving}
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  className="mm-btn mm-btn--primary"
                  disabled={formSaving}
                >
                  {formSaving
                    ? 'Saving...'
                    : formMode === 'create'
                      ? 'Create customer'
                      : 'Save changes'}
                </button>
              </footer>
            </form>
          </div>
        </div>
      )}

      {payOpen && selected && (
        <div
          className="mm-modal-backdrop"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) {
              closePay();
            }
          }}
        >
          <div
            className="mm-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="pay-account-title"
          >
            <header className="mm-modal-header">
              <h2 id="pay-account-title">Pay on account</h2>
              <button
                type="button"
                className="mm-modal-close"
                aria-label="Close"
                onClick={closePay}
              >
                &times;
              </button>
            </header>

            <form className="mm-modal-body" onSubmit={handlePaySubmit}>
              <p className="modal-context">
                Record a payment for <strong>{selected.name}</strong>. Current
                balance: <strong>{formatMoney(selected.balance)}</strong>.
              </p>

              {payError && (
                <p className="error" role="alert">
                  {payError}
                </p>
              )}

              <label className="field">
                <span>Amount</span>
                <input
                  type="number"
                  step="0.01"
                  min="0.01"
                  value={payAmount}
                  onChange={(event) => setPayAmount(event.target.value)}
                  required
                  autoFocus
                />
              </label>

              <label className="field">
                <span>Note</span>
                <input
                  type="text"
                  value={payNote}
                  onChange={(event) => setPayNote(event.target.value)}
                  maxLength={200}
                  placeholder="Optional, e.g. cash received"
                />
              </label>

              <footer className="mm-modal-footer">
                <button
                  type="button"
                  className="mm-btn mm-btn--ghost"
                  onClick={closePay}
                  disabled={paySaving}
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  className="mm-btn mm-btn--success"
                  disabled={paySaving}
                >
                  {paySaving ? 'Recording...' : 'Record payment'}
                </button>
              </footer>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}

export default CustomersPage;
