from django.urls import path
from . import views

app_name = 'users'

urlpatterns = [
    path('register/', views.register_view, name='register'),
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
    path('list/', views.user_list, name='user_list'),
    path('<int:user_id>/', views.user_detail, name='user_detail'),
    path('<int:user_id>/deactivate/', views.deactivate_user, name='deactivate_user'),
    path('<int:user_id>/activate/', views.activate_user, name='activate_user'),
]