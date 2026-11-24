from django.db import models
from products.models import Product

MOVEMENT_TYPES = [('sale','sale'),('receive','receive'),('damage','damage'),('adjustment','adjustment')]

class StockMovement(models.Model):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='movements')
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
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.product} {self.movement_type} {self.quantity}"
