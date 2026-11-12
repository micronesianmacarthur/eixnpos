from django.urls import path, include
from rest_framework.routers import DefaultRouter
from . import views
from business import views as bviews

router = DefaultRouter()
router.register(r'users', views.UserViewSet, basename='user')

urlpatterns = [
    path('login/', views.login_view),
    path('logout/', views.logout_view),
    path('me/', views.me_view),
    path('csrf/', views.csrf_view),
    path('change-password/', views.change_password_view),
    path('business-info/', views.business_info_view),
    path('admin-dashboard/', views.admin_dashboard_view),
    path('', include(router.urls)),
]