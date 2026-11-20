from django.contrib import admin

from .models import Category, Product
from accounts.admin_base import RoleAwareModelAdmin


@admin.register(Category)
class CategoryAdmin(RoleAwareModelAdmin):
    list_display = ['name', 'active', 'created_at', 'updated_at']
    list_filter = ['active']
    search_fields = ['name']
    ordering = ['name']


@admin.register(Product)
class ProductAdmin(RoleAwareModelAdmin):
    list_display = ['name', 'sku', 'category', 'price', 'cost', 'min_stock', 'active', 'quantity']
    list_filter = ['category', 'active']
    search_fields = ['name', 'sku', 'description']
    ordering = ['-created_at']

    @admin.display(description='Quantity')
    def quantity(self, obj):
        return obj.quantity
