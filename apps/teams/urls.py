from django.urls import path, include
from rest_framework.routers import DefaultRouter
from rest_framework_nested import routers

from .views import (
    OrganizationViewSet,
    InvitationViewSet,
    OrganizationAPIKeyViewSet,
    RoleViewSet,
    OrganizationMemberViewSet,
    InvitationResponseView,
    CheckSlugAvailabilityView,
    SearchUsersView
)

# Create the main router
router = DefaultRouter()
router.register(r'organizations', OrganizationViewSet, basename='organization')
router.register(r'roles', RoleViewSet, basename='role')

# Create nested routers for organization-specific resources
organizations_router = routers.NestedDefaultRouter(
    router,
    r'organizations',
    lookup='organization'
)

# Register nested routes
organizations_router.register(
    r'invitations',
    InvitationViewSet,
    basename='organization-invitations'
)

organizations_router.register(
    r'api-keys',
    OrganizationAPIKeyViewSet,
    basename='organization-api-keys'
)

organizations_router.register(
    r'members',
    OrganizationMemberViewSet,
    basename='organization-members'
)

app_name = 'teams'

urlpatterns = [
    # Main router URLs
    path('', include(router.urls)),

    # Nested router URLs
    path('', include(organizations_router.urls)),

    # Standalone URLs
    path(
        'invitations/<uuid:token>/respond/',
        InvitationResponseView.as_view(),
        name='invitation-respond'
    ),
    path(
        'organizations/check-slug/',
        CheckSlugAvailabilityView.as_view(),
        name='check-slug-availability'
    ),
    path(
        'users/search/',
        SearchUsersView.as_view(),
        name='search-users'
    ),
]