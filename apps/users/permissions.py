from rest_framework import permissions
from django.contrib.auth import get_user_model

User = get_user_model()


class IsOwnerOrReadOnly(permissions.BasePermission):
    """
    Permission that allows owners to edit, others to read only
    """

    def has_object_permission(self, request, view, obj):
        # Read permissions for any authenticated user
        if request.method in permissions.SAFE_METHODS:
            return True

        # Write permissions only for owner
        if hasattr(obj, 'user'):
            return obj.user == request.user
        elif hasattr(obj, 'owner'):
            return obj.owner == request.user
        elif isinstance(obj, User):
            return obj == request.user

        return False


class IsOwner(permissions.BasePermission):
    """
    Permission that only allows owners to access the object
    """

    def has_object_permission(self, request, view, obj):
        if hasattr(obj, 'user'):
            return obj.user == request.user
        elif hasattr(obj, 'owner'):
            return obj.owner == request.user
        elif isinstance(obj, User):
            return obj == request.user

        return False


class CanViewUserData(permissions.BasePermission):
    """
    Permission to check if user can view another user's data
    Rules:
    1. Users can always view their own data
    2. Users can view data of members in their organizations
    3. Organization owners/admins can view any member's data
    """

    def has_object_permission(self, request, view, obj):
        # Users can always view their own data
        if obj == request.user:
            return True

        # Check if users share any organizations
        user_orgs = set(request.user.organization_memberships.filter(
            is_active=True
        ).values_list('organization_id', flat=True))

        target_user_orgs = set(obj.organization_memberships.filter(
            is_active=True
        ).values_list('organization_id', flat=True))

        # If they share any organizations, allow access
        shared_orgs = user_orgs.intersection(target_user_orgs)
        return bool(shared_orgs)


class CanManageUser(permissions.BasePermission):
    """
    Permission to check if user can manage another user
    Rules:
    1. Users can always manage themselves
    2. Organization owners can manage any member
    3. Organization admins can manage members (but not other admins/owners)
    """

    def has_object_permission(self, request, view, obj):
        # Users can always manage themselves
        if obj == request.user:
            return True

        # Check organization-level permissions
        from apps.teams.models import Role

        # Get shared organizations
        user_memberships = request.user.organization_memberships.filter(
            is_active=True
        ).select_related('organization', 'role')

        target_memberships = obj.organization_memberships.filter(
            is_active=True
        ).select_related('organization', 'role')

        for user_membership in user_memberships:
            # Check if target user is in the same organization
            target_membership = target_memberships.filter(
                organization=user_membership.organization
            ).first()

            if target_membership:
                # Organization owners can manage anyone
                if user_membership.role.name == Role.OWNER:
                    return True

                # Admins can manage members and viewers (not other admins/owners)
                if (user_membership.role.name == Role.ADMIN and
                        target_membership.role.name in [Role.MEMBER, Role.VIEWER]):
                    return True

        return False


class IsAdminOrOwner(permissions.BasePermission):
    """
    Permission for admin or owner level access
    """

    def has_permission(self, request, view):
        if not request.user.is_authenticated:
            return False

        # Check if user is superuser
        if request.user.is_superuser:
            return True

        # Check if user is owner or admin of any organization
        from apps.teams.models import Role

        user_roles = request.user.organization_memberships.filter(
            is_active=True
        ).values_list('role__name', flat=True)

        return any(role in [Role.OWNER, Role.ADMIN] for role in user_roles)


class IsUserOrSuperuser(permissions.BasePermission):
    """
    Permission that allows access to the user themselves or superusers
    """

    def has_permission(self, request, view):
        return request.user.is_authenticated

    def has_object_permission(self, request, view, obj):
        # Superusers can access any user data
        if request.user.is_superuser:
            return True

        # Users can access their own data
        if isinstance(obj, User):
            return obj == request.user
        elif hasattr(obj, 'user'):
            return obj.user == request.user

        return False


class CanCreateNotification(permissions.BasePermission):
    """
    Permission to check if user can create notifications for other users
    Only organization owners/admins can create notifications for their members
    """

    def has_permission(self, request, view):
        if not request.user.is_authenticated:
            return False

        # Check if user has admin privileges in any organization
        from apps.teams.models import Role

        admin_roles = [Role.OWNER, Role.ADMIN]
        return request.user.organization_memberships.filter(
            is_active=True,
            role__name__in=admin_roles
        ).exists()


class CanAccessUserSession(permissions.BasePermission):
    """
    Permission to check if user can access session data
    Only the session owner can access their sessions
    """

    def has_object_permission(self, request, view, obj):
        return obj.user == request.user


class CanAccessUserActivity(permissions.BasePermission):
    """
    Permission to check if user can access activity data
    Users can only access their own activities
    """

    def has_object_permission(self, request, view, obj):
        return obj.user == request.user


class IsVerifiedUser(permissions.BasePermission):
    """
    Permission that requires user to have verified email
    """

    def has_permission(self, request, view):
        if not request.user.is_authenticated:
            return False

        return request.user.has_verified_email


class IsOnboardedUser(permissions.BasePermission):
    """
    Permission that requires user to have completed onboarding
    """

    def has_permission(self, request, view):
        if not request.user.is_authenticated:
            return False

        return request.user.is_onboarded


class CanInviteUsers(permissions.BasePermission):
    """
    Permission to check if user can invite other users
    Users must be owner or admin of at least one organization
    """

    def has_permission(self, request, view):
        if not request.user.is_authenticated:
            return False

        from apps.teams.models import Role

        # Check if user has invite permissions in any organization
        invite_roles = [Role.OWNER, Role.ADMIN]
        return request.user.organization_memberships.filter(
            is_active=True,
            role__name__in=invite_roles
        ).exists()


class RateLimitPermission(permissions.BasePermission):
    """
    Basic rate limiting permission
    Can be extended with more sophisticated rate limiting logic
    """

    def has_permission(self, request, view):
        # Basic implementation - can be enhanced with Redis/cache-based rate limiting
        return True


class APIKeyPermission(permissions.BasePermission):
    """
    Permission for API key based authentication
    """

    def has_permission(self, request, view):
        # Check if request has valid API key
        return hasattr(request, 'api_key') and request.api_key


class OrganizationMemberPermission(permissions.BasePermission):
    """
    Permission that requires user to be a member of at least one organization
    """

    def has_permission(self, request, view):
        if not request.user.is_authenticated:
            return False

        return request.user.organization_memberships.filter(
            is_active=True
        ).exists()