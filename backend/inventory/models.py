from django.conf import settings
from django.db import models, transaction
from django.db.models import F
from django.utils import timezone
from products.models import Product

MOVEMENT_TYPES = [('sale','sale'),('receive','receive'),('damage','damage'),('adjustment','adjustment')]

class StockMovement(models.Model):
    # PRODUCT IS PROTECTed: receiving (and all) history must never be
    # cascade-deleted with the product. A product with any movement may only be
    # soft-deactivated (active=False) or have its movements archived, never
    # hard-deleted while history exists (see ProductViewSet.destroy).
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name='movements')
    movement_type = models.CharField(max_length=20, choices=MOVEMENT_TYPES)
    quantity = models.IntegerField()
    # Unit cost of the goods at the time of the movement. Used by refund/void
    # restock entries so the cost basis of goods returning to inventory is
    # preserved for the audit trail.
    cost = models.DecimalField(
        max_digits=12, decimal_places=2, default=0,
        help_text='Unit cost of the goods at the time of the movement.',
    )
    reference = models.CharField(max_length=128, blank=True)
    note = models.TextField(blank=True)
    # Audit trail fields for operator-stamped adjustments (damage / count
    # corrections). They stay null for automatic ledger rows (sales, receives)
    # and are populated only when a staff user records an adjustment.
    adjusted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='stock_adjustments',
    )
    old_on_hand = models.IntegerField(null=True, blank=True)
    new_on_hand = models.IntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def save(self, *args, **kwargs):
        with transaction.atomic():
            super().save(*args, **kwargs)
            if not self.product.non_stock:
                # Keep the materialized Product.quantity in lock-step with the
                # ledger, atomically, in the same transaction as the movement
                # row. ``F()`` makes this a single conditional UPDATE (no
                # read-modify-write on a stale value); the ``quantity >= 0``
                # CHECK constraint turns a below-zero result into an
                # IntegrityError, which writers translate to a clean 400/409.
                Product.objects.filter(pk=self.product_id).update(
                    quantity=F('quantity') + self.quantity
                )

    def delete(self, *args, **kwargs):
        # To delete, the product must still have at least ``|quantity|`` units
        # available to take back (no helper deleted row may remove stock the
        # ledger relies on); run fully inside the delete's own transaction.
        with transaction.atomic():
            result = super().delete(*args, **kwargs)
            if not self.product.non_stock:
                Product.objects.filter(pk=self.product_id).update(
                    quantity=F('quantity') - self.quantity
                )
            return result

    def __str__(self):
        return f"{self.product} {self.movement_type} {self.quantity}"


class CostLayer(models.Model):
    """A received-batch cost layer for inventory valuation.

    Each row represents one batch of a product received at a given unit cost,
    so weighted-average cost can be derived from the layers without reading
    the product's *current* ``Product.cost`` (which would restate prior
    periods whenever the cost is edited). ``source_reference`` ties the layer
    back to the originating receive (e.g. a StockMovement reference or a
    future receipt number).
    """

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='cost_layers')
    quantity = models.IntegerField()
    unit_cost = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    received_at = models.DateTimeField(default=timezone.now)
    source_reference = models.CharField(max_length=128, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['received_at']

    def __str__(self):
        return f"{self.product} x{self.quantity} @ {self.unit_cost}"


class Receipt(models.Model):
    """An inventory receiving audit record.

    Captures who received stock (``operator``), from which supplier
    (``supplier``), and when (``received_at``). ``reference`` holds an
    external identifier (PO number, delivery note) and ``note`` free text.
    The goods received are recorded on the related :class:`ReceiptLine`
    rows.
    """

    supplier = models.ForeignKey('suppliers.Supplier', on_delete=models.PROTECT, related_name='receipts')
    received_at = models.DateTimeField(default=timezone.now)
    operator = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='receipts')
    reference = models.CharField(max_length=128, blank=True)  # PO number, delivery note, etc.
    note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-received_at']

    def __str__(self):
        return f"Receipt {self.reference or self.pk} ({self.supplier})"


class ReceiptLine(models.Model):
    """One product line on a :class:`Receipt`.

    ``quantity`` x ``unit_cost`` = line total; no line total is stored, it is
    always computed from the two fields.
    """

    receipt = models.ForeignKey(Receipt, on_delete=models.CASCADE, related_name='lines')
    product = models.ForeignKey('products.Product', on_delete=models.PROTECT, related_name='receipt_lines')
    quantity = models.IntegerField()
    unit_cost = models.DecimalField(max_digits=12, decimal_places=2)

    class Meta:
        ordering = ['receipt', 'id']

    def __str__(self):
        return f"{self.product} x{self.quantity} @ {self.unit_cost}"
