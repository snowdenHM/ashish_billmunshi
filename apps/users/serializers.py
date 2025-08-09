from rest_framework import serializers
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils import timezone
from allauth.account.models import EmailAddress

from .models import (
    UserPreference,
    UserActivity,
    UserSession,
    UserNotification
)

User = get_user_model()


class UserProfileSerializer(serializers.ModelSerializer):
    """
    Serializer for user profile information
    """
    avatar_url = serializers.ReadOnlyField()
    gravatar_id = serializers.ReadOnlyField()
    has_verified_email = serializers.ReadOnlyField()
    total_organizations = serializers.ReadOnlyField()
    owned_organizations_count = serializers.ReadOnlyField()

    class Meta:
        model = User
        fields = [
            'id', 'username', 'email', 'first_name', 'last_name',
            'avatar', 'avatar_url', 'gravatar_id', 'phone', 'bio',
            'location', 'website', 'timezone', 'language',
            'is_verified', 'is_onboarded', 'email_notifications',
            'marketing_emails', 'two_factor_enabled',
            'has_verified_email', 'total_organizations',
            'owned_organizations_count', 'date_joined', 'last_login'
        ]
        read_only_fields = [
            'id', 'username', 'email', 'is_verified', 'date_joined',
            'last_login', 'avatar_url', 'gravatar_id', 'has_verified_email',
            'total_organizations', 'owned_organizations_count'
        ]

    def validate_phone(self, value):
        """Validate phone number format"""
        if value and len(value.strip()) < 10:
            raise serializers.ValidationError("Phone number must be at least 10 digits.")
        return value.strip() if value else value

    def validate_bio(self, value):
        """Validate bio length"""
        if value and len(value.strip()) > 500:
            raise serializers.ValidationError("Bio cannot exceed 500 characters.")
        return value.strip() if value else value


class UserBasicSerializer(serializers.ModelSerializer):
    """
    Basic user serializer for nested usage
    """
    avatar_url = serializers.ReadOnlyField()

    class Meta:
        model = User
        fields = [
            'id', 'username', 'email', 'first_name', 'last_name',
            'avatar_url', 'is_verified', 'date_joined'
        ]
        read_only_fields = fields


class UserDetailSerializer(UserProfileSerializer):
    """
    Detailed user serializer with additional information
    """
    primary_organization = serializers.SerializerMethodField()
    recent_activity = serializers.SerializerMethodField()
    unread_notifications_count = serializers.SerializerMethodField()

    class Meta(UserProfileSerializer.Meta):
        fields = UserProfileSerializer.Meta.fields + [
            'primary_organization', 'recent_activity', 'unread_notifications_count',
            'last_activity_at', 'last_login_ip'
        ]
        read_only_fields = UserProfileSerializer.Meta.read_only_fields + [
            'last_activity_at', 'last_login_ip'
        ]

    def get_primary_organization(self, obj):
        """Get user's primary organization"""
        primary_org = obj.get_primary_organization()
        if primary_org:
            from apps.teams.serializers import OrganizationSerializer
            return OrganizationSerializer(primary_org, context=self.context).data
        return None

    def get_recent_activity(self, obj):
        """Get user's recent activities"""
        activities = obj.activities.all()[:5]
        return UserActivitySerializer(activities, many=True).data

    def get_unread_notifications_count(self, obj):
        """Get count of unread notifications"""
        return obj.notifications.filter(is_read=False).count()


class UserPreferenceSerializer(serializers.ModelSerializer):
    """
    Serializer for user preferences
    """
    default_organization_name = serializers.CharField(
        source='default_organization.name',
        read_only=True
    )

    class Meta:
        model = UserPreference
        fields = [
            'id', 'default_organization', 'default_organization_name',
            'dashboard_layout', 'notification_frequency', 'default_api_format',
            'profile_visibility', 'theme', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']

    def validate_default_organization(self, value):
        """Validate that user is a member of the default organization"""
        if value:
            user = self.context['request'].user
            if not value.has_member(user):
                raise serializers.ValidationError(
                    "You can only set organizations you're a member of as default."
                )
        return value


class UserActivitySerializer(serializers.ModelSerializer):
    """
    Serializer for user activities
    """
    action_display = serializers.CharField(source='get_action_display', read_only=True)
    organization_name = serializers.CharField(source='organization.name', read_only=True)
    time_ago = serializers.SerializerMethodField()

    class Meta:
        model = UserActivity
        fields = [
            'id', 'action', 'action_display', 'description',
            'organization', 'organization_name', 'metadata',
            'created_at', 'time_ago'
        ]
        read_only_fields = fields

    def get_time_ago(self, obj):
        """Get human-readable time difference"""
        now = timezone.now()
        diff = now - obj.created_at

        if diff.days > 0:
            return f"{diff.days} day{'s' if diff.days > 1 else ''} ago"
        elif diff.seconds > 3600:
            hours = diff.seconds // 3600
            return f"{hours} hour{'s' if hours > 1 else ''} ago"
        elif diff.seconds > 60:
            minutes = diff.seconds // 60
            return f"{minutes} minute{'s' if minutes > 1 else ''} ago"
        else:
            return "Just now"


class UserSessionSerializer(serializers.ModelSerializer):
    """
    Serializer for user sessions
    """
    is_current = serializers.SerializerMethodField()
    is_expired = serializers.ReadOnlyField()
    browser_info = serializers.SerializerMethodField()

    class Meta:
        model = UserSession
        fields = [
            'id', 'session_key', 'ip_address', 'user_agent',
            'country', 'city', 'is_active', 'is_current',
            'is_expired', 'browser_info', 'last_activity',
            'expires_at', 'created_at'
        ]
        read_only_fields = fields

    def get_is_current(self, obj):
        """Check if this is the current session"""
        request = self.context.get('request')
        if request and hasattr(request, 'session'):
            return request.session.session_key == obj.session_key
        return False

    def get_browser_info(self, obj):
        """Extract browser information from user agent"""
        user_agent = obj.user_agent.lower()

        if 'chrome' in user_agent:
            browser = 'Chrome'
        elif 'firefox' in user_agent:
            browser = 'Firefox'
        elif 'safari' in user_agent:
            browser = 'Safari'
        elif 'edge' in user_agent:
            browser = 'Edge'
        else:
            browser = 'Unknown'

        if 'mobile' in user_agent:
            device = 'Mobile'
        elif 'tablet' in user_agent:
            device = 'Tablet'
        else:
            device = 'Desktop'

        return f"{browser} on {device}"


class UserNotificationSerializer(serializers.ModelSerializer):
    """
    Serializer for user notifications
    """
    type_display = serializers.CharField(source='get_notification_type_display', read_only=True)
    organization_name = serializers.CharField(source='organization.name', read_only=True)
    time_ago = serializers.SerializerMethodField()

    class Meta:
        model = UserNotification
        fields = [
            'id', 'title', 'message', 'notification_type', 'type_display',
            'is_read', 'read_at', 'action_url', 'action_text',
            'organization', 'organization_name', 'metadata',
            'created_at', 'time_ago'
        ]
        read_only_fields = [
            'id', 'created_at', 'time_ago', 'type_display', 'organization_name'
        ]

    def get_time_ago(self, obj):
        """Get human-readable time difference"""
        now = timezone.now()
        diff = now - obj.created_at

        if diff.days > 0:
            return f"{diff.days} day{'s' if diff.days > 1 else ''} ago"
        elif diff.seconds > 3600:
            hours = diff.seconds // 3600
            return f"{hours} hour{'s' if hours > 1 else ''} ago"
        elif diff.seconds > 60:
            minutes = diff.seconds // 60
            return f"{minutes} minute{'s' if minutes > 1 else ''} ago"
        else:
            return "Just now"


class PasswordChangeSerializer(serializers.Serializer):
    """
    Serializer for password change
    """
    current_password = serializers.CharField(write_only=True)
    new_password = serializers.CharField(write_only=True)
    confirm_password = serializers.CharField(write_only=True)

    def validate_current_password(self, value):
        """Validate current password"""
        user = self.context['request'].user
        if not user.check_password(value):
            raise serializers.ValidationError("Current password is incorrect.")
        return value

    def validate_new_password(self, value):
        """Validate new password using Django's validators"""
        try:
            validate_password(value, self.context['request'].user)
        except DjangoValidationError as e:
            raise serializers.ValidationError(e.messages)
        return value

    def validate(self, data):
        """Validate that passwords match"""
        if data['new_password'] != data['confirm_password']:
            raise serializers.ValidationError("New passwords don't match.")

        if data['current_password'] == data['new_password']:
            raise serializers.ValidationError("New password must be different from current password.")

        return data

    def save(self):
        """Change user's password"""
        user = self.context['request'].user
        user.set_password(self.validated_data['new_password'])
        user.save(update_fields=['password'])
        return user


class UserOrganizationSerializer(serializers.Serializer):
    """
    Serializer for user's organization memberships
    """
    organization = serializers.SerializerMethodField()
    role = serializers.SerializerMethodField()
    membership_info = serializers.SerializerMethodField()

    def get_organization(self, obj):
        """Get organization details"""
        from apps.teams.serializers import OrganizationSerializer
        return OrganizationSerializer(obj.organization, context=self.context).data

    def get_role(self, obj):
        """Get role details"""
        from apps.teams.serializers import RoleSerializer
        return RoleSerializer(obj.role).data

    def get_membership_info(self, obj):
        """Get membership information"""
        return {
            'joined_at': obj.joined_at,
            'is_active': obj.is_active,
            'invited_by': obj.invited_by.get_display_name() if obj.invited_by else None
        }


class BulkNotificationActionSerializer(serializers.Serializer):
    """
    Serializer for bulk notification actions
    """
    notification_ids = serializers.ListField(
        child=serializers.IntegerField(),
        min_length=1,
        max_length=100
    )
    action = serializers.ChoiceField(choices=['mark_read', 'mark_unread', 'delete'])

    def validate_notification_ids(self, value):
        """Validate that notification IDs exist and belong to user"""
        user = self.context['request'].user
        existing_ids = set(
            user.notifications.filter(id__in=value).values_list('id', flat=True)
        )

        if len(existing_ids) != len(value):
            raise serializers.ValidationError("Some notification IDs are invalid or don't belong to you.")

        return value


class UserStatsSerializer(serializers.Serializer):
    """
    Serializer for user statistics
    """
    total_organizations = serializers.IntegerField()
    owned_organizations = serializers.IntegerField()
    total_notifications = serializers.IntegerField()
    unread_notifications = serializers.IntegerField()
    recent_activities_count = serializers.IntegerField()
    active_sessions_count = serializers.IntegerField()
    account_age_days = serializers.IntegerField()


class UserOnboardingSerializer(serializers.Serializer):
    """
    Serializer for user onboarding completion
    """
    first_name = serializers.CharField(max_length=150)
    last_name = serializers.CharField(max_length=150)
    timezone = serializers.CharField(max_length=50, required=False)
    language = serializers.CharField(max_length=10, required=False)
    phone = serializers.CharField(max_length=20, required=False)

    def validate_timezone(self, value):
        """Validate timezone"""
        import pytz
        if value and value not in pytz.all_timezones:
            raise serializers.ValidationError("Invalid timezone.")
        return value

    def save(self):
        """Complete user onboarding"""
        user = self.context['request'].user

        for attr, value in self.validated_data.items():
            setattr(user, attr, value)

        user.is_onboarded = True
        user.save()

        # Create user preferences
        UserPreference.objects.get_or_create(user=user)

        # Log onboarding completion
        UserActivity.objects.create(
            user=user,
            action='onboarding_complete',
            description='User completed onboarding process'
        )

        return user