from django.contrib import admin

from .models import Customer, PayOnAccount
from accounts.admin_base import RoleAwareModelAdmin


@admin.register(Customer)
class CustomerAdmin(RoleAwareModelAdmin):
    list_display = ['name', 'phone', 'balance', 'credit_limit', 'created_at', 'updated_at']
    search_fields = ['name', 'phone', 'notes']
    ordering = ['-created_at']


@admin.register(PayOnAccount)
class PayOnAccountAdmin(RoleAwareModelAdmin):
    list_display = ['customer', 'amount', 'created_at']
    list_filter = ['created_at']
    search_fields = ['note', 'customer__name']
    ordering = ['-created_at']
