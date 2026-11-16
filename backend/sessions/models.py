from django.db import models
from django.db.models import Q, UniqueConstraint
from django.contrib.auth import get_user_model
from decimal import Decimal

User = get_user_model()

class Session(models.Model):
    STATUS_OPEN = 'open'
    STATUS_CLOSED = 'closed'
    STATUS_CHOICES = [(STATUS_OPEN,'Open'),(STATUS_CLOSED,'Closed')]
    cashier = models.ForeignKey(User, on_delete=models.PROTECT, related_name='sessions')
    starting_cash = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_OPEN)
    opened_at = models.DateTimeField(auto_now_add=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    cash_counts = models.JSONField(default=dict, blank=True)
    cash_removed = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    expected_cash = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    counted_cash = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    variance = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    sales_total = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    notes = models.TextField(blank=True, default='')

    class Meta:
        ordering = ['-opened_at']
        constraints = [
            # Enforce the single-open-shift rule at the DB level so the check is
            # atomic — only one `open` session can exist across all cashiers.
            UniqueConstraint(
                fields=['status'],
                condition=Q(status='open'),
                name='only_one_open_session',
            ),
        ]

    def __str__(self):
        return f'Session #{self.id}'