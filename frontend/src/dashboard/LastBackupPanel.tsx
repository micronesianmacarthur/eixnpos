import { useCallback, useEffect, useState } from 'react';
import {
  type BackupStatus,
  fetchBackupStatus,
  triggerBackup,
} from './data';
import { formatDateTime } from '../dashboardFormat';
import './DashboardPanels.css';

interface LastBackupPanelProps {
  /** Opt-in test seam: seed data and skip the network effect. */
  initialStatus?: BackupStatus;
  /** IANA zone name used to format the last-backup wall clock (S2). */
  businessTimezone?: string | null;
}

/**
 * Last-backup status + quick "Backup now" action (contract §4.7 / §5.8).
 *
 * Answers the manager question "when was the system last backed up" and lets
 * them trigger a dump immediately. The status comes from
 * `GET /api/dashboard/backup/`; the action reuses the shared backup trigger
 * endpoint `POST /api/backups/trigger/`.
 *
 * States:
 * - path not configured → button disabled, tooltip explains why
 * - configured, never backed up → "Never backed up", button enabled
 * - configured, has backup → business-local timestamp, button enabled
 *
 * Accessibility: the primary status lives in an `aria-live` region so a
 * screen reader announces "backup started" / "backup complete", and the
 * button carries a descriptive `aria-label` including the role of the action.
 */
export default function LastBackupPanel({
  initialStatus,
  businessTimezone,
}: LastBackupPanelProps) {
  const [status, setStatus] = useState<BackupStatus | null>(
    initialStatus ?? null,
  );
  const [loading, setLoading] = useState(!initialStatus);
  const [error, setError] = useState('');
  // True while a POST /api/backups/trigger/ is in flight.
  const [backingUp, setBackingUp] = useState(false);
  // announcement fed to the aria-live region after a trigger completes/fails.
  const [announcement, setAnnouncement] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      setStatus(await fetchBackupStatus());
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : 'Failed to load the backup status.',
      );
      setStatus(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!initialStatus) void load();
  }, [initialStatus, load]);

  const trigger = useCallback(async () => {
    if (backingUp || !status?.backup_path_configured) return;
    setBackingUp(true);
    setAnnouncement('Backup started.');
    try {
      await triggerBackup();
      setAnnouncement('Backup complete.');
      // Refresh the last-backup time so the panel reflects the new dump.
      await load();
    } catch (err) {
      setAnnouncement(
        'Backup failed. ' +
          (err instanceof Error ? err.message : ''),
      );
    } finally {
      setBackingUp(false);
    }
  }, [backingUp, status, load]);

  let content: React.ReactNode;

  if (loading && !status) {
    content = (
      <div className="mm-loading dashboard-panel-loading" role="status">
        <span className="mm-spinner" aria-hidden="true" />
        Loading backup status…
      </div>
    );
  } else if (error && !status) {
    content = (
      <div className="mm-alert mm-alert--danger" role="alert">
        <span>Failed to load the backup status: {error}</span>{' '}
        <button
          type="button"
          className="dashboard-panel-retry"
          onClick={() => void load()}
        >
          Retry
        </button>
      </div>
    );
  } else {
    const configured = status?.backup_path_configured ?? false;
    const neverBackedUp = !status?.last_backup_at;
    const lastLabel = status?.last_backup_at_local
      ? formatDateTime(status.last_backup_at_local, businessTimezone)
      : '';

    const button = (
      <button
        type="button"
        className="mm-btn mm-btn--sm mm-btn--primary"
        onClick={() => void trigger()}
        disabled={backingUp || !configured}
        aria-label={
          configured
            ? backingUp
              ? 'Creating a backup now'
              : 'Create a backup now'
            : 'Backup is unavailable because no backup path is configured'
        }
        aria-busy={backingUp}
      >
        {backingUp ? (
          <>
            <span className="mm-spinner mm-spinner--inline" aria-hidden="true" />
            Backing up…
          </>
        ) : (
          'Backup now'
        )}
      </button>
    );

    content = (
      <div className="last-backup-panel-body">
        <div className="last-backup-info">
          <span
            className={`mm-icon-tile${
              neverBackedUp ? '' : ' mm-icon-tile--success'
            }`}
          >
            <svg
              viewBox="0 0 24 24"
              width="22"
              height="22"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
              aria-hidden="true"
            >
              <circle cx="12" cy="12" r="10" />
              <path d="M12 6v6l4 2" />
            </svg>
          </span>
          <div>
            <p className="dashboard-session-title">Last backup</p>
            {neverBackedUp ? (
              <p className="mm-muted dashboard-session-subtitle">
                Never backed up
              </p>
            ) : (
              <p className="mm-muted dashboard-session-subtitle">
                {lastLabel}
              </p>
            )}
          </div>
        </div>

        {configured ? (
          <span title="Back up the database now">{button}</span>
        ) : (
          <span
            title="No backup destination is configured. Set one in Settings to enable quick backups."
            className="mm-tooltip-anchor"
          >
            {button}
          </span>
        )}
      </div>
    );
  }

  return (
    <section
      className="mm-card dashboard-panel dashboard-panel--backup"
      aria-labelledby="backup-status-title"
    >
      <header className="mm-card-header">
        <div>
          <h2 className="mm-card-title" id="backup-status-title">
            Backups
          </h2>
          <p className="mm-card-subtitle">Databases of all system data are
            stored on this device.
          </p>
        </div>
        <button
          type="button"
          className="mm-btn mm-btn--outline mm-btn--sm"
          onClick={() => void load()}
        >
          Refresh
        </button>
      </header>

      {content}

      {/* Live region announces backup progress to assistive tech. */}
      <span className="mm-visually-hidden" role="status" aria-live="polite">
        {announcement}
      </span>
    </section>
  );
}
