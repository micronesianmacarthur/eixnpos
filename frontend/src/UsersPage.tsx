import {
  useCallback,
  useEffect,
  useMemo,
  useState,
} from 'react';
import { apiFetch, ApiError } from './api';
import { useAuth } from './authContext';
import UserFormModal from './UserFormModal';
import { USER_ROLES, type User } from './users';
import './UsersPage.css';

function formatDate(value: string | undefined): string {
  if (!value) return '';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString(undefined, { hour12: false });
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

  const roles = useMemo(() => ['All', ...USER_ROLES], []);

  const openCreate = () => {
    setSelectedUser(null);
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

  const handleUserSubmit = async (payload: Record<string, string>) => {
    setFormSaving(true);
    setFormError('');
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
        setFormSaving(false);
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
    <section className="mm-page users-page">
      <header className="mm-page-header users-heading">
        <div>
          <h1 className="mm-page-title">Users</h1>
          <p className="mm-page-subtitle">
            {users.length} user{users.length === 1 ? '' : 's'} on file
          </p>
        </div>
        <button type="button" className="mm-btn mm-btn--primary" onClick={openCreate}>
          + New user
        </button>
      </header>

      <div className="users-layout">
        <aside className="mm-card user-list-panel">
          <div className="user-search">
            <input className="mm-input"
              type="search"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder="Search by name, email, or role"
              aria-label="Search users"
            />
          </div>
          <div className="user-filter">
            <select className="mm-select"
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
              <p className="mm-alert mm-alert--danger user-list-message" role="alert">
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

        <section className="mm-card user-detail-panel" aria-live="polite">
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
                    className="mm-btn mm-btn--ghost"
                    onClick={() => openEditWithUser(selectedUser)}
                  >
                    Edit
                  </button>
                  <button
                    type="button"
                    className="mm-btn mm-btn--danger"
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
                    {selectedUser.role.charAt(0).toUpperCase() + selectedUser.role.slice(1)}
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
        <UserFormModal
          mode={formMode}
          user={selectedUser}
          saving={formSaving}
          error={formError}
          titleId="user-form-title"
          onSubmit={(payload) => void handleUserSubmit(payload)}
          onClose={closeForm}
        />
      )}
    </section>
  );
}
