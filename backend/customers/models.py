from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models


class Customer(models.Model):
    name = models.CharField(max_length=200)
    phone = models.CharField(max_length=50, blank=True, default='')
    balance = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    credit_limit = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    notes = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name

    class Meta:
        constraints = [
            # Backstop against direct-DB corruption. The authoritative guard
            # lives in the ledger service; this only catches out-of-band writes
            # and should never fire for sanctioned operations.
            models.CheckConstraint(
                condition=models.Q(balance__gte=Decimal('-99999999.99')),
                name='customer_balance_floor',
            ),
        ]


class PayOnAccount(models.Model):
    customer = models.ForeignKey(Customer, on_delete=models.CASCADE, related_name='payments')
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)
    note = models.CharField(max_length=200, blank=True, default='')


class LedgerEntry(models.Model):
    """Append-only audit trail backing ``Customer.balance``.

    Every sanctioned balance mutation writes exactly one row here and derives
    ``Customer.balance`` from the running total — ``Customer.balance`` is a
    cache of this ledger, never a primary store. Nothing except
    ``LedgerService`` (customers/services.py) may write to it.
    """

    ENTRY_TYPES = [
        ('payment', 'Payment'),
        ('adjustment', 'Adjustment'),
        ('correction', 'Correction'),
        ('authorization', 'Account Exception / Authorization'),
    ]
    DIRECTIONS = [
        ('credit', 'Credit — customer owes more (balance increases)'),
        ('debit', 'Debit — customer owes less (balance decreases)'),
    ]
    METHODS = ['invoice', 'cash', 'card', 'split', 'other', 'legacy']

    customer = models.ForeignKey(
        Customer, on_delete=models.CASCADE, related_name='ledger_entries'
    )
    # Stored as a positive magnitude; the semantic sign lives in ``direction``.
    amount = models.DecimalField(
        max_digits=12, decimal_places=2, validators=[MinValueValidator(Decimal('0'))]
    )
    direction = models.CharField(max_length=8, choices=DIRECTIONS)
    entry_type = models.CharField(max_length=24, choices=ENTRY_TYPES, default='payment')
    method = models.CharField(
        max_length=16, blank=True, null=True,
        help_text='invoice | cash | card | split | other | legacy',
    )
    # Balance computed as-of this entry (after applying ``amount``/``direction``).
    running_balance = models.DecimalField(max_digits=12, decimal_places=2)
    # Operator who performed the mutation. Null only for legacy/backfilled rows
    # that predate the audit field (there is no real owner to attribute).
    operator = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        null=True, blank=True, related_name='ledger_entries',
    )
    sale = models.ForeignKey(
        'sales.Sale', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='ledger_entries',
    )
    # Explicit account-level exception sign-off required for a negative running
    # balance or for exceeding the customer's credit ceiling. Distinct from the
    # generic invoice manager approval: granting this is guarded by
    # ``can_override_credit`` in the ledger service.
    approved = models.BooleanField(default=False)
    reference = models.CharField(max_length=64, blank=True)  # invoice no, check no, journal ref
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at', 'id']
        constraints = [
            models.CheckConstraint(
                condition=models.Q(direction__in=['credit', 'debit']),
                name='ledger_direction_in',
            ),
            # Negative amounts are structurally impossible; allow unapproved
            # negative *running balances* (overpayment) is a separate guard in
            # the service, but a negative *entry magnitude* is never valid.
            models.CheckConstraint(
                condition=models.Q(amount__gte=Decimal('0')),
                name='ledger_amount_nonnegative',
            ),
        ]
        permissions = [
            (
                'can_override_credit',
                'Can approve a ledger entry that exceeds credit limit or '
                'drives the balance negative',
            ),
        ]

    def __str__(self):
        return f'{self.direction} {self.amount} ({self.customer_id})'
