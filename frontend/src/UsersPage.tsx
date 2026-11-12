import {
  useCallback,
  useEffect,
  useMemo,
  useState,
  type FormEvent,
} from 'react';
import { apiFetch, ApiError } from './api';
import { useAuth } from './authContext';
import './UsersPage.css';

interface User {
  id: number;
  email: string;
  name: string;
  role: string;
  createdAt: string;
  updatedAt: string;
}

interface UserForm {
  username: string;
  email: string;
  first_name: string;
  last_name: string;
  role: string;
  password: string;
}

const EMPTY_FORM: UserForm = {
  username: '',
  email: '',
  first_name: '',
  last_name: '',
  role: 'cashier',
  password: '',
};

const ROLES = ['cashier', 'manager', 'admin'] as const;

function formatDate(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

function normalizeList<T>(data: T[] | { results: T[] }): T[] {
  return Array.isArray(data) ? data : data.results;
}

export default function UsersPage() {
  const { me } = useAuth();
  const currentUserId = me?.user.id ?? null;

  const [users, setUsers] = useState<User[]>([]);
  const [search, setSearch] = useState('');
  const [roleFilter, setRoleFilter] = useState('All');
  const [listLoading, setListLoading] = useState(true);
  const [listError, setListError] = useState('');

  const [formMode, setFormMode] = useState<'closed' | 'create' | 'edit'>('closed');
  const [form, setForm] = useState<UserForm>(EMPTY_FORM);
  const [formError, setFormError] = useState('');
  const [formSaving, setFormSaving] = useState(false);
  const [deletingId, setDeletingId] = useState<number | null>(null);
  const [selectedUser, setSelectedUser] = useState<User | null>(null);

  const loadUsers = useCallback(async () => {
    try {
      const data = await apiFetch<User[] | { results: User[] }>('/api/users/');
      const list = normalizeList(data);
      setUsers(list);
      setListError('');
    } catch (err) {
      setListError(
        err instanceof ApiError ? err.message : 'Failed to load users',
      );
    } finally {
      setListLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadUsers();
  }, [loadUsers]);

  useEffect(() => {
    if (formMode === 'closed') {
      return;
    }

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setFormMode('closed');
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [formMode]);

  const filteredUsers = useMemo(() => {
    const query = search.trim().toLowerCase();
    return users.filter((user) => {
      const matchesSearch =
        !query ||
        user.name.toLowerCase().includes(query) ||
        user.email.toLowerCase().includes(query) ||
        user.role.toLowerCase().includes(query);
      const matchesRole = roleFilter === 'All' || user.role === roleFilter;
      return matchesSearch && matchesRole;
    });
  }, [users, search, roleFilter]);

  const roles = useMemo(() => ['All', ...ROLES], []);

  const openCreate = () => {
    setForm({ ...EMPTY_FORM, password: '' });
    setFormError('');
    setFormMode('create');
  };

  const closeForm = () => {
    if (formSaving) {
      return;
    }
    setFormMode('closed');
    setSelectedUser(null);
  };

  const openEditWithUser = (user: User) => {
    setSelectedUser(user);
    // Split name into first/last for the form - approximate since backend only returns combined name
    const nameParts = user.name.trim().split(' ');
    setForm({
      username: nameParts[0] || '',
      email: user.email,
      first_name: nameParts[0] || '',
      last_name: nameParts.slice(1).join(' ') || '',
      role: user.role,
      password: '',
    });
    setFormError('');
    setFormMode('edit');
  };

  const handleDelete = async (user: User) => {
    if (user.id === currentUserId) {
      setListError('You cannot delete your own account.');
      return;
    }

    const confirmed = window.confirm(
      `Delete ${user.name} (${user.email})? This cannot be undone.`,
    );
    if (!confirmed) {
      return;
    }

    setDeletingId(user.id);
    setListError('');
    try {
      await apiFetch(`/api/users/${user.id}/`, { method: 'DELETE' });
      await loadUsers();
    } catch (err) {
      setListError(
        err instanceof ApiError ? err.message : 'Failed to delete user',
      );
    } finally {
      setDeletingId(null);
    }
  };

  const handleFormSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();

    const username = form.username.trim();
    const email = form.email.trim();
    const firstName = form.first_name.trim();
    const lastName = form.last_name.trim();
    const role = form.role;
    const password = form.password;

    if (!username) {
      setFormError('Username is required.');
      return;
    }
    if (!email) {
      setFormError('Email is required.');
      return;
    }
    if (!firstName && !lastName) {
      setFormError('At least first name or last name is required.');
      return;
    }
    if (!ROLES.includes(role as (typeof ROLES)[number])) {
      setFormError('Invalid role selected.');
      return;
    }
    if (formMode === 'create' && !password) {
      setFormError('Password is required for new users.');
      return;
    }
    if (password && password.length < 8) {
      setFormError('Password must be at least 8 characters.');
      return;
    }

    setFormSaving(true);
    setFormError('');

    const payload: Record<string, string> = {
      username,
      email,
      first_name: firstName,
      last_name: lastName,
      role,
    };
    if (password) {
      payload.password = password;
    }

    try {
      if (formMode === 'create') {
        await apiFetch<User>('/api/users/', {
          method: 'POST',
          body: JSON.stringify(payload),
        });
      } else if (selectedUser) {
        await apiFetch<User>(`/api/users/${selectedUser.id}/`, {
          method: 'PATCH',
          body: JSON.stringify(payload),
        });
      } else {
        setFormError('No user selected for editing.');
        return;
      }

      setFormMode('closed');
      setSelectedUser(null);
      await loadUsers();
    } catch (err) {
      setFormError(
        err instanceof ApiError ? err.message : 'Failed to save user',
      );
    } finally {
      setFormSaving(false);
    }
  };

  return (
    <section className="users-page">
      <header className="users-heading">
        <div>
          <h1>Users</h1>
          <p className="page-subtitle">
            {users.length} user{users.length === 1 ? '' : 's'} on file
          </p>
        </div>
        <button type="button" className="btn btn-primary" onClick={openCreate}>
          + New user
        </button>
      </header>

      <div className="users-layout">
        <aside className="user-list-panel">
          <div className="user-search">
            <input
              type="search"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder="Search by name, email, or role"
              aria-label="Search users"
            />
          </div>
          <div className="user-filter">
            <select
              value={roleFilter}
              onChange={(event) => setRoleFilter(event.target.value)}
              aria-label="Filter by role"
            >
              {roles.map((role) => (
                <option key={role} value={role}>
                  {role}
                </option>
              ))}
            </select>
          </div>

          <div className="user-list" role="listbox" aria-label="Users">
            {listLoading && (
              <p className="user-list-message">Loading users...</p>
            )}
            {listError && (
              <p className="user-list-message error" role="alert">
                {listError}
              </p>
            )}
            {!listLoading && !listError && filteredUsers.length === 0 && (
              <p className="user-list-message">
                {users.length === 0
                  ? 'No users yet. Create the first one.'
                  : 'No users match your filters.'}
              </p>
            )}

            {filteredUsers.map((user) => (
              <button
                key={user.id}
                type="button"
                role="option"
                aria-selected={selectedUser?.id === user.id}
                className={`user-row${selectedUser?.id === user.id ? ' selected' : ''}`}
                onClick={() => setSelectedUser(user)}
              >
                <span className="user-row-main">
                  <span className="user-row-name">{user.name}</span>
                  <span className="user-row-email">{user.email}</span>
                </span>
                <span className={`user-row-role role-${user.role}`}>
                  {user.role}
                </span>
              </button>
            ))}
          </div>
        </aside>

        <section className="user-detail-panel" aria-live="polite">
          {!selectedUser ? (
            <div className="user-empty">
              <p>Select a user to view details.</p>
            </div>
          ) : (
            <>
              <header className="user-detail-header">
                <div>
                  <h2>{selectedUser.name}</h2>
                  <p className="user-detail-email">{selectedUser.email}</p>
                </div>
                <div className="user-detail-buttons">
                  <button
                    type="button"
                    className="btn btn-ghost"
                    onClick={() => openEditWithUser(selectedUser)}
                  >
                    Edit
                  </button>
                  <button
                    type="button"
                    className="btn btn-danger-ghost"
                    onClick={() => void handleDelete(selectedUser)}
                    disabled={deletingId === selectedUser.id || selectedUser.id === currentUserId}
                  >
                    {deletingId === selectedUser.id
                      ? 'Deleting...'
                      : selectedUser.id === currentUserId
                      ? 'Cannot delete self'
                      : 'Delete'}
                  </button>
                </div>
              </header>

              <div className="user-stats">
                <div className="user-stat">
                  <span className="user-stat-label">Role</span>
                  <span className={`user-stat-value role-${selectedUser.role}`}>
                    {selectedUser.role}
                  </span>
                </div>

                <div className="user-stat">
                  <span className="user-stat-label">Created</span>
                  <span className="user-stat-value">
                    {formatDate(selectedUser.createdAt)}
                  </span>
                </div>

                <div className="user-stat">
                  <span className="user-stat-label">Updated</span>
                  <span className="user-stat-value">
                    {formatDate(selectedUser.updatedAt)}
                  </span>
                </div>
              </div>
            </>
          )}
        </section>
      </div>

      {formMode !== 'closed' && (
        <div
          className="modal-backdrop"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) {
              closeForm();
            }
          }}
        >
          <div
            className="modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="user-form-title"
          >
            <header className="modal-header">
              <h2 id="user-form-title">
                {formMode === 'create' ? 'New user' : 'Edit user'}
              </h2>
              <button
                type="button"
                className="icon-button"
                aria-label="Close"
                onClick={closeForm}
              >
                &times;
              </button>
            </header>

            <form className="modal-form" onSubmit={handleFormSubmit}>
              {formError && (
                <p className="error" role="alert">
                  {formError}
                </p>
              )}

              <label className="field">
                <span>Username</span>
                <input
                  type="text"
                  value={form.username}
                  onChange={(event) =>
                    setForm((current) => ({
                      ...current,
                      username: event.target.value,
                    }))
                  }
                  required
                  autoFocus
                  maxLength={150}
                  disabled={formMode === 'edit'}
                />
                {formMode === 'edit' && (
                  <span className="field-hint">Username cannot be changed</span>
                )}
              </label>

              <label className="field">
                <span>Email</span>
                <input
                  type="email"
                  value={form.email}
                  onChange={(event) =>
                    setForm((current) => ({
                      ...current,
                      email: event.target.value,
                    }))
                  }
                  required
                  maxLength={254}
                />
              </label>

              <div className="field-row">
                <label className="field">
                  <span>First name</span>
                  <input
                    type="text"
                    value={form.first_name}
                    onChange={(event) =>
                      setForm((current) => ({
                        ...current,
                        first_name: event.target.value,
                      }))
                    }
                    required
                    maxLength={150}
                  />
                </label>

                <label className="field">
                  <span>Last name</span>
                  <input
                    type="text"
                    value={form.last_name}
                    onChange={(event) =>
                      setForm((current) => ({
                        ...current,
                        last_name: event.target.value,
                      }))
                    }
                    maxLength={150}
                    placeholder="Optional"
                  />
                </label>
              </div>

              <label className="field">
                <span>Role</span>
                <select
                  value={form.role}
                  onChange={(event) =>
                    setForm((current) => ({
                      ...current,
                      role: event.target.value,
                    }))
                  }
                  required
                >
                  {ROLES.map((role) => (
                    <option key={role} value={role}>
                      {role.charAt(0).toUpperCase() + role.slice(1)}
                    </option>
                  ))}
                </select>
              </label>

              <label className="field">
                <span>Password</span>
                <input
                  type="password"
                  value={form.password}
                  onChange={(event) =>
                    setForm((current) => ({
                      ...current,
                      password: event.target.value,
                    }))
                  }
                  minLength={8}
                  maxLength={128}
                  placeholder={
                    formMode === 'edit'
                      ? 'Leave blank to keep current password'
                      : 'At least 8 characters'
                  }
                  required={formMode === 'create'}
                />
                {formMode === 'edit' && (
                  <span className="field-hint">
                    Leave blank to keep current password
                  </span>
                )}
              </label>

              <footer className="modal-actions">
                <button
                  type="button"
                  className="btn btn-ghost"
                  onClick={closeForm}
                  disabled={formSaving}
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  className="btn btn-primary"
                  disabled={formSaving}
                >
                  {formSaving
                    ? 'Saving...'
                    : formMode === 'create'
                    ? 'Create user'
                    : 'Save changes'}
                </button>
              </footer>
            </form>
          </div>
        </div>
      )}
    </section>
  );
}