from django.urls import path

from . import views

urlpatterns = [
    path(
        '',
        views.composite_view,
        name='dashboard-composite',
    ),
    path('sales/today/', views.today_view, name='dashboard-sales-today'),
    path('sales/period/', views.period_view, name='dashboard-sales-period'),
    path('sales/top/', views.top_view, name='dashboard-sales-top'),
    path('sales/monthly/', views.monthly_view, name='dashboard-sales-monthly'),
    path('sessions/current/', views.session_view, name='dashboard-sessions-current'),
    path(
        'inventory/low-stock/',
        views.low_stock_view,
        name='dashboard-inventory-low-stock',
    ),
    path('backup/', views.backup_view, name='dashboard-backup'),
]
