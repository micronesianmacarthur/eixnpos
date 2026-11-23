import { useEffect, useMemo, useState, type FormEvent } from 'react';
import { useAuth } from './authContext';
import { Icon } from './Icon';
import './SuppliersPage.css';

/**
 * Supplier contact record, matching the backend `Supplier` model's fields.
 * Data here is static / prop-driven for now; real fetching will wire this
 * to `GET /api/suppliers/` in a follow-up.
 */
export interface Supplier {
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

interface SupplierForm {
  name: string;
  contact_name: string;
  email: string;
  phone: string;
  address: string;
  notes: string;
  active: boolean;
}

const EMPTY_FORM: SupplierForm = {
  name: '',
  contact_name: '',
  email: '',
  phone: '',
  address: '',
  notes: '',
  active: true,
};

/** Static sample roster so the page renders without a backend dependency. */
const SAMPLE_SUPPLIERS: Supplier[] = [
  {
    id: 1,
    name: 'Acme Paper Co.',
    contact_name: 'Dana Reyes',
    email: 'dana@acmepaper.test',
    phone: '+1 555 0141',
    address: '1200 Harbor Blvd, San Diego, CA',
    notes: 'Primary packaging and receipt roll vendor.',
    active: true,
    created_at: '2026-05-02T10:00:00Z',
  },
  {
    id: 2,
    name: 'Nordic Beverage Supply',
    contact_name: 'Ingrid Larsen',
    email: 'ingrid@nordicbev.test',
    phone: '+1 555 0188',
    address: '88 Fjord Way, Portland, OR',
    notes: '',
    active: true,
    created_at: '2026-05-14T10:00:00Z',
  },
  {
    id: 3,
    name: 'Sunrise Grocers Distributors',
    contact_name: 'Marcus Webb',
    email: 'marcus@sunrise.test',
    phone: '+1 555 0123',
    address: '450 Alder St, Austin, TX',
    notes: 'Weekly delivery on Mondays.',
    active: true,
    created_at: '2026-06-01T10:00:00Z',
  },
];

function normalizeList<T>(data: T[] | { results: T[] }): T[] {
  return Array.isArray(data) ? data : data.results;
}

interface SuppliersPageProps {
  /** Optional external supplier list. Falls back to a static sample roster. */
  initialSuppliers?: Supplier[];
}

/** Toolbar: search + active filter + action buttons (placeholders for now). */
function SuppliersToolbar({
  search,
  onSearch,
  statusFilter,
  onStatusFilter,
  canManage,
  onNew,
}: {
  search: string;
  onSearch: (value: string) => void;
  statusFilter: string;
  onStatusFilter: (value: string) => void;
  canManage: boolean;
  onNew: () => void;
}) {
  return (
    <div className="suppliers-toolbar">
      <div className="suppliers-field suppliers-search">
        <label className="mm-visually-hidden" htmlFor="suppliers-search">
          Search suppliers
        </label>
        <input
          id="suppliers-search"
          className="mm-input"
          type="search"
          value={search}
          onChange={(e) => onSearch(e.target.value)}
          placeholder="Search by name or contact"
          aria-label="Search suppliers"
        />
      </div>
      <div className="suppliers-filters">
        <div className="suppliers-field">
          <label className="mm-visually-hidden" htmlFor="suppliers-status">
            Filter by supplier status
          </label>
          <select
            id="suppliers-status"
            className="mm-select"
            value={statusFilter}
            onChange={(e) => onStatusFilter(e.target.value)}
            aria-label="Filter by supplier status"
          >
            <option value="All">All suppliers</option>
            <option value="Active">Active</option>
            <option value="Inactive">Inactive</option>
          </select>
        </div>
        {canManage && (
          <button
            type="button"
            className="mm-btn mm-btn--primary"
            onClick={onNew}
          >
            + New supplier
          </button>
        )}
      </div>
    </div>
  );
}

/** Empty-state placeholder for no suppliers or no filtered matches. */
function SuppliersEmpty({ hasSuppliers }: { hasSuppliers: boolean }) {
  return (
    <div className="mm-empty suppliers-empty">
      <span className="mm-icon-tile">
        <Icon name="suppliers" size={22} />
      </span>
      <h2 className="mm-empty-title">
        {hasSuppliers ? 'No matching suppliers' : 'No suppliers yet'}
      </h2>
      <p className="mm-empty-text">
        {hasSuppliers
          ? 'Try a different search term or status filter.'
          : 'Create the first supplier to start building your vendor directory.'}
      </p>
    </div>
  );
}

/** Supplier create/edit modal. Local state only; no API call in this task. */
function SupplierFormModal({
  editing,
  initial,
  saving,
  onClose,
  onSubmit,
}: {
  editing: boolean;
  initial: SupplierForm;
  saving: boolean;
  onClose: () => void;
  onSubmit: (form: SupplierForm) => void;
}) {
  const [form, setForm] = useState<SupplierForm>(initial);
  const [formError, setFormError] = useState('');

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!form.name.trim()) {
      setFormError('Supplier name is required.');
      return;
    }
    onSubmit(form);
  };

  const field = (key: keyof SupplierForm) =>
    (event: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) =>
      setForm((c) => ({ ...c, [key]: event.target.value }));

  return (
    <div
      className="mm-modal-backdrop"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        className="mm-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="supplier-form-title"
      >
        <header className="mm-modal-header">
          <h2 id="supplier-form-title" className="mm-modal-title">
            {editing ? 'Edit supplier' : 'New supplier'}
          </h2>
          <button
            type="button"
            className="mm-modal-close"
            onClick={onClose}
            aria-label="Close"
          >
            <Icon name="x" size={20} />
          </button>
        </header>

        <form className="supplier-form" onSubmit={handleSubmit}>
          <div className="mm-modal-body supplier-body">
            {formError && (
              <div className="mm-alert mm-alert--danger" role="alert">
                {formError}
              </div>
            )}

            <label className="mm-field">
              <span className="mm-label">Name</span>
              <input
                className="mm-input"
                type="text"
                value={form.name}
                onChange={field('name')}
                required
                autoFocus
              />
            </label>

            <div className="supplier-field-row">
              <label className="mm-field">
                <span className="mm-label">Contact name</span>
                <input
                  className="mm-input"
                  type="text"
                  value={form.contact_name}
                  onChange={field('contact_name')}
                />
              </label>
              <label className="mm-field">
                <span className="mm-label">Phone</span>
                <input
                  className="mm-input"
                  type="tel"
                  value={form.phone}
                  onChange={field('phone')}
                />
              </label>
            </div>

            <label className="mm-field">
              <span className="mm-label">Email</span>
              <input
                className="mm-input"
                type="email"
                value={form.email}
                onChange={field('email')}
              />
            </label>

            <label className="mm-field">
              <span className="mm-label">Address</span>
              <input
                className="mm-input"
                type="text"
                value={form.address}
                onChange={field('address')}
              />
            </label>

            <label className="mm-field">
              <span className="mm-label">Notes</span>
              <textarea
                className="mm-textarea"
                rows={3}
                value={form.notes}
                onChange={field('notes')}
                maxLength={500}
              />
            </label>

            <label className="supplier-active-check">
              <input
                type="checkbox"
                checked={form.active}
                onChange={(e) =>
                  setForm((c) => ({ ...c, active: e.target.checked }))
                }
              />
              <span>Active supplier</span>
            </label>
          </div>

          <footer className="mm-modal-footer">
            <button
              type="button"
              className="mm-btn mm-btn--ghost"
              onClick={onClose}
              disabled={saving}
            >
              Cancel
            </button>
            <button
              type="submit"
              className="mm-btn mm-btn--primary"
              disabled={saving}
            >
              {saving ? 'Saving...' : editing ? 'Save changes' : 'Create supplier'}
            </button>
          </footer>
        </form>
      </div>
    </div>
  );
}

export default function SuppliersPage({ initialSuppliers }: SuppliersPageProps) {
  const { me } = useAuth();
  const canManage =
    me !== null && (me.user.role === 'manager' || me.user.role === 'admin');
  const [suppliers, setSuppliers] = useState<Supplier[]>(
    normalizeList(initialSuppliers ?? SAMPLE_SUPPLIERS),
  );
  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState('All');

  const [modalOpen, setModalOpen] = useState(false);
  const [editingSupplier, setEditingSupplier] = useState<Supplier | null>(null);
  const [form, setForm] = useState<SupplierForm>(EMPTY_FORM);
  const [saving, setSaving] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<Supplier | null>(null);

  const filteredSuppliers = useMemo(() => {
    const query = search.trim().toLowerCase();
    return suppliers.filter((s) => {
      const matchesSearch =
        !query ||
        s.name.toLowerCase().includes(query) ||
        (s.contact_name && s.contact_name.toLowerCase().includes(query));
      const matchesStatus =
        statusFilter === 'All' ||
        (statusFilter === 'Active' ? s.active : !s.active);
      return matchesSearch && matchesStatus;
    });
  }, [suppliers, search, statusFilter]);

  const activeCount = useMemo(
    () => suppliers.filter((s) => s.active).length,
    [suppliers],
  );
  const inactiveCount = suppliers.length - activeCount;

  useEffect(() => {
    if (modalOpen || deleteTarget) {
      const handleKeyDown = (event: KeyboardEvent) => {
        if (event.key === 'Escape') {
          setModalOpen(false);
          setEditingSupplier(null);
          setDeleteTarget(null);
        }
      };
      window.addEventListener('keydown', handleKeyDown);
      return () => window.removeEventListener('keydown', handleKeyDown);
    }
  }, [modalOpen, deleteTarget]);

  const openCreate = () => {
    setEditingSupplier(null);
    setForm(EMPTY_FORM);
    setModalOpen(true);
  };

  const openEdit = (supplier: Supplier) => {
    setEditingSupplier(supplier);
    setForm({
      name: supplier.name,
      contact_name: supplier.contact_name,
      email: supplier.email,
      phone: supplier.phone,
      address: supplier.address,
      notes: supplier.notes,
      active: supplier.active,
    });
    setModalOpen(true);
  };

  const closeModal = () => {
    if (saving) return;
    setModalOpen(false);
    setEditingSupplier(null);
  };

  /** Local-state placeholder mutation. Real persistence arrives with fetching. */
  const handleSubmit = (next: SupplierForm) => {
    setSaving(true);
    // Simulate a short save so the disabled/saving state is exercised.
    window.setTimeout(() => {
      if (editingSupplier) {
        setSuppliers((list) =>
          list.map((s) => (s.id === editingSupplier.id ? { ...s, ...next } : s)),
        );
      } else {
        setSuppliers((list) => [
          ...list,
          {
            id: Math.max(0, ...list.map((s) => s.id)) + 1,
            created_at: new Date().toISOString(),
            ...next,
          },
        ]);
      }
      setSaving(false);
      setModalOpen(false);
      setEditingSupplier(null);
    }, 350);
  };

  const confirmDelete = () => {
    if (!deleteTarget) return;
    setSuppliers((list) => list.filter((s) => s.id !== deleteTarget.id));
    setDeleteTarget(null);
  };

  return (
    <div className="mm-page suppliers-page">
      <header className="mm-page-header">
        <div>
          <h1 className="mm-page-title">Suppliers</h1>
          <p className="mm-page-subtitle">
            {suppliers.length} supplier{suppliers.length === 1 ? '' : 's'} on
            file · {activeCount} active · {inactiveCount} inactive
          </p>
        </div>
      </header>

      <section className="mm-card suppliers-panel" aria-label="Suppliers">
        <SuppliersToolbar
          search={search}
          onSearch={setSearch}
          statusFilter={statusFilter}
          onStatusFilter={setStatusFilter}
          canManage={canManage}
          onNew={openCreate}
        />

        {filteredSuppliers.length === 0 ? (
          <SuppliersEmpty hasSuppliers={suppliers.length > 0} />
        ) : (
          <div className="mm-table-wrap suppliers-table-wrap">
            <table className="mm-table suppliers-table">
              <thead>
                <tr>
                  <th className="col-id">
                    <span className="mm-visually-hidden">ID</span>
                  </th>
                  <th>Name</th>
                  <th>Contact</th>
                  <th>Phone</th>
                  <th>Status</th>
                  {canManage && (
                    <th>
                      <span className="mm-visually-hidden">Actions</span>
                    </th>
                  )}
                </tr>
              </thead>
              <tbody>
                {filteredSuppliers.map((supplier) => (
                  <tr key={supplier.id} className="suppliers-row">
                    <td className="col-id">{supplier.id}</td>
                    <td className="col-name">
                      <span className="supplier-name">{supplier.name}</span>
                      {supplier.email && (
                        <span className="supplier-email">{supplier.email}</span>
                      )}
                    </td>
                    <td className="col-contact">
                      {supplier.contact_name || <span className="mm-muted">—</span>}
                    </td>
                    <td className="col-phone">
                      {supplier.phone || <span className="mm-muted">—</span>}
                    </td>
                    <td className="col-status">
                      <span
                        className={
                          supplier.active
                            ? 'mm-badge mm-badge--success'
                            : 'mm-badge mm-badge--muted'
                        }
                      >
                        {supplier.active ? 'Active' : 'Inactive'}
                      </span>
                    </td>
                    {canManage && (
                      <td className="col-actions">
                        <button
                          type="button"
                          className="mm-btn mm-btn--outline mm-btn--sm"
                          onClick={() => openEdit(supplier)}
                          aria-label={`Edit ${supplier.name}`}
                          title="Edit supplier"
                        >
                          Edit
                        </button>
                        <button
                          type="button"
                          className="mm-btn mm-btn--danger mm-btn--sm"
                          onClick={() => setDeleteTarget(supplier)}
                          aria-label={`Delete ${supplier.name}`}
                          title="Delete supplier"
                        >
                          Delete
                        </button>
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {!canManage && (
          <p className="suppliers-manager-hint mm-muted">
            Supplier management actions are available to managers and admins.
          </p>
        )}
      </section>

      {modalOpen && (
        <SupplierFormModal
          editing={editingSupplier !== null}
          initial={form}
          saving={saving}
          onClose={closeModal}
          onSubmit={handleSubmit}
        />
      )}

      {deleteTarget && (
        <div
          className="mm-modal-backdrop"
          onMouseDown={(e) => {
            if (e.target === e.currentTarget) setDeleteTarget(null);
          }}
        >
          <div
            className="mm-modal"
            role="alertdialog"
            aria-modal="true"
            aria-labelledby="supplier-delete-title"
            aria-describedby="supplier-delete-desc"
          >
            <header className="mm-modal-header">
              <h2 id="supplier-delete-title" className="mm-modal-title">
                Delete supplier
              </h2>
              <button
                type="button"
                className="mm-modal-close"
                onClick={() => setDeleteTarget(null)}
                aria-label="Close"
              >
                <Icon name="x" size={20} />
              </button>
            </header>
            <div className="mm-modal-body">
              <p id="supplier-delete-desc">
                Delete <strong>{deleteTarget.name}</strong>? Linked products keep
                existing but no longer reference this supplier.
              </p>
            </div>
            <footer className="mm-modal-footer">
              <button
                type="button"
                className="mm-btn mm-btn--ghost"
                onClick={() => setDeleteTarget(null)}
              >
                Cancel
              </button>
              <button
                type="button"
                className="mm-btn mm-btn--danger"
                onClick={confirmDelete}
              >
                Delete supplier
              </button>
            </footer>
          </div>
        </div>
      )}
    </div>
  );
}