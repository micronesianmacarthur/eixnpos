from django.contrib import admin

from .models import Supplier
from accounts.admin_base import RoleAwareModelAdmin


@admin.register(Supplier)
class SupplierAdmin(RoleAwareModelAdmin):
    list_display = ['name', 'contact_name', 'email', 'phone', 'active', 'created_at']
    list_filter = ['active']
    search_fields = ['name', 'contact_name', 'email', 'phone']
    ordering = ['name']
