from rest_framework import permissions
from django.contrib.auth import get_user_model

User = get_user_model()


class CanViewSubscription(permissions.BasePermission):
    """
    Permission to view subscription details
    Users can view subscriptions for organizations they belong to
    """

    def has_permission(self, request, view):
        return request.user.is_authenticated

    def has_object_permission(self, request, view, obj):
        # For subscription objects
        if hasattr(obj, 'organization'):
            return obj.organization.has_member(request.user)

        # For invoice objects
        if hasattr(obj, 'subscription'):
            return obj.subscription.organization.has_member(request.user)

        return False


class CanManageSubscription(permissions.BasePermission):
    """
    Permission to manage subscription (create, update, cancel)
    Only organization owners and admins can manage subscriptions
    """

    def has_permission(self, request, view):
        return request.user.is_authenticated

    def has_object_permission(self, request, view, obj):
        from apps.teams.models import Role

        # For subscription objects
        if hasattr(obj, 'organization'):
            user_role = obj.organization.get_user_role(request.user)
            return user_role and user_role.can_manage_billing

        # For invoice objects
        if hasattr(obj, 'subscription'):
            user_role = obj.subscription.organization.get_user_role(request.user)
            return user_role and user_role.can_manage_billing

        return False


class CanViewBilling(permissions.BasePermission):
    """
    Permission to view billing information
    Organization members with billing access can view invoices and payment history
    """

    def has_permission(self, request, view):
        return request.user.is_authenticated

    def has_object_permission(self, request, view, obj):
        from apps.teams.models import Role

        # Get organization from object
        organization = None
        if hasattr(obj, 'organization'):
            organization = obj.organization
        elif hasattr(obj, 'subscription'):
            organization = obj.subscription.organization

        if not organization:
            return False

        # Check if user is a member and has billing access
        user_role = organization.get_user_role(request.user)
        return user_role and (
                user_role.can_manage_billing or
                user_role.can_view_analytics or
                user_role.name in [Role.OWNER, Role.ADMIN]
        )


class CanManagePlans(permissions.BasePermission):
    """
    Permission to manage subscription plans
    Only superusers and staff can manage plans
    """

    def has_permission(self, request, view):
        return request.user.is_authenticated and (
                request.user.is_superuser or request.user.is_staff
        )


class CanViewAnalytics(permissions.BasePermission):
    """
    Permission to view subscription analytics
    Only superusers and staff can view system-wide analytics
    """

    def has_permission(self, request, view):
        return request.user.is_authenticated and (
                request.user.is_superuser or request.user.is_staff
        )


class CanManageDiscounts(permissions.BasePermission):
    """
    Permission to manage discount codes
    Only superusers and staff can create/manage discounts
    """

    def has_permission(self, request, view):
        return request.user.is_authenticated and (
                request.user.is_superuser or request.user.is_staff
        )


class CanAccessUsageRecords(permissions.BasePermission):
    """
    Permission to access usage records
    Users can access usage records for their organizations
    """

    def has_permission(self, request, view):
        return request.user.is_authenticated

    def has_object_permission(self, request, view, obj):
        if hasattr(obj, 'subscription'):
            return obj.subscription.organization.has_member(request.user)
        return False


class IsSubscriptionOwner(permissions.BasePermission):
    """
    Permission that only allows subscription owners (organization owners) to access
    """

    def has_permission(self, request, view):
        return request.user.is_authenticated

    def has_object_permission(self, request, view, obj):
        from apps.teams.models import Role

        organization = None
        if hasattr(obj, 'organization'):
            organization = obj.organization
        elif hasattr(obj, 'subscription'):
            organization = obj.subscription.organization

        if not organization:
            return False

        return organization.owner == request.user


class CanModifySubscriptionStatus(permissions.BasePermission):
    """
    Permission to modify subscription status (activate, suspend, etc.)
    Only superusers can modify subscription status directly
    """

    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.is_superuser


class CanProcessWebhooks(permissions.BasePermission):
    """
    Permission for webhook endpoints
    Webhooks don't require authentication but should validate the source
    """

    def has_permission(self, request, view):
        # TODO: Implement webhook signature validation
        # For now, allow all webhook requests
        return True


class HasValidSubscription(permissions.BasePermission):
    """
    Permission that requires user's organization to have a valid subscription
    """

    def has_permission(self, request, view):
        if not request.user.is_authenticated:
            return False

        # Check if user belongs to any organization with valid subscription
        user_orgs = request.user.organization_memberships.filter(
            is_active=True
        ).select_related('organization')

        for membership in user_orgs:
            org = membership.organization
            if hasattr(org, 'subscription') and org.subscription.is_active:
                return True

        return False


class CanExceedUsageLimits(permissions.BasePermission):
    """
    Permission to exceed usage limits
    Some plans or special arrangements allow exceeding limits
    """

    def has_permission(self, request, view):
        if not request.user.is_authenticated:
            return False

        # Check if user has any organizations with unlimited plans
        user_orgs = request.user.organization_memberships.filter(
            is_active=True
        ).select_related('organization__subscription__plan')

        for membership in user_orgs:
            org = membership.organization
            if (hasattr(org, 'subscription') and
                    org.subscription.plan.plan_type == 'enterprise'):
                return True

        return False


class CanViewInvoices(permissions.BasePermission):
    """
    Permission to view invoices
    Organization members with appropriate roles can view invoices
    """

    def has_permission(self, request, view):
        return request.user.is_authenticated

    def has_object_permission(self, request, view, obj):
        from apps.teams.models import Role

        organization = obj.subscription.organization
        user_role = organization.get_user_role(request.user)

        # Allow owners, admins, and members with billing access
        return user_role and (
                user_role.name in [Role.OWNER, Role.ADMIN] or
                user_role.can_manage_billing or
                user_role.can_view_analytics
        )


class CanDownloadInvoices(permissions.BasePermission):
    """
    Permission to download invoice PDFs
    Only owners and admins can download invoices
    """

    def has_permission(self, request, view):
        return request.user.is_authenticated

    def has_object_permission(self, request, view, obj):
        from apps.teams.models import Role

        organization = obj.subscription.organization
        user_role = organization.get_user_role(request.user)

        return user_role and user_role.name in [Role.OWNER, Role.ADMIN]


class CanCreateSubscription(permissions.BasePermission):
    """
    Permission to create subscriptions
    Only organization owners can create subscriptions
    """

    def has_permission(self, request, view):
        return request.user.is_authenticated

    def has_object_permission(self, request, view, obj):
        # obj would be the organization in this case
        return obj.owner == request.user


class CanCancelSubscription(permissions.BasePermission):
    """
    Permission to cancel subscriptions
    Only organization owners can cancel subscriptions
    """

    def has_permission(self, request, view):
        return request.user.is_authenticated

    def has_object_permission(self, request, view, obj):
        if hasattr(obj, 'organization'):
            return obj.organization.owner == request.user
        return False


class IsTrialUser(permissions.BasePermission):
    """
    Permission that identifies trial users
    Used for applying trial-specific restrictions
    """

    def has_permission(self, request, view):
        if not request.user.is_authenticated:
            return False

        # Check if user's primary organization is on trial
        primary_org = request.user.get_primary_organization()
        if primary_org and hasattr(primary_org, 'subscription'):
            return primary_org.subscription.is_trial

        return False


class CanExtendTrial(permissions.BasePermission):
    """
    Permission to extend trial periods
    Only staff and superusers can extend trials
    """

    def has_permission(self, request, view):
        return request.user.is_authenticated and (
                request.user.is_staff or request.user.is_superuser
        )


class CanApplyDiscounts(permissions.BasePermission):
    """
    Permission to apply discount codes
    All authenticated users can apply discounts to their subscriptions
    """

    def has_permission(self, request, view):
        return request.user.is_authenticated