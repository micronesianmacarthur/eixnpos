from django.conf import settings
from django.db import models, transaction
from django.db.models import F
from django.utils import timezone
from products.models import Product

MOVEMENT_TYPES = [
    ('sale', 'sale'),
    ('receive', 'receive'),
    ('damage', 'damage'),
    ('adjustment', 'adjustment'),
    ('repack_out', 'repack_out'),
    ('repack_in', 'repack_in'),
]

#: Single inventory location in v1. There is no Location model yet; a repack
#: always records the constant below (client-supplied locations are ignored).
REPACK_LOCATION = 'main'

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


class Repack(models.Model):
    """An inventory repack (conversion between two linked flat Products).

    One row per successful conversion, written by ``inventory.services.perform_repack``
    atomically together with its two StockMovement rows (``repack_out`` on the source,
    ``repack_in`` on the target). The record is immutable audit history:

    * ``source_sku`` / ``target_sku`` / ``units_per_pack`` are snapshots taken at
      execution time, so later product renames or SKU edits never rewrite history.
    * All product FKs and both movement FKs are ``PROTECT``: a product that appears
      in a repack (or a movement a repack links) cannot be hard-deleted, so the
      ledger stays reproducible forever.
    * There is no API or admin path to update, delete, or reverse a row. Reversal is a
      new, compensating ``merge`` repack; never an edit of this row.

    The ``OneToOneField``s to the two movements (each with ``unique=True`` implied) are
    the DB form of "FK, unique" linking exactly one pair of movements per repack.
    """

    class Direction(models.TextChoices):
        SPLIT = 'split', 'split'
        MERGE = 'merge', 'merge'

    reference = models.CharField(max_length=16, unique=True)
    location = models.CharField(max_length=32, default=REPACK_LOCATION)
    direction = models.CharField(max_length=8, choices=Direction.choices)
    source_product = models.ForeignKey(
        Product, on_delete=models.PROTECT, related_name='repacks_as_source'
    )
    source_sku = models.CharField(max_length=64)
    source_quantity = models.PositiveIntegerField()
    target_product = models.ForeignKey(
        Product, on_delete=models.PROTECT, related_name='repacks_as_target'
    )
    target_sku = models.CharField(max_length=64)
    target_quantity = models.PositiveIntegerField()
    units_per_pack = models.PositiveIntegerField()
    performed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='repacks_performed'
    )
    note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    source_movement = models.OneToOneField(
        StockMovement, on_delete=models.PROTECT, related_name='repack_source_for'
    )
    target_movement = models.OneToOneField(
        StockMovement, on_delete=models.PROTECT, related_name='repack_target_for'
    )

    class Meta:
        ordering = ['-created_at', '-id']
        constraints = [
            # A repack always moves stock between two distinct products.
            models.CheckConstraint(
                condition=~models.Q(source_product_id=models.F('target_product_id')),
                name='repack_products_differ',
            ),
            # Both quantities are positive whole units; zero- or negative-quantity
            # repacks are rejected at the serializer before any ORM write, and the
            # DB deserves a last-resort guard too.
            models.CheckConstraint(
                condition=models.Q(source_quantity__gte=1),
                name='repack_source_qty_positive',
            ),
            models.CheckConstraint(
                condition=models.Q(target_quantity__gte=1),
                name='repack_target_qty_positive',
            ),
        ]

    def __str__(self):
        return f"{self.reference} {self.direction} {self.source_sku}x{self.source_quantity} -> {self.target_sku}x{self.target_quantity}"
