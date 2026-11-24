from django.conf import settings
from django.db import models
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
        on_delete=models.SET_NULL,
        related_name='stock_adjustments',
    )
    old_on_hand = models.IntegerField(null=True, blank=True)
    new_on_hand = models.IntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.product} {self.movement_type} {self.quantity}"
