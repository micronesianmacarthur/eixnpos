from django.db import models
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
            last = Sale.objects.order_by('-number').first()
            self.number = (last.number + 1) if last else 1
        super().save(*args, **kwargs)

    def recompute(self, tax_rate: Decimal = Decimal('0')):
        """Recalculate sale totals from line items.
        
        Args:
            tax_rate: Tax rate as a decimal (e.g., Decimal('0.10') for 10%)
        """
        subtotal = Decimal('0')
        discount_total = Decimal('0')
        for line in self.lines.all():
            line.line_total = (line.unit_price * line.quantity) - line.discount_amount
            line.save(update_fields=['line_total'])
            # Update in-memory object to reflect saved value
            line.refresh_from_db(fields=['line_total'])
            subtotal += line.unit_price * line.quantity
            discount_total += line.discount_amount
        
        self.subtotal = subtotal
        self.discount_total = discount_total
        taxable_amount = subtotal - discount_total
        self.tax_total = (taxable_amount * tax_rate).quantize(Decimal('0.01'))
        self.total = taxable_amount + self.tax_total
        self.save(update_fields=['subtotal', 'discount_total', 'tax_total', 'total'])

    def is_open(self) -> bool:
        """Check if sale is in a modifiable state."""
        return self.status in (self.STATUS_PENDING, self.STATUS_OPEN)

    def __str__(self):
        return f'Sale #{self.number}'


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
    # Neutralisation fields: a payment that has been voided (or whose full
    # amount has been refunded) no longer counts as a captured sale payment.
    # A void does not delete the row — it keeps the audit history while the
    # flag stops the amount from contributing to balances/paid totals.
    voided = models.BooleanField(
        default=False, db_index=True,
        help_text='Payment has been voided (neutralized). It no longer counts '
                  'as a captured sale payment.',
    )
    voided_at = models.DateTimeField(
        null=True, blank=True,
        help_text='Timestamp the payment was voided.',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def remaining_refundable(self) -> Decimal:
        """Money still refundable against this captured payment.

        The amount that was captured minus the cumulative refunds already
        booked against it. Zero once fully refunded.
        """
        refunded = self.refunds.aggregate(
            total=models.Sum('amount')
        )['total'] or Decimal('0')
        return max(Decimal('0'), self.amount - refunded)

    def is_fully_refunded(self) -> bool:
        return self.remaining_refundable() <= Decimal('0')


class Refund(models.Model):
    """Authoritative record of a (partial) refund and its reversing entries.

    One row per refunded line item (or per line/payment segment when a single
    line's refund spans multiple captured payments). Writing a Refund row is
    the bookkeeping entry for: the tender returned, the AR/customer-balance
    credit (for invoice payments), and the inventory restock movement.
    """

    sale = models.ForeignKey(
        Sale, on_delete=models.CASCADE, related_name='refunds',
        help_text='The sale the refund applies to.',
    )
    line = models.ForeignKey(
        SaleLine, on_delete=models.CASCADE, related_name='refunds',
        help_text='The specific sale line being refunded.',
    )
    payment = models.ForeignKey(
        Payment, on_delete=models.CASCADE, related_name='refunds',
        help_text='The captured payment this refund reverses (or a portion of).',
    )
    quantity = models.IntegerField(
        default=0,
        help_text='Units of the line returned. <= line.quantity.',
    )
    amount = models.DecimalField(
        max_digits=12, decimal_places=2,
        help_text='Refunded money. > 0, <= remaining unrefunded capacity of '
                  'the line and of the payment.',
    )
    method = models.CharField(
        max_length=16,
        choices=[(m, n) for m, n in Payment.METHOD_CHOICES],
        help_text='Tender method this refund returns. For cash this is the '
                  'tendered cash being returned; for card/invoice it is the '
                  'method being reversed.',
    )
    reason = models.TextField(blank=True)
    manager_approved = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['sale', 'created_at']),
            models.Index(fields=['payment', 'created_at']),
        ]

    def __str__(self):
        return f'Refund {self.amount} on {self.sale}'

