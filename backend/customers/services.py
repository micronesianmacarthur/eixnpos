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
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from django.db import transaction

from accounts.roles import has_admin_role
from .models import Customer, LedgerEntry

#: Fixed two-decimal currency precision used by every ledger amount.
CENTS = Decimal('0.01')


class LedgerError(Exception):
    """Base class for ledger mutation errors."""


class NegativeBalanceError(LedgerError):
    """A debit would drive the customer's balance below zero without approval."""


class OverLimitError(LedgerError):
    """A credit would push the running balance above the credit ceiling."""


class LedgerAuthorizationError(LedgerError):
    """A caller requested approval the operator is not authorized to grant."""


def can_override_credit(user):
    """Return True if *user* may grant the account exception (negative balance / over-limit).

    The override is a distinct, stricter authorization than plain manager
    approval of an invoice. It is granted to admins and to users holding the
    explicit ``customers.can_override_credit`` permission; ``is_manager`` alone
    is deliberately insufficient so that manager approval of a sale can never
    silently unlock the credit ceiling.
    """
    if not user or not getattr(user, 'is_authenticated', False):
        # Null operator is the legacy/backfill path (no real owner to check).
        return True
    if getattr(user, 'is_superuser', False):
        return True
    if has_admin_role(user):
        return True
    return user.has_perm('customers.can_override_credit')


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
    def _assert_guards(customer, new_balance, approved):
        if new_balance < Decimal('0') and not approved:
            raise NegativeBalanceError(
                f'This payment would drive the balance below zero '
                f'({new_balance:.2f}). An approved account exception is required.'
            )
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
        payments cannot produce a stale running balance. Guards:

        * a debit that would make the balance negative requires ``approved``
        * a credit whose running balance would exceed ``credit_limit`` requires
          ``approved``
        * ``approved`` itself requires override authority (see
          :func:`can_override_credit`)
        """
        with transaction.atomic():
            LedgerService._validate_entry_args(direction, entry_type, method, operator)
            amount = LedgerService.coerce_amount(amount)
            approved = bool(approved)
            if approved:
                LedgerService._assert_approval_authority(operator)

            # Lock the customer row for the remainder of the transaction.
            customer = Customer.objects.select_for_update().get(pk=customer.pk)
            new_balance = LedgerService._project(customer, amount, direction)
            LedgerService._assert_guards(customer, new_balance, approved)

            entry = LedgerEntry.objects.create(
                customer=customer,
                amount=amount,
                direction=direction,
                entry_type=entry_type,
                method=method or None,
                operator=operator,
                sale=sale,
                approved=approved,
                reference=reference,
                notes=notes,
                running_balance=new_balance,
            )
            # Cache the derived balance last so the ledger row always exists
            # before the balance column is committed.
            customer.balance = new_balance
            customer.save(update_fields=['balance', 'updated_at'])
            return entry

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
        """
        return LedgerService.append(
            customer, amount, direction='credit', entry_type='payment',
            method='invoice', operator=operator, sale=sale,
            approved=account_exception,
            reference=str(getattr(sale, 'number', '')),
            notes=notes,
        )

    @staticmethod
    def record_pay_down_account(customer, amount, *, operator,
                                approved=False, note='') -> LedgerEntry:
        """Debit the customer's account (customer settles debt).

        A debit that would drive the balance negative is rejected by
        :meth:`append` unless ``approved`` is set (overpayment / advance).
        """
        return LedgerService.append(
            customer, amount, direction='debit', entry_type='payment',
            method='cash', operator=operator, approved=approved, notes=note,
        )

    # ------------------------------------------------------------- preflight

    @staticmethod
    def preflight(customer, amount, *, direction, approved=False) -> Decimal:
        """Validate a prospective entry without writing it.

        Intended to reject a doomed invoice before a payment is processed (so a
        failing credit-limit check does not cost a payment round-trip). The
        authoritative guard is re-applied in :meth:`append`, which is the only
        race-safe check.
        """
        amount = LedgerService.coerce_amount(amount)
        with transaction.atomic():
            customer = Customer.objects.select_for_update().get(pk=customer.pk)
            new_balance = LedgerService._project(customer, amount, direction)
            LedgerService._assert_guards(customer, new_balance, bool(approved))
            return new_balance
