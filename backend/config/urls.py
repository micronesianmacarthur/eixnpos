from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/auth/', include('accounts.urls')),
    path('api/business/', include('business.urls')),
    path('api/customers/', include('customers.urls')),
]
