from django.contrib import admin

from .models import StockMovement
from accounts.admin_base import RoleAwareModelAdmin


@admin.register(StockMovement)
class StockMovementAdmin(RoleAwareModelAdmin):
    list_display = ['product', 'movement_type', 'quantity', 'created_at']
    list_filter = ['movement_type']
    search_fields = ['reference', 'note', 'product__name']
    ordering = ['-created_at']

    # The stock ledger is an append-only audit trail: it is written only by the
    # inventory/sales endpoints. Exposing it as changeable/deletable in the
    # admin would let an operator rewrite on_hand or erase the audit trail
    # outside every endpoint guard, so the admin surface is read-only.
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
