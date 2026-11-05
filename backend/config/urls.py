from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/auth/', include('accounts.urls')),
    path('api/business/', include('business.urls')),
    path('api/suppliers/', include('suppliers.urls')),
    path('api/products/', include('products.urls')),
    path('api/inventory/', include('inventory.urls')),
]
