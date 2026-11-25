from django.conf import settings
from django.db import models, transaction
from django.db.models import F
from products.models import Product

MOVEMENT_TYPES = [('sale','sale'),('receive','receive'),('damage','damage'),('adjustment','adjustment')]

class StockMovement(models.Model):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='movements')
    movement_type = models.CharField(max_length=20, choices=MOVEMENT_TYPES)
    quantity = models.IntegerField()
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
