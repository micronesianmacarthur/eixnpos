from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import ProductViewSet

product_router = DefaultRouter()
product_router.register(r'', ProductViewSet, basename='product')

urlpatterns = [
    path('', include(product_router.urls)),
]
