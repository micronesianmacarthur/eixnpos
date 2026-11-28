import { useEffect, useMemo, useState, type FormEvent } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { apiFetch, ApiError } from './api';
import { formatDateTime } from './dashboardFormat';
import { currentSession } from './session';
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
  business_day?: string | null;
  cash_counts: Record<string, number>;
  cash_removed: string;
  expected_cash: string;
  counted_cash: string;
  variance: string;
  sales_total: string;
  notes: string;
}

interface SummaryData {
  sale_count: number;
  item_count: number;
  gross_sales: string;
  discounts: string;
  tax_total: string;
  revenue: string;
  cost_of_goods: string;
  profit_loss: string;
  profit_margin: number | null;
  by_payment: Record<string, string>;
  by_category: Record<string, string>;
  /** True when any amount failed numeric parsing and was excluded. */
  warnings_blocked?: boolean;
}

interface SessionSummaryResponse {
  session: {
    id: number;
    cashier: string;
    status: string;
    opened_at: string;
    closed_at: string | null;
  };
  summary: SummaryData;
  warnings: Record<string, number>;
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
  const [searchParams] = useSearchParams();
  // `/close?session=<id>` targets a specific session (used by the dashboard's
  // "Close & reconcile" link for a shift left open on an earlier business
  // day). Without the param the page keeps its historical behaviour: resolve
  // today's open session for the caller's register/day.
  const requestedSessionId = searchParams.get('session');

  const [session, setSession] = useState<Session | null>(null);
  const [summary, setSummary] = useState<SummaryData | null>(null);
  const [summarySessionId, setSummarySessionId] = useState<number | null>(null);
  const [summaryError, setSummaryError] = useState('');
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

  // Next-session starting cash: the session cannot begin until a value is
  // entered and saved. After close/backup this prompt blocks the next POST.
  const [nextCashInput, setNextCashInput] = useState('');
  const [nextStarting, setNextStarting] = useState(false);
  const [nextSession, setNextSession] = useState<Session | null>(null);
  const [nextError, setNextError] = useState('');

  // Fetch the pandas-computed revenue/profit-loss summary for the current
  // session. Every state write happens only after the await, so the "loading"
  // and success states below are derived from whether the stored summary
  // belongs to the currently active session (summarySessionId) rather than
  // mutated synchronously in the effect body.
  useEffect(() => {
    const sessionId = session?.id;
    if (!sessionId) {
      return;
    }
    let cancelled = false;
    const load = async () => {
      try {
        const res = await apiFetch<SessionSummaryResponse>(
          `/api/sessions/${sessionId}/summary/`,
        );
        if (!cancelled) {
          setSummary({
            ...res.summary,
            warnings_blocked: Object.values(res.warnings ?? {}).some((n) => n > 0),
          });
          setSummarySessionId(sessionId);
          setSummaryError('');
        }
      } catch (err) {
        if (!cancelled) {
          setSummarySessionId(sessionId);
          setSummaryError(
            err instanceof ApiError ? err.message : 'Could not load the summary.',
          );
        }
      }
    };
    void load();
    return () => {
      cancelled = true;
    };
  }, [session?.id]);

  // Loading is derived: no stored summary/error yet for the active session.
  const summaryLoading =
    !!session && summarySessionId !== session?.id && !summaryError;

  // Resolve the session to close. When `?session=<id>` is present the caller
  // explicitly targeted one (the dashboard's previous-day reminder); otherwise
  // resolve today's session from GET /api/sessions/current/ — the same
  // register/day scope the route guard validated. We never scan the session
  // list for "an open one": with multiple accessible registers that could
  // pick a sibling register's shift, the exact failure ADR-001 forbids. If no
  // open session exists on the guard's register/day, show the empty state.
  useEffect(() => {
    const load = async () => {
      try {
        if (requestedSessionId !== null) {
          const numericId = Number(requestedSessionId);
          if (!Number.isInteger(numericId) || numericId <= 0) {
            setError('That close link is not valid. Open Close from the dashboard.');
            return;
          }
          const detail = await apiFetch<Session>(`/api/sessions/${numericId}/`);
          setSession(detail);
          setCounts({ ...emptyCounts(), ...(detail.cash_counts ?? {}) });
          return;
        }
        const current = await currentSession();
        if (!current.open || !current.sessionId) {
          setSession(null);
          return;
        }
        const detail = await apiFetch<Session>(`/api/sessions/${current.sessionId}/`);
        setSession(detail);
        setCounts({ ...emptyCounts(), ...(detail.cash_counts ?? {}) });
      } catch (err) {
        setError(err instanceof ApiError ? err.message : 'Could not load the session.');
      } finally {
        setLoading(false);
      }
    };
    void load();
  }, [requestedSessionId]);

  const counted = useMemo(() => countedTotal(counts), [counts]);
  // Expected cash = starting cash + cash sales. `expected_cash` is only
  // populated once the session is closed; while open we compute from the
  // starting cash and the cash payments recorded in this shift.
  const expected =
    toNumber(session?.expected_cash) ||
    toNumber(session?.starting_cash) + toNumber(summary?.by_payment.cash ?? 0);
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

  // Next session cannot start until a valid (non-negative, numeric) starting
  // cash is entered. Entered value is persisted on the session and used as the
  // opening float for the new shift.
  const nextCash = toNumber(nextCashInput);
  const nextCashValid = nextCashInput.trim() !== '' && Number.isFinite(nextCash) && nextCash >= 0;

  const startNextSession = async (event: FormEvent) => {
    event.preventDefault();
    if (!nextCashValid) return;
    setNextStarting(true);
    setNextError('');
    try {
      const created = await apiFetch<Session>('/api/sessions/', {
        method: 'POST',
        body: JSON.stringify({ starting_cash: nextCash.toFixed(2) }),
      });
      setNextSession(created);
    } catch (err) {
      setNextError(err instanceof ApiError ? err.message : 'Could not start the next session.');
    } finally {
      setNextStarting(false);
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
            Count the drawer, review the shift, and close the session with a
            Z-report. Closing is always manual — nothing closes a shift
            automatically.
          </p>
        </div>
      </header>

      {loading && <div className="mm-loading">Loading session…</div>}

      {!loading && error && (
        <section className="mm-card mm-empty">
          <h2 className="mm-empty-title">Could not load the session</h2>
          <p className="mm-empty-text">{error}</p>
          <Link className="mm-btn" to="/">
            Back to dashboard
          </Link>
        </section>
      )}

      {!loading && !error && !session && (
        <section className="mm-card mm-empty">
          <h2 className="mm-empty-title">No session to close</h2>
          <p className="mm-empty-text">
            There is no open session for today. If a shift from an earlier day
            is still open, the dashboard lists it under “Unclosed shift from an
            earlier day” with a Close &amp; reconcile link.
          </p>
          <Link className="mm-btn" to="/">
            Back to dashboard
          </Link>
        </section>
      )}

      {!loading && !error && session && session.status !== 'open' && (
        <section className="mm-card mm-empty close-already-closed">
          <h2 className="mm-empty-title">This session is already closed</h2>
          <p className="mm-empty-text">
            It was closed
            {session.closed_at ? ` on ${formatDateTime(session.closed_at)}` : ''}.
            No further action is needed.
          </p>
          <div className="close-already-closed-actions">
            <button
              type="button"
              className="mm-btn"
              onClick={() => {
                window.open(`/api/sessions/${session.id}/zreport/`, '_blank');
              }}
            >
              Print Z-report (PDF)
            </button>
            <Link className="mm-btn mm-btn--primary" to="/">
              Back to dashboard
            </Link>
          </div>
        </section>
      )}

      {!loading && !error && session && session.status === 'open' && (
        <div className="close-flow">
          {session.business_day && (
            <div className="mm-alert close-day-notice" role="note">
              This shift belongs to business day {session.business_day}. Sessions
              stay open across days until someone closes and reconciles them
              here.
            </div>
          )}
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
                    {formatMoney(summary?.by_payment.cash ?? 0)}
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
              {summaryLoading && (
                <p className="mm-muted">Loading session summary…</p>
              )}
              {!summaryLoading && summaryError && (
                <div className="mm-alert mm-alert--danger">{summaryError}</div>
              )}
              {!summaryLoading && !summaryError && summary && (
                <>
                  <div className="close-summary-grid">
                    <div className="close-summary-cell">
                      <div className="close-summary-label">Sales</div>
                      <div className="close-summary-value">{summary.sale_count}</div>
                    </div>
                    <div className="close-summary-cell">
                      <div className="close-summary-label">Gross sales</div>
                      <div className="close-summary-value">
                        {formatMoney(summary.gross_sales)}
                      </div>
                    </div>
                    <div className="close-summary-cell">
                      <div className="close-summary-label">Revenue</div>
                      <div className="close-summary-value">
                        {formatMoney(summary.revenue)}
                      </div>
                    </div>
                    <div className="close-summary-cell">
                      <div className="close-summary-label">Cost of goods</div>
                      <div className="close-summary-value">
                        {formatMoney(summary.cost_of_goods)}
                      </div>
                    </div>
                    <div
                      className={`close-summary-cell close-pnl${
                        toNumber(summary.profit_loss) < 0 ? ' neg' : ''
                      }`}
                    >
                      <div className="close-summary-label">Profit / Loss</div>
                      <div className="close-summary-value">
                        {formatMoney(summary.profit_loss)}
                      </div>
                    </div>
                    <div className="close-summary-cell">
                      <div className="close-summary-label">Margin</div>
                      <div className="close-summary-value">
                        {summary.profit_margin === null
                          ? '—'
                          : `${(summary.profit_margin * 100).toFixed(1)}%`}
                      </div>
                    </div>
                  </div>
                  <div className="close-summary-section">
                    <h3 className="close-summary-section-title">By payment</h3>
                    {Object.entries(summary.by_payment).length > 0 ? (
                      Object.entries(summary.by_payment).map(([method, amount]) => (
                        <div key={method} className="close-summary-row">
                          <span>{byPaymentLabels[method] ?? method}</span>
                          <span>{formatMoney(amount)}</span>
                        </div>
                      ))
                    ) : (
                      <p className="mm-muted">No payments recorded in this session.</p>
                    )}
                  </div>
                  {summary.warnings_blocked && (
                    <div className="mm-alert mm-alert--warning">
                      Some amounts were non-numeric and were excluded from the summary.
                    </div>
                  )}
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
              )}
              {!summaryLoading && !summaryError && !summary && (
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
                <div className="close-closed-section">
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

                  {nextSession ? (
                    <div className="mm-alert mm-alert--success close-next-notice" role="status">
                      Next session started with {formatMoney(nextSession.starting_cash)} in
                      starting cash.
                    </div>
                  ) : (
                    <form
                      className="mm-card close-next-form"
                      onSubmit={startNextSession}
                      aria-label="Start next session"
                    >
                      <h3 className="close-next-title">Start the next session</h3>
                      <p className="mm-card-subtitle">
                        Enter the money you are placing in the drawer, then start the next
                        session.
                      </p>
                      <label className="close-next-label" htmlFor="next-starting-cash">
                        Starting cash
                      </label>
                      <input
                        id="next-starting-cash"
                        type="number"
                        min={0}
                        step="0.01"
                        inputMode="decimal"
                        className="mm-input close-next-input"
                        value={nextCashInput}
                        placeholder="0.00"
                        onChange={(e) => setNextCashInput(e.target.value)}
                        aria-required="true"
                      />
                      {nextError && (
                        <div className="mm-alert mm-alert--danger">{nextError}</div>
                      )}
                      <button
                        type="submit"
                        className="mm-btn mm-btn--primary close-next-btn"
                        disabled={!nextCashValid || nextStarting}
                      >
                        {nextStarting ? 'Starting…' : 'Start Next Session'}
                      </button>
                    </form>
                  )}
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
