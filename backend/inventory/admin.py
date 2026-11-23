from django.contrib import admin

from .models import StockMovement
from accounts.admin_base import RoleAwareModelAdmin


@admin.register(StockMovement)
class StockMovementAdmin(RoleAwareModelAdmin):
    list_display = ['product', 'movement_type', 'quantity', 'created_at']
    list_filter = ['movement_type']
    search_fields = ['reference', 'note', 'product__name']
    ordering = ['-created_at']
