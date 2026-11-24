from django.db import models
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
    reference = models.CharField(max_length=128, blank=True)
    note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.product} {self.movement_type} {self.quantity}"
