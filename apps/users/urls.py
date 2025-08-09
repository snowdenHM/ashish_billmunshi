from django.urls import path, include
from rest_framework.routers import DefaultRouter

from .views import (
    UserProfileViewSet,
    UserPreferenceViewSet,
    UserActivityViewSet,
    UserSessionViewSet,
    UserNotificationViewSet,
    UserSearchView,
    UserDetailView,
    DeleteAccountView
)

# Create router
router = DefaultRouter()

# Register viewsets with custom base names to avoid conflicts
router.register(r'profile', UserProfileViewSet, basename='user-profile')
router.register(r'preferences', UserPreferenceViewSet, basename='user-preferences')
router.register(r'activities', UserActivityViewSet, basename='user-activities')
router.register(r'sessions', UserSessionViewSet, basename='user-sessions')
router.register(r'notifications', UserNotificationViewSet, basename='user-notifications')

app_name = 'users'

urlpatterns = [
    # Router URLs
    path('', include(router.urls)),

    # Custom endpoints
    path('search/', UserSearchView.as_view(), name='user-search'),
    path('details/<int:user_id>/', UserDetailView.as_view(), name='user-detail'),
    path('delete-account/', DeleteAccountView.as_view(), name='delete-account'),
]