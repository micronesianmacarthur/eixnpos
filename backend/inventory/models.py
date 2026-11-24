from django.conf import settings
from django.db import models
from django.utils import timezone
from products.models import Product

MOVEMENT_TYPES = [('sale','sale'),('receive','receive'),('damage','damage'),('adjustment','adjustment')]

class StockMovement(models.Model):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='movements')
    movement_type = models.CharField(max_length=20, choices=MOVEMENT_TYPES)
    quantity = models.IntegerField()
    reference = models.CharField(max_length=128, blank=True)
    note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

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
