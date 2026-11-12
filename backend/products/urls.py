from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import ProductViewSet, CategoryViewSet

product_router = DefaultRouter()
product_router.register(r'', ProductViewSet, basename='product')

category_router = DefaultRouter()
category_router.register(r'categories', CategoryViewSet, basename='category')

urlpatterns = [
    path('', include(category_router.urls)),
    path('', include(product_router.urls)),
]
