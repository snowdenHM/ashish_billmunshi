from rest_framework import permissions
from rest_framework_api_key.permissions import BaseHasAPIKey

from .models import Organization, OrganizationAPIKey, Role


# ---------- Helpers ----------

def _get_org_from_view(view):
    """
    Try to pull an organization id from typical URL kwargs.
    Returns org_id (str/int) or None.
    """
    return view.kwargs.get("organization_id") or view.kwargs.get("org_id") or view.kwargs.get("pk")


def _get_organization(org_id):
    try:
        return Organization.objects.get(id=org_id)
    except Organization.DoesNotExist:
        return None


def _org_from_obj(obj):
    """
    Return Organization instance if obj has one or is one, else None.
    """
    if isinstance(obj, Organization):
        return obj
    if hasattr(obj, "organization") and obj.organization is not None:
        return obj.organization
    return None


def _is_user_owner_or_admin(org, user):
    role = org.get_user_role(user)
    if not role:
        return False
    # Accept either enum or string-stored names.
    name = getattr(role, "name", str(role))
    return name in {Role.OWNER, Role.ADMIN} if hasattr(Role, "OWNER") else name in {"OWNER", "ADMIN"}


# ---------- Permissions ----------

class IsOrganizationMember(permissions.BasePermission):
    """
    Allow if the authenticated user is a member of the organization.
    """

    def has_permission(self, request, view):
        if not request.user.is_authenticated:
            return False

        org_id = _get_org_from_view(view)
        if not org_id:
            return False

        organization = _get_organization(org_id)
        return bool(organization and organization.has_member(request.user))

    def has_object_permission(self, request, view, obj):
        organization = _org_from_obj(obj)
        return bool(organization and organization.has_member(request.user))


class IsOrganizationOwnerOrAdmin(permissions.BasePermission):
    """
    Allow if the user is OWNER or ADMIN of the organization.
    """

    def has_permission(self, request, view):
        if not request.user.is_authenticated:
            return False

        org_id = _get_org_from_view(view)
        if not org_id:
            return False

        organization = _get_organization(org_id)
        return bool(organization and _is_user_owner_or_admin(organization, request.user))

    def has_object_permission(self, request, view, obj):
        organization = _org_from_obj(obj)
        return bool(organization and _is_user_owner_or_admin(organization, request.user))


class CanViewAnalytics(permissions.BasePermission):
    """
    Allow if user's role can view analytics.
    """

    def has_permission(self, request, view):
        if not request.user.is_authenticated:
            return False

        org_id = _get_org_from_view(view)
        if not org_id:
            return False

        organization = _get_organization(org_id)
        if not organization:
            return False

        role = organization.get_user_role(request.user)
        return bool(role and getattr(role, "can_view_analytics", False))

    def has_object_permission(self, request, view, obj):
        organization = _org_from_obj(obj)
        if not organization:
            return False
        role = organization.get_user_role(request.user)
        return bool(role and getattr(role, "can_view_analytics", False))


class HasOrganizationAPIKey(BaseHasAPIKey):
    """
    Permission class for Organization API Key authentication.
    Expects Bearer <key> in the Authorization header.
    """
    model = OrganizationAPIKey

    def get_key(self, request):
        auth_header = request.META.get("HTTP_AUTHORIZATION", "")
        if auth_header.startswith("Bearer "):
            return auth_header[7:]
        return auth_header or None

    def has_permission(self, request, view):
        key = self.get_key(request)
        if not key:
            return False

        try:
            api_key = (
                OrganizationAPIKey.objects
                .select_related("organization")
                .get(key=key, is_active=True)
            )
        except OrganizationAPIKey.DoesNotExist:
            return False

        # Expiry check
        if getattr(api_key, "is_expired", False):
            return False

        # IP allowlist check
        client_ip = self.get_client_ip(request)
        if hasattr(api_key, "is_ip_allowed") and not api_key.is_ip_allowed(client_ip):
            return False

        # Attach to a request for downstream use
        request.api_key = api_key
        request.organization = api_key.organization

        # Best-effort logging (should not block)
        self.log_api_usage(request, api_key)
        return True

    def get_client_ip(self, request):
        x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
        if x_forwarded_for:
            return x_forwarded_for.split(",")[0].strip()
        return request.META.get("REMOTE_ADDR")

    def log_api_usage(self, request, api_key):
        try:
            from .models import APIKeyUsageLog

            APIKeyUsageLog.objects.create(
                api_key=api_key,
                ip_address=self.get_client_ip(request),
                user_agent=request.META.get("HTTP_USER_AGENT", "")[:500],
                endpoint=request.path,
                method=request.method,
                status_code=200,          # Can be updated by middleware later
                response_time_ms=0        # Can be updated by middleware later
            )

            # Increment usage counter if model supports it
            if hasattr(api_key, "increment_usage"):
                api_key.increment_usage()

        except Exception:
            # Never fail the request due to logging errors
            pass


class IsOwnerOrReadOnly(permissions.BasePermission):
    """
    SAFE_METHODS: allowed for everyone.
    Write: allowed if the user is the owner/creator OR has OWNER/ADMIN on the related organization.
    """

    message = "You do not have permission to modify this resource."

    def has_object_permission(self, request, view, obj):
        # Read-only allowed
        if request.method in permissions.SAFE_METHODS:
            return True

        user = request.user
        if not user or not user.is_authenticated:
            return False

        # Owner / creator check
        if hasattr(obj, "owner") and getattr(obj.owner, "id", None) == getattr(user, "id", None):
            return True

        if hasattr(obj, "created_by") and getattr(obj.created_by, "id", None) == getattr(user, "id", None):
            return True

        # Organization role check
        organization = _org_from_obj(obj)
        if organization:
            return _is_user_owner_or_admin(organization, user)

        # Default deny for writes
        return False


class IsOrganizationOwner(permissions.BasePermission):
    """
    Allow only if the authenticated user is the owner (Organization.owner).
    """

    def has_permission(self, request, view):
        if not request.user.is_authenticated:
            return False

        org_id = _get_org_from_view(view)
        if not org_id:
            return False

        organization = _get_organization(org_id)
        return bool(organization and organization.owner == request.user)

    def has_object_permission(self, request, view, obj):
        organization = _org_from_obj(obj)
        return bool(organization and organization.owner == request.user)


class CanManageAPIKeys(permissions.BasePermission):
    """
    Allow if the user's role can manage API keys.
    """

    def has_permission(self, request, view):
        if not request.user.is_authenticated:
            return False

        org_id = _get_org_from_view(view)
        if not org_id:
            return False

        organization = _get_organization(org_id)
        if not organization:
            return False

        role = organization.get_user_role(request.user)
        return bool(role and getattr(role, "can_manage_api_keys", False))

    def has_object_permission(self, request, view, obj):
        organization = _org_from_obj(obj)
        if not organization:
            return False
        role = organization.get_user_role(request.user)
        return bool(role and getattr(role, "can_manage_api_keys", False))


class CanManageMembers(permissions.BasePermission):
    """
    Allow if a user's role can manage members.
    """

    def has_permission(self, request, view):
        if not request.user.is_authenticated:
            return False

        org_id = _get_org_from_view(view)
        if not org_id:
            return False

        organization = _get_organization(org_id)
        if not organization:
            return False

        role = organization.get_user_role(request.user)
        return bool(role and getattr(role, "can_manage_members", False))

    def has_object_permission(self, request, view, obj):
        organization = _org_from_obj(obj)
        if not organization:
            return False
        role = organization.get_user_role(request.user)
        return bool(role and getattr(role, "can_manage_members", False))
