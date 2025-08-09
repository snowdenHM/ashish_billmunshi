from rest_framework import permissions
from rest_framework_api_key.permissions import BaseHasAPIKey
from apps.teams.models import OrganizationAPIKey


class HasUserAPIKey(BaseHasAPIKey):
    """
    Permission class for Organization API Key authentication.
    """
    model = OrganizationAPIKey

    def get_key(self, request):
        """Extract API key from Authorization header"""
        auth_header = request.META.get("HTTP_AUTHORIZATION", "")
        if auth_header.startswith("Bearer "):
            return auth_header[7:]
        return None

    def has_permission(self, request, view):
        """Check if request has valid API key"""
        key = self.get_key(request)
        if not key:
            return False

        try:
            api_key = OrganizationAPIKey.objects.select_related('organization').get(
                key=key,
                is_active=True
            )
        except OrganizationAPIKey.DoesNotExist:
            return False

        # Check if API key is expired
        if api_key.is_expired:
            return False

        # Check IP restrictions
        client_ip = self.get_client_ip(request)
        if not api_key.is_ip_allowed(client_ip):
            return False

        # Set API key and organization context
        request.api_key = api_key
        request.organization = api_key.organization

        # Increment usage count
        api_key.increment_usage()

        return True

    def get_client_ip(self, request):
        """Get client IP address"""
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded_for:
            return x_forwarded_for.split(',')[0].strip()
        return request.META.get('REMOTE_ADDR', '0.0.0.0')


class IsAuthenticatedOrHasUserAPIKey(permissions.BasePermission):
    """
    Permission that allows access to authenticated users or valid API keys
    """

    def has_permission(self, request, view):
        # Check if user is authenticated
        if request.user and request.user.is_authenticated:
            return True

        # Check if request has valid API key
        api_key_permission = HasUserAPIKey()
        return api_key_permission.has_permission(request, view)


class RateLimitPermission(permissions.BasePermission):
    """
    Basic rate limiting permission based on API key or user
    """

    def has_permission(self, request, view):
        # If API key is present, rate limiting is handled by middleware
        if hasattr(request, 'api_key'):
            return True

        # For authenticated users, apply basic rate limiting
        if request.user.is_authenticated:
            # You can implement user-based rate limiting here
            # For now, just allow all authenticated requests
            return True

        return False


class OrganizationAPIPermission(permissions.BasePermission):
    """
    Permission that requires API key to belong to specific organization
    """

    def has_permission(self, request, view):
        if not hasattr(request, 'api_key'):
            return False

        # Get organization ID from URL
        org_id = view.kwargs.get('organization_id') or view.kwargs.get('pk')
        if not org_id:
            return True  # Let other permissions handle this

        # Check if API key belongs to the organization
        return str(request.api_key.organization.id) == str(org_id)


class APIKeyActivePermission(permissions.BasePermission):
    """
    Permission that ensures API key is active and not expired
    """

    def has_permission(self, request, view):
        if not hasattr(request, 'api_key'):
            return True  # Not an API key request

        api_key = request.api_key

        # Check if API key is active
        if not api_key.is_active:
            return False

        # Check if API key is expired
        if api_key.is_expired:
            return False

        return True