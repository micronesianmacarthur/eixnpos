"""
Sanctioned, append-only ledger for ``Customer.balance``.

``Customer.balance`` is a cached/derived value: every balance mutation must
route through :class:`LedgerService`, which appends exactly one
:class:`LedgerEntry`, computes the running balance, and only then updates
``Customer.balance`` — all inside a single atomic block. Nothing else in the
codebase may write ``Customer.balance`` directly.

Money is ``Decimal`` only, at two-decimal currency precision. Arbitrary
``float`` input is rejected at the boundary because floats cannot represent
currency amounts exactly.
"""
import time
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from django.db import connection, transaction
from django.db.utils import OperationalError

from accounts.roles import has_admin_role
from .models import Customer, LedgerEntry

#: Fixed two-decimal currency precision used by every ledger amount.
CENTS = Decimal('0.01')

# ---------------------------------------------------------------------------
# Bounded write-serialisation for sqlite.
#
# ``select_for_update()`` is a documented no-op on sqlite (the engine cannot
# emit ``FOR UPDATE`` and any naive reader/writer of the balance field is
# unprotected). With the configured backend the *transaction* is still the
# serialisation boundary: sqlite allows exactly one writer at a time, so a
# second concurrent writer only fails transiently with a ``database is
# locked`` ``OperationalError``. Rather than letting that surface as an
# unhandled 500 we bound-retry the losing write (by the time the winner's
# transaction commits the lock is released, so the loser re-reads the fresh
# balance and appends contiguously) and, if contention persists, raise a
# clean :class:`LedgerContentionError` the API layer answers with a retryable
# 503. Postgres/MySQL callers get real row locking and never hit this path.
# ---------------------------------------------------------------------------

#: How many times a losing sqlite writer re-runs its transaction.
LEDGER_WRITE_ATTEMPTS = 5
#: Base backoff (seconds, linear) between retry attempts.
LEDGER_WRITE_RETRY_DELAY = 0.05


def _is_db_lock_contention(exc):
    """True when *exc* is the transient sqlite write-lock ``OperationalError``.

    Anything else (a genuine SQL error) is not contention and must propagate
    unchanged rather than being silently retried.
    """
    return isinstance(exc, OperationalError) and 'locked' in str(exc).lower()


def run_serialized(body, *, attempts=LEDGER_WRITE_ATTEMPTS,
                   delay=LEDGER_WRITE_RETRY_DELAY):
    """Run *body* under bounded retry on sqlite write-lock contention.

    *body* is a zero-arg callable owning its own transaction. When it fails
    with the transient ``database is locked`` error it is re-run (a fresh
    transaction re-reads the committed balance, so concurrent writers apply
    serially). After *attempts* exhausted failures raise
    :class:`LedgerContentionError` so the API layer can answer a clean,
    retryable 503 instead of an unhandled 500.
    """
    for attempt in range(attempts):
        try:
            return body()
        except OperationalError as exc:
            if not _is_db_lock_contention(exc):
                raise
            if attempt + 1 >= attempts:
                raise LedgerContentionError(
                    'The account balance is being updated by another request '
                    'right now; please retry.'
                ) from exc
            time.sleep(delay * (attempt + 1))
    raise AssertionError('unreachable')  # pragma: no cover


class LedgerError(Exception):
    """Base class for ledger mutation errors."""


class NegativeBalanceError(LedgerError):
    """A debit would drive the customer's balance below zero without approval."""


class OverLimitError(LedgerError):
    """A credit would push the running balance above the credit ceiling."""


class LedgerAuthorizationError(LedgerError):
    """A caller requested approval the operator is not authorized to grant."""


class LedgerContentionError(LedgerError):
    """A concurrent balance write held the lock; the operation should be retried.

    Raised by :func:`run_serialized` when a sqlite writer could not acquire the
    write lock across the bounded retry window. The API layer maps it to a
    clean, retryable 503 (never an unhandled 500).
    """


def can_override_credit(user):
    """Return True if *user* may grant the account exception (negative balance / over-limit).

    Fails **closed**: a missing or anonymous operator is never granted
    override authority. The check is deliberately strict — it is granted only
    to authenticated users who are superusers/admins or hold the explicit
    ``customers.can_override_credit`` permission; ``is_manager`` alone is
    insufficient so that manager approval of a sale can never silently unlock
    the credit ceiling. Legacy/backfill writes (which have no real operator)
    bypass the ledger service entirely and write entries directly, so they do
    not need this predicate to return True for a missing user.
    """
    if not user or not getattr(user, 'is_authenticated', False):
        return False
    if getattr(user, 'is_superuser', False):
        return True
    if has_admin_role(user):
        return True
    return user.has_perm('customers.can_override_credit')


def _note_authorization(notes, is_approved, operator):
    """Append an explicit authorising-operator marker to *notes* when approved.

    Account exceptions (a negative-balance advance or an over-limit credit) are
    separate audit events. Naming the operator who granted the exception makes
    the audit trail able to trace *who* signed off, not just that the entry was
    approved.
    """
    if not is_approved:
        return notes
    who = operator.get_username() if operator else '<legacy/unknown>'
    marker = f'[authorized by {who}]'
    return f'{marker} {notes}'.strip()


class LedgerService:
    """The only sanctioned entry points for balance changes."""

    # ------------------------------------------------------------------ utils

    @staticmethod
    def coerce_amount(value) -> Decimal:
        """Return a positive two-decimal ``Decimal``, or raise ``LedgerError``.

        Rejects ``float`` outright (precision), empty/null, non-numeric strings,
        non-finite values, and non-positive amounts.
        """
        if isinstance(value, bool) or value is None:
            raise LedgerError('Amount must be a positive decimal number.')
        if isinstance(value, float):
            raise LedgerError(
                'Amount must be a precise decimal, not a float (e.g. "10.00").'
            )
        try:
            amount = Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError):
            raise LedgerError('Amount must be a valid decimal number.')
        if not amount.is_finite():
            raise LedgerError('Amount must be a finite number.')
        amount = amount.quantize(CENTS, rounding=ROUND_HALF_UP)
        if amount <= 0:
            raise LedgerError('Amount must be a positive decimal number.')
        return amount

    @staticmethod
    def _validate_entry_args(direction, entry_type, method, operator):
        valid_directions = dict(LedgerEntry.DIRECTIONS)
        if direction not in valid_directions:
            raise LedgerError(f'Invalid direction: {direction!r} (expected credit|debit).')
        valid_types = dict(LedgerEntry.ENTRY_TYPES)
        if entry_type not in valid_types:
            raise LedgerError(f'Invalid entry_type: {entry_type!r}.')
        if method is not None and method not in LedgerEntry.METHODS:
            raise LedgerError(f'Invalid method: {method!r}.')
        if not operator:
            raise LedgerError('A ledger entry requires an operator.')

    @staticmethod
    def _project(customer, amount, direction):
        balance = customer.balance or Decimal('0')
        if direction == 'credit':
            return balance + amount
        return balance - amount

    @staticmethod
    def _assert_guards(customer, new_balance, approved, direction):
        if direction == 'debit':
            # A debit (pay-down) lowers the balance; only the negative-balance
            # floor applies. The credit ceiling is never consulted for debits so
            # an over-limit customer can still pay their debt down, and a
            # legacy-balance customer with the default ``credit_limit=0`` is not
            # locked out of settling.
            if new_balance < Decimal('0') and not approved:
                raise NegativeBalanceError(
                    f'This payment would drive the balance below zero '
                    f'({new_balance:.2f}). An approved account exception is required.'
                )
            return
        # direction == 'credit': a credit raises the balance, so only the
        # credit ceiling applies (a credit can never drive the balance negative).
        if new_balance > customer.credit_limit and not approved:
            raise OverLimitError(
                f'This invoice would exceed the credit limit of '
                f'{customer.credit_limit:.2f} (new balance {new_balance:.2f}). '
                f'An approved account exception is required.'
            )

    @staticmethod
    def _assert_approval_authority(operator):
        """Refuse ``approved=True`` unless the operator can actually grant it.

        Defense-in-depth: even if a caller passes ``approved=True`` from a
        spoofable payload, a non-privileged operator cannot self-grant.
        """
        if operator and not can_override_credit(operator):
            raise LedgerAuthorizationError(
                'Approving a balance exception requires admin authority or the '
                'customers.can_override_credit permission.'
            )

    # ------------------------------------------------------------------ entry

    @staticmethod
    def append(customer, amount, *, direction, entry_type='payment',
               method=None, operator=None, sale=None, approved=False,
               reference='', notes='') -> LedgerEntry:
        """Append one ledger entry and re-derive ``Customer.balance`` atomically.

        The customer row is locked (``select_for_update``) so concurrent
        payments cannot produce a stale running balance. On sqlite — where
        ``select_for_update`` is a no-op — the write is additionally wrapped in
        a bounded retry (:func:`run_serialized`) so a losing writer serialises
        instead of surfacing an unhandled 500; a caller that already owns an
        outer transaction performs that serialisation itself. Guards:

        * a debit that would make the balance negative requires ``approved``
        * a credit whose running balance would exceed ``credit_limit`` requires
          ``approved``
        * ``approved`` itself requires override authority (see
          :func:`can_override_credit`)
        """
        def _append_once():
            with transaction.atomic():
                LedgerService._validate_entry_args(direction, entry_type, method, operator)
                entry_amount = LedgerService.coerce_amount(amount)
                is_approved = bool(approved)
                if is_approved:
                    LedgerService._assert_approval_authority(operator)

                # Lock the customer row for the remainder of the transaction.
                locked = Customer.objects.select_for_update().get(pk=customer.pk)
                new_balance = LedgerService._project(locked, entry_amount, direction)
                LedgerService._assert_guards(
                    locked, new_balance, is_approved, direction)

                entry = LedgerEntry.objects.create(
                    customer=locked,
                    amount=entry_amount,
                    direction=direction,
                    entry_type=entry_type,
                    method=method or None,
                    operator=operator,
                    sale=sale,
                    approved=is_approved,
                    reference=reference,
                    notes=notes,
                    running_balance=new_balance,
                )
                # Cache the derived balance last so the ledger row always exists
                # before the balance column is committed.
                locked.balance = new_balance
                locked.save(update_fields=['balance', 'updated_at'])
                return entry

        # Inside a caller-managed ``atomic()`` the write lock is held by the
        # outer transaction, so retrying a nested savepoint cannot help (the
        # caller serialises). Otherwise we own the transaction and retry so a
        # losing sqlite writer applies serially rather than 500ing.
        if connection.in_atomic_block:
            return _append_once()
        return run_serialized(_append_once)

    # ------------------------------------------------------------ domain steps

    @staticmethod
    def record_invoice_payment(customer, amount, *, operator, sale,
                               account_exception=False, notes='') -> LedgerEntry:
        """Credit the customer's account for an invoice sale.

        ``sale`` is recorded on the ledger row (method ``invoice``, reference
        the sale number). ``account_exception`` is the explicit, separate
        sign-off needed when the invoice would breach the credit ceiling; it is
        distinct from the generic ``manager_approved`` flag that merely
        authorizes creating the invoice. The credit ceiling is enforced even
        for manager-approved invoices.

        An ``account_exception`` is an account-authorization event, not an
        ordinary payment, so the row is written as ``entry_type=authorization``
        (and approved) with the authorising operator named in ``notes``, so the
        audit trail can separate an exception from a normal payment.
        """
        notes = _note_authorization(notes, account_exception, operator)
        return LedgerService.append(
            customer, amount, direction='credit',
            entry_type='authorization' if account_exception else 'payment',
            method='invoice', operator=operator, sale=sale,
            approved=account_exception,
            reference=str(getattr(sale, 'number', '')),
            notes=notes,
        )

    @staticmethod
    def record_pay_down_account(customer, amount, *, operator, method=None,
                                approved=False, note='') -> LedgerEntry:
        """Debit the customer's account (customer settles debt).

        ``method`` records how the customer paid (``cash|card|split|other|
        legacy``); it must be one of ``LedgerEntry.METHODS`` and defaults to
        ``cash`` when omitted. A debit that would drive the balance negative is
        rejected by :meth:`append` unless ``approved`` is set (overpayment /
        advance).

        An ``approved`` pay-down is an account-authorization event (it grants
        a negative balance), not an ordinary payment, so it is written as
        ``entry_type=authorization`` with the authorising operator named in
        ``notes`` so audit can separate the exception from a normal payment.
        """
        method = method or 'cash'
        notes = _note_authorization(note, approved, operator)
        return LedgerService.append(
            customer, amount, direction='debit',
            entry_type='authorization' if approved else 'payment',
            method=method, operator=operator, approved=approved, notes=notes,
        )

    # ------------------------------------------------------------- preflight

    @staticmethod
    def preflight(customer, amount, *, direction, approved=False,
                  operator=None) -> Decimal:
        """Validate a prospective entry without writing it.

        Intended to reject a doomed invoice before a payment is processed (so a
        failing credit-limit check does not cost a payment round-trip). The
        authoritative guard is re-applied in :meth:`append`, which is the only
        race-safe check.

        ``approved=True`` calls for override authority up front (via
        :func:`_assert_approval_authority`), so a prospective request that
        :meth:`append` would later reject with a 403 is *not* green-lit here —
        preflight either accepts it the same way append would, or refuses it.
        """
        amount = LedgerService.coerce_amount(amount)
        if approved:
            LedgerService._assert_approval_authority(operator)
        with transaction.atomic():
            customer = Customer.objects.select_for_update().get(pk=customer.pk)
            new_balance = LedgerService._project(customer, amount, direction)
            LedgerService._assert_guards(customer, new_balance, bool(approved), direction)
            return new_balance
