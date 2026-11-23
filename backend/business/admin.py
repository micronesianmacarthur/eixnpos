from django.contrib import admin

from .models import BusinessInfo
from accounts.admin_base import RoleAwareModelAdmin


@admin.register(BusinessInfo)
class BusinessInfoAdmin(RoleAwareModelAdmin):
    list_display = ['company_name', 'phone', 'email', 'is_complete']
    search_fields = ['company_name', 'phone', 'email', 'website']
    ordering = ['company_name']

    @admin.display(boolean=True, description='Complete')
    def is_complete(self, obj):
        return obj.is_complete()
