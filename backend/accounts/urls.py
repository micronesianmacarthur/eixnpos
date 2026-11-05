from django.urls import path
from . import views
from business import views as bviews

urlpatterns = [
    path('login/', views.login_view),
    path('logout/', views.logout_view),
    path('me/', views.me_view),
    path('csrf/', views.csrf_view),
    path('change-password/', views.change_password_view),
]
