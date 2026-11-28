import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type FormEvent,
  type KeyboardEvent,
} from 'react';
import { apiFetch, ApiError } from './api';
import { useTheme } from './themeContext';
import { useBlocker } from 'react-router-dom';
import {
  DEFAULT_TARGET_MARGIN_PERCENT,
  MAX_TARGET_MARGIN_PERCENT,
  MIN_TARGET_MARGIN_PERCENT,
  formatTargetMarginPercent,
  parseTargetMarginPercent,
  targetMarginFromSettings,
  type PricingSettings,
} from './pricingSettings';
import './SettingsPage.css';

type TabId = 'store' | 'pricing' | 'backup' | 'appearance';

const TABS: Array<{ id: TabId; label: string }> = [
  { id: 'store', label: 'Store' },
  { id: 'pricing', label: 'Pricing' },
  { id: 'backup', label: 'Backup' },
  { id: 'appearance', label: 'Appearance' },
];

const DEFAULT_MARGIN_TEXT = formatTargetMarginPercent(DEFAULT_TARGET_MARGIN_PERCENT);

interface BusinessInfo {
  company_name: string;
  phone: string;
  email: string;
  address: string;
  website: string;
  backup_path: string;
}

export default function SettingsPage() {
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
  /** The store detail fields as last confirmed by the server (state, not ref,
   *  so "dirty" recomputes and clears reactively after each save). */
  const [savedStore, setSavedStore] = useState<BusinessInfo>({
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

  // Backup section --------------------------------------------------------
  const [backupState, setBackupState] = useState<'idle' | 'running' | 'success' | 'error'>('idle');
  const [backupMessage, setBackupMessage] = useState('');
  const [backupError, setBackupError] = useState('');
  const [backupSaved, setBackupSaved] = useState('');
  /** The backup path as last confirmed by the server, used to detect unsaved edits. */
  const [savedBackupPath, setSavedBackupPath] = useState('');

  const loadStore = useCallback(async () => {
    try {
      const data = await apiFetch<BusinessInfo>('/api/business/');
      const next: BusinessInfo = {
        company_name: data.company_name || '',
        phone: data.phone || '',
        email: data.email || '',
        address: data.address || '',
        website: data.website || '',
        backup_path: data.backup_path || '',
      };
      setStore(next);
      setSavedStore(next);
      setSavedBackupPath(data.backup_path || '');
      setStoreError('');
      setStoreSaved('');
      setBackupError('');
      setBackupSaved('');
    } catch (err) {
      setStoreError(err instanceof ApiError ? err.message : 'Failed to load store details');
    } finally {
      setStoreLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadStore();
  }, [loadStore]);

  const handleStoreField = (field: keyof BusinessInfo, value: string) => {
    setStore((current) => ({ ...current, [field]: value }));
    setStoreSaved('');
  };

  const handleStoreSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const required: Array<[keyof BusinessInfo, string]> = [
      ['company_name', 'Company name'],
      ['phone', 'Phone'],
      ['email', 'Email'],
    ];
    const missing = required.find(([field]) => !store[field].trim());
    if (missing) {
      setStoreError(`${missing[1]} is required.`);
      return;
    }
    const email = store.email.trim();
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
      setStoreError('Enter a valid email address.');
      return;
    }
    const website = store.website.trim();
    if (website && !/^https?:\/\/\S+$/i.test(website)) {
      setStoreError('Enter a valid website URL starting with http:// or https://.');
      return;
    }
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
      setSavedStore((prev) => ({
        ...prev,
        company_name: store.company_name,
        phone: store.phone,
        email: store.email,
        address: store.address,
        website: store.website,
      }));
      setStoreSaved('Store details saved.');
    } catch (err) {
      setStoreError(err instanceof ApiError ? err.message : 'Failed to save store details');
    } finally {
      setStoreSaving(false);
    }
  };

  /** Discard unsaved Store edits by restoring the last server-confirmed values. */
  const handleStoreCancel = () => {
    setStore((current) => ({ ...current, ...storeFieldSnapshot() }));
    setStoreError('');
    setStoreSaved('');
  };

  /** The store detail fields currently confirmed by the server (excl. backup_path). */
  const storeFieldSnapshot = () => {
    const { company_name, phone, email, address, website } = savedStore;
    return { company_name, phone, email, address, website };
  };

  const isStoreDirty = useMemo(
    () =>
      store.company_name !== savedStore.company_name ||
      store.phone !== savedStore.phone ||
      store.email !== savedStore.email ||
      store.address !== savedStore.address ||
      store.website !== savedStore.website,
    [store, savedStore],
  );

  // Backup ----------------------------------------------------------------
  const handleBackupPathField = (value: string) => {
    setStore((current) => ({ ...current, backup_path: value }));
    setBackupSaved('');
    setBackupError('');
  };

  const handleSaveBackupPath = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!store.backup_path.trim()) {
      setBackupError('Backup path is required.');
      return;
    }
    setStoreSaving(true);
    setBackupError('');
    setBackupSaved('');
    try {
      await apiFetch<BusinessInfo>('/api/business/', {
        method: 'PATCH',
        body: JSON.stringify({ backup_path: store.backup_path }),
      });
      setSavedBackupPath(store.backup_path);
      setSavedStore((prev) => ({ ...prev, backup_path: store.backup_path }));
      setBackupSaved('Backup path saved.');
    } catch (err) {
      setBackupError(err instanceof ApiError ? err.message : 'Failed to save backup path');
    } finally {
      setStoreSaving(false);
    }
  };

  /** Discard an unsaved backup-path edit by restoring the last server-confirmed path. */
  const handleBackupCancel = () => {
    setStore((current) => ({ ...current, backup_path: savedStore.backup_path }));
    setBackupError('');
    setBackupSaved('');
  };

  const isBackupDirty = store.backup_path !== savedStore.backup_path;

  // Pricing ---------------------------------------------------------------
  /** The margin input as typed; starts at the fresh-install default so the tab
   *  shows 15 even before the settings request resolves. */
  const [marginInput, setMarginInput] = useState(DEFAULT_MARGIN_TEXT);
  /** The margin as last confirmed by the server, used for dirty detection. */
  const [savedMargin, setSavedMargin] = useState(DEFAULT_TARGET_MARGIN_PERCENT);
  const [pricingLoading, setPricingLoading] = useState(true);
  const [pricingLoadFailed, setPricingLoadFailed] = useState(false);
  const [pricingSaving, setPricingSaving] = useState(false);
  const [pricingError, setPricingError] = useState('');
  const [pricingSaved, setPricingSaved] = useState('');

  const loadPricing = useCallback(async () => {
    try {
      const data = await apiFetch<PricingSettings>('/api/settings/pricing/');
      const margin = targetMarginFromSettings(data);
      setMarginInput(formatTargetMarginPercent(margin));
      setSavedMargin(margin);
      setPricingLoadFailed(false);
      setPricingError('');
      setPricingSaved('');
    } catch {
      // The saved value is unknown, so do not show an editable form: the user
      // could otherwise overwrite a margin they never saw.
      setPricingLoadFailed(true);
      setMarginInput(DEFAULT_MARGIN_TEXT);
      setSavedMargin(DEFAULT_TARGET_MARGIN_PERCENT);
      setPricingError(
        `Failed to load pricing settings. The default of ${DEFAULT_TARGET_MARGIN_PERCENT}% is used until they load.`,
      );
    } finally {
      setPricingLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadPricing();
  }, [loadPricing]);

  /** Re-run the load after a failure, showing the spinner meanwhile. */
  const handlePricingRetry = () => {
    setPricingLoading(true);
    setPricingError('');
    setPricingSaved('');
    void loadPricing();
  };

  const handleMarginField = (value: string) => {
    setMarginInput(value);
    setPricingError('');
    setPricingSaved('');
  };

  const handlePricingSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const parsed = parseTargetMarginPercent(marginInput);
    if (!parsed.ok) {
      setPricingError(parsed.error);
      setPricingSaved('');
      return;
    }
    setPricingSaving(true);
    setPricingError('');
    setPricingSaved('');
    try {
      const data = await apiFetch<PricingSettings>('/api/settings/pricing/', {
        method: 'PATCH',
        body: JSON.stringify({ default_target_margin_percent: parsed.value }),
      });
      // Trust the server's echo: it is the value that will be used next load.
      const margin = targetMarginFromSettings(data);
      setMarginInput(formatTargetMarginPercent(margin));
      setSavedMargin(margin);
      setPricingSaved('Pricing settings saved.');
    } catch (err) {
      setPricingError(err instanceof ApiError ? err.message : 'Failed to save pricing settings');
    } finally {
      setPricingSaving(false);
    }
  };

  /** Discard an unsaved margin edit by restoring the last server-confirmed value. */
  const handlePricingCancel = () => {
    setMarginInput(formatTargetMarginPercent(savedMargin));
    setPricingError('');
    setPricingSaved('');
  };

  const parsedMarginInput = parseTargetMarginPercent(marginInput);
  const isPricingDirty =
    !parsedMarginInput.ok || parsedMarginInput.value !== savedMargin;

  // Dirty-state guard: warn on browser-level close/reload when the Store,
  // Pricing, or Backup section has unsaved edits.
  const dirty = isStoreDirty || isBackupDirty || isPricingDirty;
  useEffect(() => {
    if (!dirty) return;
    const onBeforeUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      // eslint-disable-next-line no-param-reassign
      event.returnValue = '';
    };
    window.addEventListener('beforeunload', onBeforeUnload);
    return () => window.removeEventListener('beforeunload', onBeforeUnload);
  }, [dirty]);

  // Block in-app navigation (sidebar links) when there are unsaved edits,
  // instead of losing them silently. Tab switches within Settings stay free
  // because each section owns its own local state.
  const blocker = useBlocker(dirty);

  const handleBackupNow = async () => {
    const path = store.backup_path.trim();
    if (!path) {
      setBackupState('error');
      setBackupMessage('Backup path is required.');
      return;
    }
    setBackupState('running');
    setBackupMessage('');
    setBackupSaved('');
    try {
      // Persist an unsaved backup path before triggering so the manual backup
      // and any later session-close backup write to the same destination.
      if (store.backup_path !== savedBackupPath) {
        await apiFetch<BusinessInfo>('/api/business/', {
          method: 'PATCH',
          body: JSON.stringify({ backup_path: store.backup_path }),
        });
        setSavedBackupPath(store.backup_path);
        setSavedStore((prev) => ({ ...prev, backup_path: store.backup_path }));
      }
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
    <form className="settings-form" onSubmit={handleStoreSubmit} noValidate>
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
            required
            aria-required="true"
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
            required
            aria-required="true"
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
            required
            aria-required="true"
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
        <button
          type="button"
          className="mm-btn mm-btn--ghost"
          onClick={handleStoreCancel}
          disabled={storeSaving || storeLoading}
        >
          Cancel
        </button>
        <button type="submit" className="mm-btn mm-btn--primary" disabled={storeSaving || storeLoading}>
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
            Configure store details, pricing, backup path, and preferences.
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
        id="settings-panel-pricing"
        aria-labelledby="settings-tab-pricing"
        hidden={activeTab !== 'pricing'}
      >
        <div className="mm-card">
          <div className="mm-card-header">
            <div>
              <h2 className="mm-card-title">Pricing</h2>
              <p className="mm-card-subtitle">
                Default target margin used to suggest selling prices.
              </p>
            </div>
          </div>
          {pricingLoading ? (
            <p className="mm-loading">
              <span className="mm-spinner" aria-hidden="true" />
              Loading pricing settings...
            </p>
          ) : pricingLoadFailed ? (
            <div className="settings-form">
              <p className="mm-alert mm-alert--danger settings-notice" role="alert">
                {pricingError}
              </p>
              <div className="settings-row">
                <button
                  type="button"
                  className="mm-btn mm-btn--outline"
                  onClick={handlePricingRetry}
                >
                  Try again
                </button>
              </div>
            </div>
          ) : (
            <form className="settings-form" onSubmit={handlePricingSubmit} noValidate>
              <div className="mm-field">
                <label className="mm-label" htmlFor="settings-default-margin">
                  Default target margin (%)
                </label>
                <input
                  id="settings-default-margin"
                  className="mm-input settings-margin-input"
                  type="number"
                  name="default_target_margin_percent"
                  value={marginInput}
                  onChange={(event) => handleMarginField(event.target.value)}
                  min={MIN_TARGET_MARGIN_PERCENT}
                  max={MAX_TARGET_MARGIN_PERCENT}
                  step="0.01"
                  inputMode="decimal"
                  autoComplete="off"
                  aria-describedby="settings-default-margin-hint"
                  aria-invalid={pricingError ? true : undefined}
                  required
                  aria-required="true"
                />
              </div>
              <p className="mm-hint settings-hint" id="settings-default-margin-hint">
                Percentage used to suggest a selling price from a product&apos;s unit cost.
                Enter a value between {MIN_TARGET_MARGIN_PERCENT} and {MAX_TARGET_MARGIN_PERCENT}, with up to 2
                decimal places.
              </p>
              {pricingError && (
                <p className="mm-alert mm-alert--danger settings-notice" role="alert">
                  {pricingError}
                </p>
              )}
              {pricingSaved && (
                <p className="mm-alert mm-alert--success settings-notice" role="status">
                  {pricingSaved}
                </p>
              )}
              <footer className="mm-card-footer">
                <button
                  type="button"
                  className="mm-btn mm-btn--ghost"
                  onClick={handlePricingCancel}
                  disabled={pricingSaving}
                >
                  Cancel
                </button>
                <button type="submit" className="mm-btn mm-btn--primary" disabled={pricingSaving}>
                  {pricingSaving ? 'Saving...' : 'Save'}
                </button>
              </footer>
            </form>
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
          <form className="settings-form" onSubmit={handleSaveBackupPath} noValidate>
            <div className="mm-field">
              <label className="mm-label" htmlFor="settings-backup-path">Backup path</label>
              <input
                id="settings-backup-path"
                className="mm-input"
                type="text"
                name="backup_path"
                value={store.backup_path}
                onChange={(event) => handleBackupPathField(event.target.value)}
                placeholder="/backup"
                autoComplete="off"
                disabled={storeLoading}
                required
                aria-required="true"
              />
            </div>
            <p className="mm-hint settings-hint">
              Manual trigger + auto at session close. Keeps last 7.
            </p>
            {backupError && (
              <p className="mm-alert mm-alert--danger settings-notice" role="alert">
                {backupError}
              </p>
            )}
            {backupSaved && (
              <p className="mm-alert mm-alert--success settings-notice" role="status">
                {backupSaved}
              </p>
            )}
            <div className="settings-row settings-backup-actions">
              <button type="submit" className="mm-btn mm-btn--outline" disabled={storeSaving || storeLoading}>
                {storeSaving ? 'Saving...' : 'Save path'}
              </button>
              <button
                type="button"
                className="mm-btn mm-btn--ghost"
                onClick={handleBackupCancel}
                disabled={storeSaving || storeLoading}
              >
                Cancel
              </button>
              <button
                type="button"
                className="mm-btn mm-btn--primary"
                onClick={() => void handleBackupNow()}
                disabled={backupState === 'running' || storeLoading || !store.backup_path.trim()}
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

      {blocker.state === 'blocked' && (
        <div
          className="mm-modal-backdrop"
          role="presentation"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) blocker.reset();
          }}
        >
          <div className="mm-modal" role="dialog" aria-modal="true" aria-labelledby="settings-unsaved-title" aria-describedby="settings-unsaved-desc">
            <div className="mm-modal-header">
              <h2 className="mm-modal-title" id="settings-unsaved-title">Discard unsaved changes?</h2>
            </div>
            <div className="mm-modal-body">
              <p id="settings-unsaved-desc">
                You have unsaved changes on the Settings page. If you navigate away, they will be lost.
              </p>
            </div>
            <div className="mm-modal-footer">
              <button type="button" className="mm-btn mm-btn--ghost" onClick={() => blocker.reset()}>
                Keep editing
              </button>
              <button
                type="button"
                className="mm-btn mm-btn--primary"
                onClick={() => blocker.proceed()}
              >
                Discard &amp; leave
              </button>
            </div>
          </div>
        </div>
      )}
    </section>
  );
}
