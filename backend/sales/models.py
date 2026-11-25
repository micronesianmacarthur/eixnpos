from django.db import models, IntegrityError
from django.contrib.auth import get_user_model
from django.utils import timezone
from decimal import Decimal

User = get_user_model()


class Sale(models.Model):
    STATUS_PENDING = 'pending'
    STATUS_OPEN = 'open'
    STATUS_COMPLETED = 'completed'
    STATUS_VOIDED = 'voided'
    STATUS_REFUNDED = 'refunded'
    STATUS_CHOICES = [
        (STATUS_PENDING, 'Pending'),
        (STATUS_OPEN, 'Open'),
        (STATUS_COMPLETED, 'Completed'),
        (STATUS_VOIDED, 'Voided'),
        (STATUS_REFUNDED, 'Refunded'),
    ]

    number = models.PositiveIntegerField(unique=True, editable=False)
    cashier = models.ForeignKey(User, on_delete=models.PROTECT, related_name='sales')
    # The POS session (register/day shift) this sale belongs to. Set
    # server-side at sale creation from the cashier's open session; NULL only
    # when there is no open session for the cashier's register/business day
    # (server-side session enforcement is absent on this line). Close/summary/
    # Z-report totals attribute a sale through this link, falling back to the
    # legacy time-window rule only for rows where it is NULL (pre-migration
    # history).
    session = models.ForeignKey(
        'pos_sessions.Session', related_name='sales',
        null=True, blank=True, on_delete=models.PROTECT,
    )
    customer = models.ForeignKey('customers.Customer', null=True, blank=True, on_delete=models.SET_NULL, related_name='sales')
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_OPEN)
    subtotal = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    discount_total = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    tax_total = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    refunded_at = models.DateTimeField(null=True, blank=True)
    note = models.TextField(blank=True)
    needs_review = models.BooleanField(default=False)

    class Meta:
        ordering = ['-created_at']

    def save(self, *args, **kwargs):
        if not self.number:
            from django.db import transaction as db_transaction
            from django.db.models import Max
            # Serialize sale-number allocation. Lock the singleton counter row
            # (SaleNumberCounter) before reading/incrementing it so two concurrent
            # allocations can never observe the same current value and issue the
            # same sale number. This replaces the racy MAX(number)+1 read-then-
            # write that double-posted numbers under concurrency. The counter
            # increment and the sale INSERT share one atomic block so a failed
            # insert also rolls the counter back.
            with db_transaction.atomic():
                # Race-safe fallback for a missing row (e.g. a TransactionTestCase
                # flush dropping the migration-seeded singleton, or a DB first-run
                # mid-flight): seed it from the current MAX(number) inside the
                # locked block instead of failing every Sale.save() with
                # DoesNotExist. get_or_create is race-safe with select_for_update:
                # if a concurrent first INSERT wins pk=1, we catch the IntegrityError
                # it raises and retry the locked get against the surviving row.
                try:
                    with db_transaction.atomic():
                        counter, _created = (
                            SaleNumberCounter.objects.select_for_update().get_or_create(
                                pk=1,
                                defaults={
                                    'current': Sale.objects.aggregate(
                                        m=Max('number')
                                    )['m'] or 0,
                                },
                            )
                        )
                except IntegrityError:
                    # A concurrent first INSERT won the row; retry the locked get
                    # so we serialize on the existing singleton instead of 500ing.
                    counter = SaleNumberCounter.objects.select_for_update().get(pk=1)
                counter.current += 1
                self.number = counter.current
                counter.save(update_fields=['current'])
                super().save(*args, **kwargs)
        else:
            super().save(*args, **kwargs)

    def recompute(self, tax_rate: Decimal = Decimal('0')):
        """Recalculate sale totals from line items.
        
        Args:
            tax_rate: Tax rate as a decimal (e.g., Decimal('0.10') for 10%)
        """
        from django.db import transaction as db_transaction
        with db_transaction.atomic():
            # Serialize cart recomputation on this sale: lock the sale row before
            # reading lines/writing totals so two concurrent cart edits cannot
            # lose each other's line_total / sale-total updates.
            locked = Sale.objects.select_for_update().get(pk=self.pk)
            subtotal = Decimal('0')
            discount_total = Decimal('0')
            for line in locked.lines.all():
                line.line_total = (line.unit_price * line.quantity) - line.discount_amount
                line.save(update_fields=['line_total'])
                # Update in-memory object to reflect saved value
                line.refresh_from_db(fields=['line_total'])
                subtotal += line.unit_price * line.quantity
                discount_total += line.discount_amount

            locked.subtotal = subtotal
            locked.discount_total = discount_total
            taxable_amount = subtotal - discount_total
            locked.tax_total = (taxable_amount * tax_rate).quantize(Decimal('0.01'))
            locked.total = taxable_amount + locked.tax_total
            locked.save(update_fields=['subtotal', 'discount_total', 'tax_total', 'total'])

        # The recomputation happened on the locked `locked` instance, not on
        # `self`; refresh `self` so callers reading `self.total` / subtotal /
        # tax_total after recompute() see the persisted values rather than the
        # stale pre-lock snapshot.
        if self.pk:
            self.refresh_from_db(
                fields=['subtotal', 'discount_total', 'tax_total', 'total']
            )

    def is_open(self) -> bool:
        """Check if sale is in a modifiable state."""
        return self.status in (self.STATUS_PENDING, self.STATUS_OPEN)

    def __str__(self):
        return f'Sale #{self.number}'


class SaleNumberCounter(models.Model):
    """Singleton row holding the next-sale-number source of truth.

    Number allocation (`Sale.save()`) `select_for_update`s this single row
    (only pk=1 is ever allowed, enforced by ``single_sale_number_counter``) so
    concurrent allocations are serialized and can never hand out the same
    number. Seeded from the existing max sale number by a data migration.
    """

    current = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(pk=1),
                name='single_sale_number_counter',
                # Guards the singleton invariant at the DB level even if a
                # caller bypasses the get(pk=1) fast path.
            )
        ]


class SaleLine(models.Model):
    DISCOUNT_NONE = 'none'
    DISCOUNT_PERCENT = 'percent'
    DISCOUNT_FIXED = 'fixed'
    DISCOUNT_CHOICES = [
        (DISCOUNT_NONE, 'None'),
        (DISCOUNT_PERCENT, 'Percent'),
        (DISCOUNT_FIXED, 'Fixed'),
    ]

    sale = models.ForeignKey(Sale, on_delete=models.CASCADE, related_name='lines')
    product = models.ForeignKey('products.Product', on_delete=models.PROTECT)
    quantity = models.IntegerField(default=1)
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)
    discount_type = models.CharField(max_length=16, choices=DISCOUNT_CHOICES, default=DISCOUNT_NONE)
    discount_value = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    discount_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    discount_authorizer = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='authorized_discounts')
    line_total = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    def __str__(self):
        return f'{self.product} x{self.quantity}'


class Payment(models.Model):
    METHOD_CASH = 'cash'
    METHOD_CARD = 'card'
    METHOD_SPLIT = 'split'
    METHOD_INVOICE = 'invoice'
    METHOD_CHOICES = [
        (METHOD_CASH, 'Cash'),
        (METHOD_CARD, 'Card'),
        (METHOD_SPLIT, 'Split'),
        (METHOD_INVOICE, 'Invoice'),
    ]

    sale = models.ForeignKey(Sale, on_delete=models.CASCADE, related_name='payments')
    method = models.CharField(max_length=16, choices=METHOD_CHOICES)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    tendered = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    change_due = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    manager_approved = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
