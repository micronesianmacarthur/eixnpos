from django.db import models
from django.contrib.auth import get_user_model
from decimal import Decimal

User = get_user_model()


class Sale(models.Model):
    STATUS_OPEN = 'open'
    STATUS_COMPLETED = 'completed'
    STATUS_VOIDED = 'voided'
    STATUS_CHOICES = [
        (STATUS_OPEN, 'Open'),
        (STATUS_COMPLETED, 'Completed'),
        (STATUS_VOIDED, 'Voided'),
    ]

    number = models.PositiveIntegerField(unique=True, editable=False)
    cashier = models.ForeignKey(User, on_delete=models.PROTECT, related_name='sales')
    customer = models.ForeignKey('customers.Customer', null=True, blank=True, on_delete=models.SET_NULL, related_name='sales')
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_OPEN)
    subtotal = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    discount_total = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']

    def save(self, *args, **kwargs):
        if not self.number:
            last = Sale.objects.order_by('-number').first()
            self.number = (last.number + 1) if last else 1
        super().save(*args, **kwargs)

    def recompute(self):
        subtotal = Decimal('0')
        discount_total = Decimal('0')
        for line in self.lines.all():
            line.line_total = (line.unit_price * line.quantity) - line.discount_amount
            line.save(update_fields=['line_total'])
            subtotal += line.unit_price * line.quantity
            discount_total += line.discount_amount
        self.subtotal = subtotal
        self.discount_total = discount_total
        self.total = subtotal - discount_total
        self.save(update_fields=['subtotal', 'discount_total', 'total'])

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
    created_at = models.DateTimeField(auto_now_add=True)
