from django.urls import path

from . import views

urlpatterns = [
    path('pricing/', views.pricing_settings_view, name='pricing-settings'),
]
