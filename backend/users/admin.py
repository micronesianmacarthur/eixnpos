from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

User = get_user_model()


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = ['username', 'email', 'first_name', 'last_name', 'role', 'is_staff', 'date_joined']
    list_filter = ['role', 'is_staff', 'is_superuser', 'is_active']
    search_fields = ['username', 'email', 'first_name', 'last_name']
    ordering = ['-date_joined']
    
    fieldsets = list(BaseUserAdmin.fieldsets) + [
        ('Role', {'fields': ('role', 'must_change_password', 'password_changed')}),
    ]
    
    add_fieldsets = list(BaseUserAdmin.add_fieldsets) + [
        ('Role', {'fields': ('role',)}),
    ]