import { useMemo, useState, type FormEvent } from 'react';
import { apiFetch, ApiError } from './api';
import { Icon } from './Icon';
import {
  buildCandidates,
  repackPreview,
  type PackCandidate,
  type RepackProduct,
} from './repackMath';

interface RepackDialogProps {
  product: RepackProduct;
  productsById: Map<number, RepackProduct>;
  onClose: () => void;
  onSuccess: (reference: string) => void;
}

export default function RepackDialog({
  product,
  productsById,
  onClose,
  onSuccess,
}: RepackDialogProps) {
  const candidates = useMemo(() => buildCandidates(product), [product]);

  const [candidateId, setCandidateId] = useState<string>(
    candidates.length === 1 ? String(candidates[0].targetId) : '',
  );
  const [quantity, setQuantity] = useState('');
  const [note, setNote] = useState('');
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);

  const selectedCandidate: PackCandidate | undefined = candidates.find(
    (c) => String(c.targetId) === candidateId,
  );

  const qty = quantity.trim() === '' ? 0 : Number(quantity);

  const preview = useMemo(() => {
    if (!selectedCandidate || !Number.isInteger(qty) || qty < 1) return null;
    const target = productsById.get(selectedCandidate.targetId);
    if (!target) return null;
    return repackPreview(product, selectedCandidate, qty, target.quantity);
  }, [selectedCandidate, qty, product, productsById]);

  const canSubmit = !!selectedCandidate && preview?.valid === true && !saving;

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!canSubmit || !selectedCandidate) return;

    setSaving(true);
    setError('');
    try {
      const created = await apiFetch<{ reference: string }>('/api/inventory/repack/', {
        method: 'POST',
        body: JSON.stringify({
          source_product: product.id,
          target_product: selectedCandidate.targetId,
          source_quantity: qty,
          ...(note.trim() ? { note: note.trim() } : {}),
        }),
      });
      onSuccess(created.reference);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to record repack.');
      setSaving(false);
    }
  };

  if (candidates.length === 0) {
    return (
      <div className="mm-modal-backdrop" onMouseDown={onClose}>
        <div
          className="mm-modal"
          role="dialog"
          aria-modal="true"
          aria-labelledby="repack-no-title"
        >
          <header className="mm-modal-header">
            <h2 id="repack-no-title" className="mm-modal-title">
              Repack stock
            </h2>
            <button type="button" className="mm-modal-close" onClick={onClose} aria-label="Close">
              <Icon name="x" size={20} />
            </button>
          </header>
          <div className="mm-modal-body">
            <p className="mm-muted">
              <strong>{product.name}</strong> is not configured as a pack or a
              pack child, so there is nothing to repack it into. A manager must
              configure the pack mapping first.
            </p>
          </div>
          <footer className="mm-modal-footer">
            <button type="button" className="mm-btn mm-btn--ghost" onClick={onClose}>
              Close
            </button>
          </footer>
        </div>
      </div>
    );
  }

  return (
    <div className="mm-modal-backdrop" onMouseDown={(e) => { if (e.target === e.currentTarget && !saving) onClose(); }}>
      <div
        className="mm-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="repack-title"
      >
        <header className="mm-modal-header">
          <h2 id="repack-title" className="mm-modal-title">
            Repack stock
          </h2>
          <button
            type="button"
            className="mm-modal-close"
            onClick={() => { if (!saving) onClose(); }}
            aria-label="Close"
          >
            <Icon name="x" size={20} />
          </button>
        </header>
        <form className="repack-form" onSubmit={handleSubmit}>
          <div className="mm-modal-body repack-body">
            <p className="mm-muted">
              Convert units of <strong>{product.name}</strong> (on hand:{' '}
              <strong>{product.quantity}</strong>).
            </p>

            {error && (
              <div className="mm-alert mm-alert--danger" role="alert">
                {error}
              </div>
            )}

            {candidates.length > 1 && (
              <label className="mm-field">
                <span className="mm-label">Convert into</span>
                <select
                  className="mm-select"
                  value={candidateId}
                  onChange={(e) => setCandidateId(e.target.value)}
                  aria-label="Target pack"
                >
                  <option value="">— Select target —</option>
                  {candidates.map((c) => (
                    <option key={c.targetId} value={String(c.targetId)}>
                      {c.label} ({c.direction === 'split' ? 'split' : 'merge'}, {c.ratio}/pack)
                    </option>
                  ))}
                </select>
              </label>
            )}

            {candidates.length === 1 && selectedCandidate && (
              <p className="mm-muted">
                Convert into <strong>{selectedCandidate.label}</strong> (
                {selectedCandidate.direction === 'split' ? 'split' : 'merge'},{' '}
                {selectedCandidate.ratio} units per pack).
              </p>
            )}

            <label className="mm-field">
              <span className="mm-label">Source quantity</span>
              <input
                className="mm-input"
                type="number"
                min="1"
                step="1"
                inputMode="numeric"
                value={quantity}
                onChange={(e) => setQuantity(e.target.value)}
                placeholder="Whole units"
                required
                autoFocus
              />
            </label>

            {preview && (
              <div className="repack-preview">
                <p className="mm-muted">
                  {selectedCandidate!.direction === 'split' ? 'Produce' : 'Produce'}{' '}
                  <strong>{preview.targetQuantity ?? '—'}</strong> units →
                  {product.sku || product.name}. Source on hand becomes{' '}
                  <strong>{preview.sourceOnHand ?? '—'}</strong>; target on hand
                  becomes <strong>{preview.targetOnHand ?? '—'}</strong>.
                </p>
              </div>
            )}

            {preview?.valid === false && (
              <div className="mm-alert mm-alert--warning" role="alert">
                {preview.reason}
              </div>
            )}

            <label className="mm-field">
              <span className="mm-label">Note</span>
              <input
                className="mm-input"
                type="text"
                value={note}
                onChange={(e) => setNote(e.target.value)}
                maxLength={500}
                placeholder="Optional"
              />
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
              disabled={!canSubmit}
            >
              {saving ? 'Recording...' : 'Confirm repack'}
            </button>
          </footer>
        </form>
      </div>
    </div>
  );
}
