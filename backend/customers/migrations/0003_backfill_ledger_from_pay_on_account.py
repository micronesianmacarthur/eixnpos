"""Backfill the append-only ledger from legacy ``PayOnAccount`` rows.

Background / rationale
----------------------
Before the ledger existed, ``Customer.balance`` was mutated in place and two
kinds of activity both contributed to it:

* invoice sales (a *credit* — customer owes more),
* ``pay_on_account`` (a *debit* — customer settles part of what they owe),
  which is the only legacy event that also wrote a ``PayOnAccount`` row.

``PayOnAccount`` rows therefore capture only the *debit* side of history:
they have no method / operator / sale link / running balance, and their
amounts are two-decimal ``Decimal`` values (the legacy code went through
``float`` internally, so values are trusted only at cent precision).

What this migration does
------------------------
For every ``Customer`` with any nonzero pre-migration balance, it rebuilds that
customer's ledger as an append-only sequence:

1. **Opening adjustment** — the portion of the customer's balance that predates
   the ledger and is *not* represented by a ``PayOnAccount`` row (invoice-sale
   credit and any other pre-ledger movement). ``opening = pre_balance +
   sum(pay_on_account debits)``. It is booked as ``entry_type=adjustment``,
   ``method=legacy``, ``operator=null``, with a provenance note — nothing is
   invented silently, the residual is explicitly marked as a historical import.
2. **One debit ``payment`` entry per ``PayOnAccount`` row**, in chronological
   order (``created_at, id``), with a computed running balance and the original
   ``created_at`` preserved on the ledger row so history reads accurately.
3. **Rounding-delta adjustments** — each legacy amount is re-quantized to cent
   precision (``ROUND_HALF_UP``, matching ``LedgerService.coerce_amount``). Any
   cent-level difference is booked as an explicit ``adjustment`` entry so no
   money is silently dropped. In practice the source is already at cent
   precision on any enforcing engine (``decimal(12,2)``), so quantization is an
   identity; a hypothetical sub-cent residue is not representable in the
   two-decimal ledger and is folded into the opening adjustment with a loud
   warning, never silently inventing money.

All imported rows are marked: ``method='legacy'``, ``operator=null``, and
``reference`` set to ``payonaccount:<id>`` / ``legacy-import:opening:<id>``,
with ``notes`` containing the ``legacy_import`` provenance marker.

Reconciliation is enforced: once every entry for a customer is written, the
final running balance is compared against the pre-migration ``Customer.balance``
and the migration **fails loudly** (raises) on any customer that does not
reconcile, rather than silently writing a corrupt ledger. ``Customer.balance``
is then set from the ledger sum (which, by construction, equals the pre-migration
value).

Idempotency
-----------
``forward`` is guarded against double-application: before writing an entry it
checks whether an entry with the same ``reference`` marker already exists and
skips it, so re-running the forward on a partially- or fully-migrated database
is a no-op rather than a duplicate. (Django normally applies each migration
exactly once; this guard covers manual re-application.)

Reversibility
-------------
``reverse`` is safe: it deletes exactly the ledger rows this migration created
(the ``payonaccount:*`` and ``legacy-import:opening:*`` entries) and leaves
``Customer.balance`` untouched (it was not changed by ``forward``). Net effect
of forward+reverse is zero for a ledger that has seen no intervening activity.
Caveat: if real ledger activity occurs *between* applying and reverting this
migration, reversing removes the synthetic provenance-marked rows but does not
rewind the later entries' running balances — reverting a fully-superseded ledger
is therefore best done before new activity accrues.
"""

from decimal import ROUND_HALF_UP, Decimal

import logging
from django.db import migrations

logger = logging.getLogger(__name__)

CENTS = Decimal('0.01')
POA_REF_PREFIX = 'payonaccount:'
OPENING_REF_PREFIX = 'legacy-import:opening:'
PROVENANCE = 'legacy_import'


def _quantize(value):
    """Return ``value`` at cent precision (ROUND_HALF_UP) and the change."""
    raw = Decimal(str(value))
    qty = raw.quantize(CENTS, rounding=ROUND_HALF_UP)
    return qty, raw - qty


def backfill(apps, schema_editor):
    LedgerEntry = apps.get_model('customers', 'LedgerEntry')
    PayOnAccount = apps.get_model('customers', 'PayOnAccount')
    Customer = apps.get_model('customers', 'Customer')

    for customer in Customer.objects.all().order_by('pk'):
        pre_balance = customer.balance or Decimal('0')
        poas = list(
            PayOnAccount.objects.filter(customer_id=customer.pk)
                                .order_by('created_at', 'id')
        )
        # Quantize every legacy amount up front; sum the *quantized* magnitudes
        # so the opening balance is expressed in the same precision as the
        # ledger (rounding deltas are booked separately below).
        quantized = [(_quantize(p.amount), p) for p in poas]
        debit_sum = sum(q for (q, _d), _p in quantized)

        opening = pre_balance + debit_sum

        running = Decimal('0')
        entries = []

        if opening:
            # Adjust the ledger baseline so the sum reconciles to the
            # pre-migration balance. Positive opening -> customer owes more
            # (credit); negative opening -> overpayment carried forward (debit).
            # Anchor it to the earliest PayOnAccount created_at (or the account
            # creation when there are none) so, in created_at order, the
            # opening row sorts first — a same-instant tie with the first
            # payment is broken by the auto-increment id (it is inserted
            # first), keeping the relative order deterministic.
            opening_ts = poas[0].created_at if poas else \
                (customer.created_at or customer.updated_at)
            entries.append(dict(
                reference=f'{OPENING_REF_PREFIX}{customer.pk}',
                amount=abs(opening),
                direction='credit' if opening > 0 else 'debit',
                entry_type='adjustment',
                method='legacy',
                operator=None,
                sale=None,
                approved=False,
                notes=(
                    f'{PROVENANCE}: opening balance carried into the ledger '
                    f'from pre-ledger activity (invoice sales / prior entries) '
                    f'not represented by PayOnAccount rows.'
                ),
                created_at=opening_ts,
            ))

        for (qty, delta), poa in quantized:
            entries.append(dict(
                reference=f'{POA_REF_PREFIX}{poa.pk}',
                amount=qty,
                direction='debit',
                entry_type='payment',
                method='legacy',
                operator=None,
                sale=None,
                approved=False,
                notes=(
                    f'{PROVENANCE}: migrated from PayOnAccount #{poa.pk}'
                    + (f' — {poa.note}' if poa.note else '')
                ),
                created_at=poa.created_at,
            ))
            if delta:
                # Quantization can only move a value by a sub-cent residue
                # (the two-decimal source is already at precision on any
                # enforcing engine). Book the cent-level difference as an
                # explicit adjustment so no money is silently dropped; any
                # unrepresentable sub-cent rest is folded into the opening
                # balance (which already guarantees reconciliation) and logged
                # loudly for the operator.
                adj_qty = abs(delta).quantize(CENTS, rounding=ROUND_HALF_UP)
                if adj_qty:
                    entries.append(dict(
                        reference=f'legacy-import:rounding:{poa.pk}',
                        amount=adj_qty,
                        direction='debit' if delta > 0 else 'credit',
                        entry_type='adjustment',
                        method='legacy',
                        operator=None,
                        sale=None,
                        approved=False,
                        notes=(
                            f'{PROVENANCE}: rounding adjustment for '
                            f'PayOnAccount #{poa.pk} (quantized {qty} vs raw '
                            f'{Decimal(str(poa.amount))}).'
                        ),
                        created_at=poa.created_at,
                    ))
                else:
                    logger.warning(
                        'ledger backfill: PayOnAccount #%s amount %s quantizes '
                        'to %s; the sub-cent residue is absorbed by the '
                        'opening balance adjustment (customer %s).',
                        poa.pk, Decimal(str(poa.amount)), qty, customer.pk,
                    )

        # Write entries, honouring the append-only running balance. Any entry
        # whose provenance marker already exists is skipped (double-application
        # guard) — but the *next* running balance still continues from that
        # existing row's authoritative value, so re-running stays correct.
        for spec in entries:
            reference = spec['reference']
            existing = LedgerEntry.objects.filter(
                customer_id=customer.pk, reference=reference
            ).first()
            if existing is not None:
                running = existing.running_balance
                continue
            running += spec['amount'] if spec['direction'] == 'credit' \
                else -spec['amount']
            entry = LedgerEntry.objects.create(
                customer_id=customer.pk,
                amount=spec['amount'],
                direction=spec['direction'],
                entry_type=spec['entry_type'],
                method=spec['method'],
                operator=spec['operator'],
                sale=spec['sale'],
                approved=spec['approved'],
                reference=reference,
                notes=spec['notes'],
                running_balance=running,
            )
            # auto_now_add ignores explicit created_at on save(); UPDATE it back
            # to the real payment/event timestamp so the ledger reads in true
            # chronological order.
            LedgerEntry.objects.filter(pk=entry.pk) \
                               .update(created_at=spec['created_at'])

        # Reconcile: the ledger sum must equal the pre-migration balance.
        final_running = running if entries else Decimal('0')
        if final_running != pre_balance:
            raise ValueError(
                f"Ledger backfill failed to reconcile for customer {customer.pk} "
                f"({customer.name!r}): ledger sum {final_running} != "
                f"pre-migration balance {pre_balance}. "
                f"Refusing to write a corrupt ledger."
            )

        # Recompute the cached balance from the ledger (equals pre-migration
        # value by construction, but write it explicitly so balance is always
        # derived from the ledger, never treated as an independent store).
        customer.balance = pre_balance
        customer.save(update_fields=['balance', 'updated_at'])


def unbackfill(apps, schema_editor):
    """Remove the synthetic ledger entries this migration created."""
    LedgerEntry = apps.get_model('customers', 'LedgerEntry')
    LedgerEntry.objects.filter(reference__startswith=POA_REF_PREFIX).delete()
    LedgerEntry.objects.filter(reference__startswith=OPENING_REF_PREFIX).delete()
    LedgerEntry.objects.filter(reference__startswith='legacy-import:rounding:').delete()


class Migration(migrations.Migration):

    dependencies = [
        ('customers', '0002_ledgerentry'),
    ]

    operations = [
        migrations.RunPython(backfill, unbackfill),
    ]
