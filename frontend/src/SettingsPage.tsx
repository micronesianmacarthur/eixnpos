import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type FormEvent,
  type KeyboardEvent,
} from 'react';
import { apiFetch, ApiError } from './api';
import { useAuth } from './authContext';
import { useTheme } from './themeContext';
import './SettingsPage.css';

type TabId = 'store' | 'users' | 'backup' | 'appearance';

const TABS: Array<{ id: TabId; label: string }> = [
  { id: 'store', label: 'Store' },
  { id: 'users', label: 'Users' },
  { id: 'backup', label: 'Backup' },
  { id: 'appearance', label: 'Appearance' },
];

interface BusinessInfo {
  company_name: string;
  phone: string;
  email: string;
  address: string;
  website: string;
  backup_path: string;
}

interface User {
  id: number;
  email: string;
  name: string;
  role: string;
  active: boolean;
}

interface UserForm {
  username: string;
  email: string;
  first_name: string;
  last_name: string;
  role: string;
  password: string;
}

const ROLES = ['cashier', 'manager', 'admin'] as const;

const EMPTY_USER_FORM: UserForm = {
  username: '',
  email: '',
  first_name: '',
  last_name: '',
  role: 'cashier',
  password: '',
};

function normalizeList<T>(data: T[] | { results: T[] }): T[] {
  return Array.isArray(data) ? data : data.results;
}

function roleBadge(role: string): string {
  const cls = `mm-badge ${role === 'admin' ? 'mm-badge--primary' : role === 'manager' ? 'mm-badge--info' : 'mm-badge--muted'}`;
  return `${cls} settings-role`;
}

export default function SettingsPage() {
  const { me } = useAuth();
  const currentUserId = me?.user.id ?? null;
  const { theme, toggleTheme } = useTheme();

  const [activeTab, setActiveTab] = useState<TabId>('store');
  const tabRefs = useRef<Map<TabId, HTMLButtonElement>>(new Map());

  // Store section ---------------------------------------------------------
  const [store, setStore] = useState<BusinessInfo>({
    company_name: '',
    phone: '',
    email: '',
    address: '',
    website: '',
    backup_path: '',
  });
  const [storeLoading, setStoreLoading] = useState(true);
  const [storeSaving, setStoreSaving] = useState(false);
  const [storeError, setStoreError] = useState('');
  const [storeSaved, setStoreSaved] = useState('');

  // Users section ---------------------------------------------------------
  const [users, setUsers] = useState<User[]>([]);
  const [usersLoading, setUsersLoading] = useState(true);
  const [usersError, setUsersError] = useState('');
  const [userFormMode, setUserFormMode] = useState<'closed' | 'create' | 'edit'>('closed');
  const [userForm, setUserForm] = useState<UserForm>(EMPTY_USER_FORM);
  const [selectedUser, setSelectedUser] = useState<User | null>(null);
  const [userFormError, setUserFormError] = useState('');
  const [userFormSaving, setUserFormSaving] = useState(false);
  const [deletingId, setDeletingId] = useState<number | null>(null);

  // Backup section --------------------------------------------------------
  const [backupState, setBackupState] = useState<'idle' | 'running' | 'success' | 'error'>('idle');
  const [backupMessage, setBackupMessage] = useState('');

  const loadStore = useCallback(async () => {
    try {
      const data = await apiFetch<BusinessInfo>('/api/business/');
      setStore({
        company_name: data.company_name || '',
        phone: data.phone || '',
        email: data.email || '',
        address: data.address || '',
        website: data.website || '',
        backup_path: data.backup_path || '',
      });
      setStoreError('');
      setStoreSaved('');
    } catch (err) {
      setStoreError(err instanceof ApiError ? err.message : 'Failed to load store details');
    } finally {
      setStoreLoading(false);
    }
  }, []);

  const loadUsers = useCallback(async () => {
    try {
      const data = await apiFetch<User[] | { results: User[] }>('/api/users/');
      setUsers(normalizeList(data));
      setUsersError('');
    } catch (err) {
      setUsersError(err instanceof ApiError ? err.message : 'Failed to load users');
    } finally {
      setUsersLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadStore();
  }, [loadStore]);

  useEffect(() => {
    if (activeTab === 'users') {
      void loadUsers();
    }
  }, [activeTab, loadUsers]);

  const handleStoreField = (field: keyof BusinessInfo, value: string) => {
    setStore((current) => ({ ...current, [field]: value }));
    setStoreSaved('');
  };

  const handleStoreSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setStoreSaving(true);
    setStoreError('');
    setStoreSaved('');
    try {
      await apiFetch<BusinessInfo>('/api/business/', {
        method: 'PATCH',
        body: JSON.stringify({
          company_name: store.company_name,
          phone: store.phone,
          email: store.email,
          address: store.address,
          website: store.website,
        }),
      });
      setStoreSaved('Store details saved.');
    } catch (err) {
      setStoreError(err instanceof ApiError ? err.message : 'Failed to save store details');
    } finally {
      setStoreSaving(false);
    }
  };

  // Users form ------------------------------------------------------------
  const openCreateUser = () => {
    setUserForm({ ...EMPTY_USER_FORM });
    setSelectedUser(null);
    setUserFormError('');
    setUserFormMode('create');
  };

  const openEditUser = (user: User) => {
    const nameParts = user.name.trim().split(' ');
    setSelectedUser(user);
    setUserForm({
      username: nameParts[0] || '',
      email: user.email,
      first_name: nameParts[0] || '',
      last_name: nameParts.slice(1).join(' ') || '',
      role: user.role,
      password: '',
    });
    setUserFormError('');
    setUserFormMode('edit');
  };

  const closeUserForm = () => {
    if (userFormSaving) return;
    setUserFormMode('closed');
    setSelectedUser(null);
  };

  useEffect(() => {
    if (userFormMode === 'closed') return;
    const handleKeyDown = (event: globalThis.KeyboardEvent) => {
      if (event.key === 'Escape') {
        closeUserForm();
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [userFormMode, userFormSaving]);

  const handleDeleteUser = async (user: User) => {
    if (user.id === currentUserId) {
      setUsersError('You cannot delete your own account.');
      return;
    }
    const confirmed = window.confirm(
      `Delete ${user.name} (${user.email})? This cannot be undone.`,
    );
    if (!confirmed) return;
    setDeletingId(user.id);
    setUsersError('');
    try {
      await apiFetch(`/api/users/${user.id}/`, { method: 'DELETE' });
      await loadUsers();
    } catch (err) {
      setUsersError(err instanceof ApiError ? err.message : 'Failed to delete user');
    } finally {
      setDeletingId(null);
    }
  };

  const handleUserSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const username = userForm.username.trim();
    const email = userForm.email.trim();
    const firstName = userForm.first_name.trim();
    const lastName = userForm.last_name.trim();
    const role = userForm.role;

    if (!username) {
      setUserFormError('Username is required.');
      return;
    }
    if (!firstName && !lastName) {
      setUserFormError('At least first name or last name is required.');
      return;
    }
    if (!ROLES.includes(role as (typeof ROLES)[number])) {
      setUserFormError('Invalid role selected.');
      return;
    }
    if (userFormMode === 'create' && !userForm.password) {
      setUserFormError('Password is required for new users.');
      return;
    }
    if (userForm.password && userForm.password.length < 8) {
      setUserFormError('Password must be at least 8 characters.');
      return;
    }

    setUserFormSaving(true);
    setUserFormError('');

    const payload: Record<string, string> = { email, first_name: firstName, last_name: lastName, role };
    if (userFormMode === 'create') payload.username = username;
    if (userForm.password) payload.password = userForm.password;

    try {
      if (userFormMode === 'create') {
        await apiFetch<User>('/api/users/', { method: 'POST', body: JSON.stringify(payload) });
      } else if (selectedUser) {
        await apiFetch<User>(`/api/users/${selectedUser.id}/`, {
          method: 'PATCH',
          body: JSON.stringify(payload),
        });
      } else {
        setUserFormError('No user selected for editing.');
        setUserFormSaving(false);
        return;
      }
      setUserFormMode('closed');
      setSelectedUser(null);
      await loadUsers();
    } catch (err) {
      setUserFormError(err instanceof ApiError ? err.message : 'Failed to save user');
    } finally {
      setUserFormSaving(false);
    }
  };

  // Backup ----------------------------------------------------------------
  const handleSaveBackupPath = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setStoreSaving(true);
    setStoreError('');
    setStoreSaved('');
    try {
      await apiFetch<BusinessInfo>('/api/business/', {
        method: 'PATCH',
        body: JSON.stringify({ backup_path: store.backup_path }),
      });
      setStoreSaved('Backup path saved.');
    } catch (err) {
      setStoreError(err instanceof ApiError ? err.message : 'Failed to save backup path');
    } finally {
      setStoreSaving(false);
    }
  };

  const handleBackupNow = async () => {
    setBackupState('running');
    setBackupMessage('');
    try {
      const data = await apiFetch<{ path: string }>('/api/backups/trigger/', {
        method: 'POST',
        body: JSON.stringify({ path: store.backup_path }),
      });
      setBackupState('success');
      setBackupMessage(
        data.path ? `Backup completed: ${data.path}` : 'Backup completed.',
      );
    } catch (err) {
      setBackupState('error');
      setBackupMessage(err instanceof ApiError ? err.message : 'Backup failed.');
    }
  };

  // Tab keyboard navigation -----------------------------------------------
  const handleTabKeyDown = (event: KeyboardEvent<HTMLButtonElement>, id: TabId) => {
    const index = TABS.findIndex((tab) => tab.id === id);
    let nextIndex: number | null = null;
    if (event.key === 'ArrowRight') nextIndex = (index + 1) % TABS.length;
    else if (event.key === 'ArrowLeft') nextIndex = (index - 1 + TABS.length) % TABS.length;
    else if (event.key === 'Home') nextIndex = 0;
    else if (event.key === 'End') nextIndex = TABS.length - 1;
    if (nextIndex === null) return;
    event.preventDefault();
    const next = TABS[nextIndex];
    setActiveTab(next.id);
    tabRefs.current.get(next.id)?.focus();
  };

  const storeForm = () => (
    <form className="settings-form" onSubmit={handleStoreSubmit}>
      <div className="settings-fields">
        <div className="mm-field">
          <label className="mm-label" htmlFor="settings-company-name">Company name</label>
          <input
            id="settings-company-name"
            className="mm-input"
            type="text"
            name="company_name"
            value={store.company_name}
            onChange={(event) => handleStoreField('company_name', event.target.value)}
            autoComplete="organization"
          />
        </div>
        <div className="mm-field">
          <label className="mm-label" htmlFor="settings-phone">Phone</label>
          <input
            id="settings-phone"
            className="mm-input"
            type="text"
            name="phone"
            value={store.phone}
            onChange={(event) => handleStoreField('phone', event.target.value)}
            autoComplete="tel"
          />
        </div>
        <div className="mm-field">
          <label className="mm-label" htmlFor="settings-email">Email</label>
          <input
            id="settings-email"
            className="mm-input"
            type="email"
            name="email"
            value={store.email}
            onChange={(event) => handleStoreField('email', event.target.value)}
            autoComplete="email"
          />
        </div>
        <div className="mm-field">
          <label className="mm-label" htmlFor="settings-address">Address</label>
          <input
            id="settings-address"
            className="mm-input"
            type="text"
            name="address"
            value={store.address}
            onChange={(event) => handleStoreField('address', event.target.value)}
            autoComplete="street-address"
          />
        </div>
        <div className="mm-field">
          <label className="mm-label" htmlFor="settings-website">Website</label>
          <input
            id="settings-website"
            className="mm-input"
            type="url"
            name="website"
            value={store.website}
            onChange={(event) => handleStoreField('website', event.target.value)}
            placeholder="https://"
          />
        </div>
      </div>
      {storeError && (
        <p className="mm-alert mm-alert--danger settings-notice" role="alert">
          {storeError}
        </p>
      )}
      {storeSaved && (
        <p className="mm-alert mm-alert--success settings-notice" role="status">
          {storeSaved}
        </p>
      )}
      <footer className="mm-card-footer">
        <button type="submit" className="mm-btn mm-btn--primary" disabled={storeSaving}>
          {storeSaving ? 'Saving...' : 'Save'}
        </button>
      </footer>
    </form>
  );

  return (
    <section className="mm-page settings-page">
      <header className="mm-page-header">
        <div>
          <h1 className="mm-page-title">Settings</h1>
          <p className="mm-page-subtitle">
            Configure store details, backup path, and preferences.
          </p>
        </div>
      </header>

      <div className="settings-tabs" role="tablist" aria-label="Settings sections">
        {TABS.map((tab) => (
          <button
            key={tab.id}
            ref={(node) => {
              if (node) tabRefs.current.set(tab.id, node);
              else tabRefs.current.delete(tab.id);
            }}
            type="button"
            role="tab"
            id={`settings-tab-${tab.id}`}
            aria-selected={activeTab === tab.id}
            aria-controls={`settings-panel-${tab.id}`}
            tabIndex={activeTab === tab.id ? 0 : -1}
            className={`settings-tab${activeTab === tab.id ? ' active' : ''}`}
            onClick={() => setActiveTab(tab.id)}
            onKeyDown={(event) => handleTabKeyDown(event, tab.id)}
          >
            {tab.label}
          </button>
        ))}
      </div>

      <div
        className="settings-panel"
        role="tabpanel"
        id="settings-panel-store"
        aria-labelledby="settings-tab-store"
        hidden={activeTab !== 'store'}
      >
        <div className="mm-card">
          <div className="mm-card-header">
            <div>
              <h2 className="mm-card-title">Store</h2>
              <p className="mm-card-subtitle">
                Company details shown on receipts and documents.
              </p>
            </div>
          </div>
          {storeLoading ? (
            <p className="mm-loading">
              <span className="mm-spinner" aria-hidden="true" />
              Loading store details...
            </p>
          ) : (
            storeForm()
          )}
        </div>
      </div>

      <div
        className="settings-panel"
        role="tabpanel"
        id="settings-panel-users"
        aria-labelledby="settings-tab-users"
        hidden={activeTab !== 'users'}
      >
        <div className="mm-card">
          <div className="mm-card-header">
            <div>
              <h2 className="mm-card-title">Users</h2>
              <p className="mm-card-subtitle">Manage staff accounts and roles.</p>
            </div>
            <button type="button" className="mm-btn mm-btn--primary" onClick={openCreateUser}>
              + Add user
            </button>
          </div>
          {usersLoading && (
            <p className="mm-loading">
              <span className="mm-spinner" aria-hidden="true" />
              Loading users...
            </p>
          )}
          {usersError && (
            <p className="mm-alert mm-alert--danger settings-notice" role="alert">
              {usersError}
            </p>
          )}
          {!usersLoading && !usersError && (
            <div className="mm-table-wrap">
              <table className="mm-table">
                <thead>
                  <tr>
                    <th scope="col">Name</th>
                    <th scope="col">Role</th>
                    <th scope="col">Status</th>
                    <th scope="col">
                      <span className="mm-visually-hidden">Actions</span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {users.map((user) => (
                    <tr key={user.id}>
                      <td className="settings-user-name">{user.name}</td>
                      <td>
                        <span className={roleBadge(user.role)}>
                          {user.role.charAt(0).toUpperCase() + user.role.slice(1)}
                        </span>
                      </td>
                      <td>
                        {user.active ? (
                          <span className="mm-badge mm-badge--success">Active</span>
                        ) : (
                          <span className="mm-badge mm-badge--muted">Inactive</span>
                        )}
                      </td>
                      <td className="settings-user-actions">
                        <button
                          type="button"
                          className="mm-btn mm-btn--sm mm-btn--ghost"
                          onClick={() => openEditUser(user)}
                        >
                          Edit
                        </button>
                        <button
                          type="button"
                          className="mm-btn mm-btn--sm mm-btn--danger"
                          onClick={() => void handleDeleteUser(user)}
                          disabled={deletingId === user.id || user.id === currentUserId}
                        >
                          {deletingId === user.id
                            ? 'Deleting...'
                            : user.id === currentUserId
                              ? 'You'
                              : 'Delete'}
                        </button>
                      </td>
                    </tr>
                  ))}
                  {users.length === 0 && (
                    <tr>
                      <td colSpan={4} className="settings-empty-row">
                        No users yet. Create the first one.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>

      <div
        className="settings-panel"
        role="tabpanel"
        id="settings-panel-backup"
        aria-labelledby="settings-tab-backup"
        hidden={activeTab !== 'backup'}
      >
        <div className="mm-card">
          <div className="mm-card-header">
            <div>
              <h2 className="mm-card-title">Backup</h2>
              <p className="mm-card-subtitle">
                Back up the local database to a chosen path.
              </p>
            </div>
          </div>
          <form className="settings-form" onSubmit={handleSaveBackupPath}>
            <div className="mm-field">
              <label className="mm-label" htmlFor="settings-backup-path">Backup path</label>
              <input
                id="settings-backup-path"
                className="mm-input"
                type="text"
                name="backup_path"
                value={store.backup_path}
                onChange={(event) => handleStoreField('backup_path', event.target.value)}
                placeholder="/backup"
                autoComplete="off"
                disabled={storeLoading}
              />
            </div>
            <p className="mm-hint settings-hint">
              Manual trigger + auto at session close. Keeps last 7.
            </p>
            {storeError && (
              <p className="mm-alert mm-alert--danger settings-notice" role="alert">
                {storeError}
              </p>
            )}
            {storeSaved && (
              <p className="mm-alert mm-alert--success settings-notice" role="status">
                {storeSaved}
              </p>
            )}
            <div className="settings-row settings-backup-actions">
              <button type="submit" className="mm-btn mm-btn--outline" disabled={storeSaving || storeLoading}>
                {storeSaving ? 'Saving...' : 'Save path'}
              </button>
              <button
                type="button"
                className="mm-btn mm-btn--primary"
                onClick={() => void handleBackupNow()}
                disabled={backupState === 'running' || storeLoading}
              >
                {backupState === 'running' ? 'Backing up...' : 'Back up now'}
              </button>
            </div>
            {backupState === 'success' && (
              <p className="mm-alert mm-alert--success settings-notice" role="status">
                {backupMessage}
              </p>
            )}
            {backupState === 'error' && (
              <p className="mm-alert mm-alert--danger settings-notice" role="alert">
                {backupMessage}
              </p>
            )}
          </form>
        </div>
      </div>

      <div
        className="settings-panel"
        role="tabpanel"
        id="settings-panel-appearance"
        aria-labelledby="settings-tab-appearance"
        hidden={activeTab !== 'appearance'}
      >
        <div className="mm-card">
          <div className="mm-card-header">
            <div>
              <h2 className="mm-card-title">Appearance</h2>
              <p className="mm-card-subtitle">Choose between light and dark theme.</p>
            </div>
          </div>
          <div className="settings-row settings-appearance-row">
            <span className="mm-label">
              Theme: <span className="settings-theme-label">{theme === 'dark' ? 'Dark' : 'Light'}</span>
            </span>
            <button
              type="button"
              className="mm-btn mm-btn--outline"
              onClick={toggleTheme}
              aria-pressed={theme === 'dark'}
            >
              Switch to {theme === 'dark' ? 'light' : 'dark'} mode
            </button>
          </div>
        </div>
      </div>

      {userFormMode !== 'closed' && (
        <div
          className="mm-modal-backdrop"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) closeUserForm();
          }}
        >
          <div
            className="mm-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="settings-user-form-title"
          >
            <header className="mm-modal-header">
              <h2 id="settings-user-form-title">
                {userFormMode === 'create' ? 'New user' : 'Edit user'}
              </h2>
              <button
                type="button"
                className="mm-modal-close"
                aria-label="Close"
                onClick={closeUserForm}
              >
                &times;
              </button>
            </header>
            <form className="mm-modal-body" onSubmit={handleUserSubmit}>
              {userFormError && (
                <p className="mm-alert mm-alert--danger" role="alert">
                  {userFormError}
                </p>
              )}
              <label className="mm-field">
                <span className="mm-label">Username</span>
                <input
                  className="mm-input"
                  type="text"
                  value={userForm.username}
                  onChange={(event) => setUserForm((c) => ({ ...c, username: event.target.value }))}
                  required
                  autoFocus
                  maxLength={150}
                  disabled={userFormMode === 'edit'}
                />
                {userFormMode === 'edit' && (
                  <span className="mm-hint">Username cannot be changed</span>
                )}
              </label>
              <label className="mm-field">
                <span className="mm-label">Email</span>
                <input
                  className="mm-input"
                  type="email"
                  value={userForm.email}
                  onChange={(event) => setUserForm((c) => ({ ...c, email: event.target.value }))}
                  maxLength={254}
                />
              </label>
              <div className="settings-field-row">
                <label className="mm-field">
                  <span className="mm-label">First name</span>
                  <input
                    className="mm-input"
                    type="text"
                    value={userForm.first_name}
                    onChange={(event) => setUserForm((c) => ({ ...c, first_name: event.target.value }))}
                    required
                    maxLength={150}
                  />
                </label>
                <label className="mm-field">
                  <span className="mm-label">Last name</span>
                  <input
                    className="mm-input"
                    type="text"
                    value={userForm.last_name}
                    onChange={(event) => setUserForm((c) => ({ ...c, last_name: event.target.value }))}
                    maxLength={150}
                    placeholder="Optional"
                  />
                </label>
              </div>
              <label className="mm-field">
                <span className="mm-label">Role</span>
                <select
                  className="mm-select"
                  value={userForm.role}
                  onChange={(event) => setUserForm((c) => ({ ...c, role: event.target.value }))}
                  required
                >
                  {ROLES.map((role) => (
                    <option key={role} value={role}>
                      {role.charAt(0).toUpperCase() + role.slice(1)}
                    </option>
                  ))}
                </select>
              </label>
              <label className="mm-field">
                <span className="mm-label">Password</span>
                <input
                  className="mm-input"
                  type="password"
                  value={userForm.password}
                  onChange={(event) => setUserForm((c) => ({ ...c, password: event.target.value }))}
                  minLength={8}
                  maxLength={128}
                  placeholder={
                    userFormMode === 'edit'
                      ? 'Leave blank to keep current password'
                      : 'At least 8 characters'
                  }
                  required={userFormMode === 'create'}
                />
              </label>
              <footer className="mm-modal-footer">
                <button
                  type="button"
                  className="mm-btn mm-btn--ghost"
                  onClick={closeUserForm}
                  disabled={userFormSaving}
                >
                  Cancel
                </button>
                <button type="submit" className="mm-btn mm-btn--primary" disabled={userFormSaving}>
                  {userFormSaving
                    ? 'Saving...'
                    : userFormMode === 'create'
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
