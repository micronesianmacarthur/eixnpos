from django.contrib import admin

from .models import Receipt, ReceiptLine, Repack, StockMovement
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


@admin.register(Receipt)
class ReceiptAdmin(RoleAwareModelAdmin):
    list_display = ['reference', 'supplier', 'operator', 'received_at', 'created_at']
    list_filter = ['received_at']
    search_fields = ['reference', 'note', 'supplier__name', 'operator__username']
    ordering = ['-received_at']
    raw_id_fields = ['supplier', 'operator']


@admin.register(ReceiptLine)
class ReceiptLineAdmin(RoleAwareModelAdmin):
    list_display = ['receipt', 'product', 'quantity', 'unit_cost']
    search_fields = ['product__name', 'receipt__reference']
    raw_id_fields = ['receipt', 'product']


@admin.register(Repack)
class RepackAdmin(RoleAwareModelAdmin):
    list_display = [
        'reference', 'direction', 'source_sku', 'source_quantity',
        'target_sku', 'target_quantity', 'performed_by', 'created_at',
    ]
    list_filter = ['direction', 'location']
    search_fields = ['reference', 'source_sku', 'target_sku']
    ordering = ['-created_at']
    raw_id_fields = ['source_product', 'target_product', 'performed_by',
                     'source_movement', 'target_movement']

    # Repack rows are immutable audit history (R3.3): they are written only by
    # the inventory repack endpoint, never edited or deleted from the admin.
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
