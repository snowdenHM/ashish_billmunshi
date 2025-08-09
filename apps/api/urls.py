from django.urls import path, include
from apps.api.views import (
    login_view, register_view, logout_view, password_reset_request_view,
    password_reset_confirm_view, change_password_view, verify_email_view,
    resend_verification_view
)

app_name = 'api'

# Authentication URLs
auth_patterns = [
    path('login/', login_view, name='login'),
    path('register/', register_view, name='register'),
    path('logout/', logout_view, name='logout'),
    path('password-reset/', password_reset_request_view, name='password_reset_request'),
    path('password-reset/confirm/', password_reset_confirm_view, name='password_reset_confirm'),
    path('change-password/', change_password_view, name='change_password'),
    path('verify-email/', verify_email_view, name='verify_email'),
    path('resend-verification/', resend_verification_view, name='resend_verification'),
]

urlpatterns = [
    path('auth/', include(auth_patterns)),
    # Add your other API URLs here
    path('users/', include('apps.users.urls')),
    path('teams/', include('apps.teams.urls')),
    path('subscriptions/', include('apps.subscriptions.urls')),
]