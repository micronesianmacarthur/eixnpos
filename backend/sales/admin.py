from django.contrib import admin

from .models import Payment, Sale, SaleLine
from accounts.admin_base import RoleAwareModelAdmin


@admin.register(Sale)
class SaleAdmin(RoleAwareModelAdmin):
    list_display = ['number', 'cashier', 'customer', 'status', 'total', 'created_at']
    list_filter = ['status']
    search_fields = ['number', 'note', 'cashier__username', 'customer__name']
    ordering = ['-created_at']


@admin.register(SaleLine)
class SaleLineAdmin(RoleAwareModelAdmin):
    list_display = ['sale', 'product', 'quantity', 'unit_price', 'line_total']
    list_filter = ['discount_type']
    search_fields = ['product__name', 'sale__number']
    ordering = ['-id']


@admin.register(Payment)
class PaymentAdmin(RoleAwareModelAdmin):
    list_display = ['sale', 'method', 'amount', 'tendered', 'change_due', 'manager_approved', 'created_at']
    list_filter = ['method', 'manager_approved']
    search_fields = ['sale__number']
    ordering = ['-created_at']
