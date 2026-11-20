from django.contrib import admin

from .models import Register, Session
from accounts.admin_base import RoleAwareModelAdmin


@admin.register(Register)
class RegisterAdmin(RoleAwareModelAdmin):
    list_display = ['name', 'identifier', 'business']
    search_fields = ['name', 'identifier']
    ordering = ['name']


@admin.register(Session)
class SessionAdmin(RoleAwareModelAdmin):
    list_display = ['cashier', 'register', 'business_day', 'status', 'opened_at', 'closed_at', 'sales_total', 'variance']
    list_filter = ['status', 'business_day']
    search_fields = ['notes', 'register__name', 'cashier__username']
    ordering = ['-opened_at']
