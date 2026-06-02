from django.urls import path
from . import views

app_name = 'users'

urlpatterns = [
    # Auth
    path('register/', views.register_view, name='register'),
    path('login/',    views.login_view,    name='login'),
    path('logout/',   views.logout_view,   name='logout'),
    path('verify-email/<str:token>/', views.verify_email, name='verify_email'),
    path('resend-verification/', views.resend_verification, name='resend_verification'),
    path('password-reset/', views.password_reset, name='password_reset'),
    path('password-reset/done/', views.password_reset_done, name='password_reset_done'),
    path('reset-password/<str:token>/', views.password_reset_confirm, name='password_reset_confirm'),
    path('reset-password/complete/', views.password_reset_complete, name='password_reset_complete'),
    path('profile/', views.profile, name='profile'),

    # Newspaper sources (admin only)
    path('sources/',                          views.user_list,       name='user_list'),
    path('sources/<int:user_id>/',            views.user_detail,     name='user_detail'),
    path('sources/<int:user_id>/deactivate/', views.deactivate_user, name='deactivate_user'),
    path('sources/<int:user_id>/activate/',   views.activate_user,   name='activate_user'),

    # Agency accounts (admin only)
    path('agencies/',                          views.agency_list,       name='agency_list'),
    path('agencies/create/',                   views.create_agency,     name='create_agency'),
    path('agencies/<int:user_id>/deactivate/', views.deactivate_agency, name='deactivate_agency'),
    path('agencies/<int:user_id>/activate/',   views.activate_agency,   name='activate_agency'),
    path('activate-account/<str:token>/',      views.activate_agency_account, name='activate_agency_account'),
]
