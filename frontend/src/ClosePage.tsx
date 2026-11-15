import { useEffect, useMemo, useState, type FormEvent } from 'react';
import { apiFetch, ApiError } from './api';
import './ClosePage.css';

/**
 * Denomination definitions mirroring the backend `cash_count.CURRENCY_DEFS`
 * (14 entries: $1/$2 each appear in both coins and bills). Keeping the keys
 * and values identical means a submitted `counts` object is interpreted by
 * `calculate_totals` exactly as it was entered — the existing cash-counting
 * logic is preserved, only fitted into the paginated flow.
 */
interface Denomination {
  readonly key: string;
  readonly value: number;
  readonly category: 'coins' | 'bills';
  readonly label: string;
}

const DENOMS: readonly Denomination[] = [
  { key: 'pennies', value: 0.01, category: 'coins', label: 'Pennies' },
  { key: 'nickels', value: 0.05, category: 'coins', label: 'Nickels' },
  { key: 'dimes', value: 0.1, category: 'coins', label: 'Dimes' },
  { key: 'quarters', value: 0.25, category: 'coins', label: 'Quarters' },
  { key: 'half_dollars', value: 0.5, category: 'coins', label: 'Half dollars' },
  { key: 'dollars', value: 1, category: 'coins', label: 'Coin dollars' },
  { key: 'two_dollars', value: 2, category: 'coins', label: 'Coin twos' },
  { key: 'ones', value: 1, category: 'bills', label: 'Ones' },
  { key: 'twos', value: 2, category: 'bills', label: 'Twos' },
  { key: 'fives', value: 5, category: 'bills', label: 'Fives' },
  { key: 'tens', value: 10, category: 'bills', label: 'Tens' },
  { key: 'twenties', value: 20, category: 'bills', label: 'Twenties' },
  { key: 'fifties', value: 50, category: 'bills', label: 'Fifties' },
  { key: 'hundreds', value: 100, category: 'bills', label: 'Hundreds' },
];

interface Session {
  id: number;
  cashier: number;
  starting_cash: string;
  status: string;
  opened_at: string;
  closed_at: string | null;
  cash_counts: Record<string, number>;
  cash_removed: string;
  expected_cash: string;
  counted_cash: string;
  variance: string;
  sales_total: string;
  notes: string;
}

interface Payment {
  method: string;
  amount: string;
}

interface Sale {
  id: number;
  number: number;
  status: string;
  total: string;
  payments: Payment[];
  created_at: string;
  completed_at: string | null;
}

interface SummaryData {
  salesCount: number;
  grossSales: number;
  discounts: number;
  netSales: number;
  byPayment: Record<string, number>;
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

function emptyCounts(): Record<string, number> {
  const counts: Record<string, number> = {};
  for (const denom of DENOMS) {
    counts[denom.key] = 0;
  }
  return counts;
}

function countedTotal(counts: Record<string, number>): number {
  return DENOMS.reduce((sum, denom) => sum + denom.value * (counts[denom.key] ?? 0), 0);
}

export default function ClosePage() {
  const [session, setSession] = useState<Session | null>(null);
  const [summary, setSummary] = useState<SummaryData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const [step, setStep] = useState(0);
  const [counts, setCounts] = useState<Record<string, number>>(emptyCounts);

  const [backupState, setBackupState] = useState<'idle' | 'running' | 'success' | 'error'>(
    'idle',
  );
  const [backupMessage, setBackupMessage] = useState('');
  const [closing, setClosing] = useState(false);
  const [closed, setClosed] = useState(false);
  const [closingError, setClosingError] = useState('');

  // fetch completed sales and derive the summary stats the summary section shows
  async function loadSummary(current: Session) {
    try {
      const sales = await apiFetch<Sale[]>('/api/sales/');
      const upper = new Date(current.closed_at ?? new Date().toISOString()).getTime();
      const lower = new Date(current.opened_at).getTime();
      const inWindow = sales.filter((sale) => {
        if (sale.status !== 'completed') return false;
        const created = new Date(sale.created_at).getTime();
        return created >= lower && created <= upper;
      });

      let gross = 0;
      const byPayment: Record<string, number> = {};
      for (const sale of inWindow) {
        gross += toNumber(sale.total);
        for (const payment of sale.payments) {
          byPayment[payment.method] = toNumber(byPayment[payment.method] ?? 0) + toNumber(payment.amount);
        }
      }
      // Gross/net/discount roll-ups beyond the session totals arrive with the
      // pandas-powered summary in a follow-up; this shell only surfaces the
      // data the current sessions/sales API already exposes.
      setSummary({
        salesCount: inWindow.length,
        grossSales: gross,
        discounts: 0,
        netSales: gross,
        byPayment,
      });
    } catch {
      // Summary is best-effort; the page stays usable if the sales feed is
      // unavailable. The richer revenue/profit computation lands in a follow-up.
    }
  }

  useEffect(() => {
    const load = async () => {
      try {
        const sessions = await apiFetch<Session[]>('/api/sessions/');
        const open = sessions.find((s) => s.status === 'open') ?? sessions[0] ?? null;
        setSession(open);
        if (open) {
          setCounts({ ...emptyCounts(), ...(open.cash_counts ?? {}) });
          await loadSummary(open);
        }
      } catch (err) {
        setError(err instanceof ApiError ? err.message : 'Could not load the session.');
      } finally {
        setLoading(false);
      }
    };
    void load();
  }, []);

  const counted = useMemo(() => countedTotal(counts), [counts]);
  // Expected cash = starting cash + cash sales. `expected_cash` is only
  // populated once the session is closed; while open we compute from the
  // starting cash and the cash payments recorded in this shift.
  const expected =
    toNumber(session?.expected_cash) ||
    toNumber(session?.starting_cash) + toNumber(summary?.byPayment.cash ?? 0);
  const variance = expected - counted;

  const updateCount = (key: string, value: string) => {
    const parsed = Math.max(0, Number.parseInt(value, 10) || 0);
    setCounts((prev) => ({ ...prev, [key]: parsed }));
  };

  const canNext = step === 0 || step === 1;
  const nextLabel = step === 1 ? 'Continue to Close & Backup' : 'Continue to Summary';

  const goNext = () => {
    if (!canNext) return;
    setStep((s) => Math.min(2, s + 1));
  };

  const goBack = () => {
    setStep((s) => Math.max(0, s - 1));
  };

  const runBackup = async () => {
    setBackupState('running');
    setBackupMessage('');
    try {
      const res = await apiFetch<{ path: string }>('/api/backups/trigger/', {
        method: 'POST',
        body: JSON.stringify({}),
      });
      setBackupState('success');
      setBackupMessage(`Backup written to ${res.path}`);
    } catch (err) {
      setBackupState('error');
      setBackupMessage(err instanceof ApiError ? err.message : 'Backup failed.');
    }
  };

  const closeSession = async (event: FormEvent) => {
    event.preventDefault();
    if (!session) return;
    setClosing(true);
    setClosingError('');
    try {
      await apiFetch(`/api/sessions/${session.id}/close/`, {
        method: 'POST',
        body: JSON.stringify({ counts }),
      });
      setClosed(true);
    } catch (err) {
      setClosingError(err instanceof ApiError ? err.message : 'Could not close the session.');
    } finally {
      setClosing(false);
    }
  };

  const byPaymentLabels: Record<string, string> = {
    cash: 'Cash',
    card: 'Card',
    split: 'Split',
    invoice: 'Invoice',
  };

  return (
    <div className="mm-page close-page">
      <header className="mm-page-header">
        <div>
          <h1 className="mm-page-title">Close</h1>
          <p className="mm-page-subtitle">
            Count the drawer, review the shift, and close the session with a Z-report.
          </p>
        </div>
      </header>

      {loading && <div className="mm-loading">Loading session…</div>}

      {!loading && error && (
        <section className="mm-card mm-empty">
          <h2 className="mm-empty-title">Could not load the session</h2>
          <p className="mm-empty-text">{error}</p>
        </section>
      )}

      {!loading && !error && !session && (
        <section className="mm-card mm-empty">
          <h2 className="mm-empty-title">No session to close</h2>
          <p className="mm-empty-text">
            There is no open session. Start one before closing out a shift.
          </p>
        </section>
      )}

      {!loading && !error && session && (
        <div className="close-flow">
          <div className="close-flow-head" role="list" aria-label="Close flow steps">
            {['Count cash', 'Summary', 'Close & backup'].map((label, index) => (
              <div
                key={label}
                role="listitem"
                className={`close-step${index === step ? ' active' : ''}${index < step ? ' done' : ''}`}
                aria-current={index === step ? 'step' : undefined}
              >
                <span className="close-step-number">{index + 1}</span>
                <span className="close-step-label">{label}</span>
              </div>
            ))}
          </div>

          {step === 0 && (
            <section className="mm-card">
              <h2 className="mm-card-title">Count cash</h2>
              <p className="mm-card-subtitle">
                Enter the bills and coins currently in the drawer.
              </p>

              {(['bills', 'coins'] as const).map((category) => (
                <div key={category} className="close-denom-group">
                  <h3 className="close-denom-group-title">
                    {category === 'bills' ? 'Bills' : 'Coins'}
                  </h3>
                  {DENOMS.filter((d) => d.category === category).map((denom) => (
                    <div key={denom.key} className="close-denom-row">
                      <span className="close-denom-label">{denom.label}</span>
                      <span className="close-denom-value">${denom.value.toFixed(2)}</span>
                      <input
                        type="number"
                        min={0}
                        value={counts[denom.key] ?? 0}
                        aria-label={denom.label}
                        className="close-denom-input mm-input"
                        onChange={(e) => updateCount(denom.key, e.target.value)}
                      />
                      <span className="close-denom-subtotal">
                        = ${(denom.value * counts[denom.key]).toFixed(2)}
                      </span>
                    </div>
                  ))}
                </div>
              ))}

              <div className="close-cash-summary">
                <div className="close-cash-stat">
                  <div className="close-cash-stat-label">Starting cash</div>
                  <div className="close-cash-stat-value">
                    {formatMoney(session.starting_cash)}
                  </div>
                </div>
                <div className="close-cash-stat">
                  <div className="close-cash-stat-label">Cash sales</div>
                  <div className="close-cash-stat-value">
                    {formatMoney(summary?.byPayment.cash ?? 0)}
                  </div>
                </div>
                <div className="close-cash-stat">
                  <div className="close-cash-stat-label">Expected</div>
                  <div className="close-cash-stat-value">{formatMoney(expected)}</div>
                </div>
                <div className="close-cash-stat">
                  <div className="close-cash-stat-label">Counted</div>
                  <div className="close-cash-stat-value">{formatMoney(counted)}</div>
                </div>
                <div
                  className={`close-variance${Math.abs(variance) < 0.005 ? ' ok' : ' short'}`}
                >
                  Over/Short: {variance >= 0 ? '+' : '-'}
                  {formatMoney(Math.abs(variance))}
                </div>
              </div>
            </section>
          )}

          {step === 1 && (
            <section className="mm-card">
              <h2 className="mm-card-title">Session summary</h2>
              {summary ? (
                <>
                  <div className="close-summary-grid">
                    <div className="close-summary-cell">
                      <div className="close-summary-label">Sales</div>
                      <div className="close-summary-value">{summary.salesCount}</div>
                    </div>
                    <div className="close-summary-cell">
                      <div className="close-summary-label">Gross sales</div>
                      <div className="close-summary-value">{formatMoney(summary.grossSales)}</div>
                    </div>
                    <div className="close-summary-cell">
                      <div className="close-summary-label">Net sales</div>
                      <div className="close-summary-value">{formatMoney(summary.netSales)}</div>
                    </div>
                  </div>
                  <div className="close-summary-section">
                    <h3 className="close-summary-section-title">By payment</h3>
                    {Object.entries(summary.byPayment).length > 0 ? (
                      Object.entries(summary.byPayment).map(([method, amount]) => (
                        <div key={method} className="close-summary-row">
                          <span>{byPaymentLabels[method] ?? method}</span>
                          <span>{formatMoney(amount)}</span>
                        </div>
                      ))
                    ) : (
                      <p className="mm-muted">No payments recorded in this session.</p>
                    )}
                  </div>
                  <div className="close-summary-section">
                    <h3 className="close-summary-section-title">Cash reconciliation</h3>
                    <div className="close-summary-row">
                      <span>Counted cash</span>
                      <span>{formatMoney(counted)}</span>
                    </div>
                    <div className="close-summary-row">
                      <span>Expected cash</span>
                      <span>{formatMoney(expected)}</span>
                    </div>
                    <div className="close-summary-row">
                      <span>Over/Short</span>
                      <span>
                        {variance >= 0 ? '+' : ''}
                        {formatMoney(Math.abs(variance))}
                      </span>
                    </div>
                  </div>
                </>
              ) : (
                <p className="mm-muted">No summary available for this session.</p>
              )}
            </section>
          )}

          {step === 2 && (
            <section className="mm-card">
              <h2 className="mm-card-title">Close &amp; backup</h2>
              <p className="mm-card-subtitle">
                Back up the database, then close the session and print the Z-report.
              </p>

              <div className="close-backup-row">
                <button
                  type="button"
                  className="mm-btn"
                  onClick={() => void runBackup()}
                  disabled={backupState === 'running'}
                >
                  Backup to USB
                </button>
                {backupMessage && (
                  <span
                    className={`close-backup-status${
                      backupState === 'error' ? ' error' : ''
                    }`}
                  >
                    {backupMessage}
                  </span>
                )}
              </div>

              {closed ? (
                <div className="mm-alert mm-alert--success close-closed-notice">
                  <div>Session closed. The Z-report was saved and a backup was triggered.</div>
                  <button
                    type="button"
                    className="mm-btn close-zreport-btn"
                    onClick={() => {
                      window.open(`/api/sessions/${session.id}/zreport/`, '_blank');
                    }}
                  >
                    Print Z-report (PDF)
                  </button>
                </div>
              ) : (
                <form onSubmit={closeSession}>
                  {closingError && (
                    <div className="mm-alert mm-alert--danger">{closingError}</div>
                  )}
                  <button
                    type="submit"
                    className="mm-btn mm-btn--primary close-close-btn"
                    disabled={closing}
                  >
                    {closing ? 'Closing…' : 'Close Session & Print Z-Report'}
                  </button>
                </form>
              )}
            </section>
          )}

          <div className="close-nav" aria-label="Close flow navigation">
            <button
              type="button"
              className="mm-btn"
              onClick={goBack}
              disabled={step === 0}
            >
              Back
            </button>
            {canNext && (
              <button type="button" className="mm-btn mm-btn--primary" onClick={goNext}>
                {nextLabel}
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
