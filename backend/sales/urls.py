from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import SaleViewSet

router = DefaultRouter()
router.register('', SaleViewSet, basename='sale')

urlpatterns = [path('', include(router.urls))]
