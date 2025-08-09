import hashlib
import uuid
from functools import cached_property

from allauth.account.models import EmailAddress
from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils import timezone
from django.core.exceptions import ValidationError

from apps.users.helpers import validate_profile_picture
from apps.utils.models import BaseModel


def _get_avatar_filename(instance, filename):
    """Use random filename prevent overwriting existing files & to fix caching issues."""
    return f"profile-pictures/{uuid.uuid4()}.{filename.split('.')[-1]}"


class CustomUser(AbstractUser):
    """
    Enhanced user model with additional fields for multi-tenant SaaS
    """
    # Profile Information
    avatar = models.FileField(
        upload_to=_get_avatar_filename,
        blank=True,
        validators=[validate_profile_picture]
    )
    phone = models.CharField(max_length=20, blank=True)
    bio = models.TextField(max_length=500, blank=True)
    location = models.CharField(max_length=100, blank=True)
    website = models.URLField(blank=True)

    # User Preferences
    timezone = models.CharField(
        max_length=50,
        default='UTC',
        help_text="User's preferred timezone"
    )
    language = models.CharField(
        max_length=10,
        default='en',
        choices=[
            ('en', 'English'),
            ('es', 'Spanish'),
            ('fr', 'French'),
            ('de', 'German'),
            ('hi', 'Hindi'),
        ]
    )

    # Account Status
    is_verified = models.BooleanField(
        default=False,
        help_text="Whether user has verified their email"
    )
    is_onboarded = models.BooleanField(
        default=False,
        help_text="Whether user has completed onboarding"
    )

    # Activity Tracking
    last_login_ip = models.GenericIPAddressField(null=True, blank=True)
    last_activity_at = models.DateTimeField(null=True, blank=True)

    # Notifications Settings
    email_notifications = models.BooleanField(
        default=True,
        help_text="Receive email notifications"
    )
    marketing_emails = models.BooleanField(
        default=False,
        help_text="Receive marketing emails"
    )

    # Two-Factor Authentication
    two_factor_enabled = models.BooleanField(
        default=False,
        help_text="Whether 2FA is enabled"
    )

    class Meta:
        db_table = 'users'
        ordering = ['-date_joined']

    def __str__(self):
        return f"{self.get_full_name()} <{self.email or self.username}>"

    def get_display_name(self) -> str:
        """Get user's display name"""
        if self.get_full_name().strip():
            return self.get_full_name()
        return self.email or self.username

    def get_short_name(self) -> str:
        """Get user's short name"""
        if self.first_name:
            return self.first_name
        return self.email.split('@')[0] if self.email else self.username

    @property
    def avatar_url(self) -> str:
        """Get user's avatar URL"""
        if self.avatar:
            return self.avatar.url
        else:
            return f"https://www.gravatar.com/avatar/{self.gravatar_id}?s=128&d=identicon"

    @property
    def gravatar_id(self) -> str:
        """Get Gravatar ID"""
        # https://en.gravatar.com/site/implement/hash/
        email = self.email.lower().strip() if self.email else ''
        return hashlib.md5(email.encode("utf-8")).hexdigest()

    @cached_property
    def has_verified_email(self):
        """Check if user has verified email"""
        return EmailAddress.objects.filter(user=self, verified=True).exists()

    def get_organizations(self):
        """Get all organizations user is a member of"""
        return self.organization_memberships.filter(
            is_active=True
        ).select_related('organization', 'role')

    def get_primary_organization(self):
        """Get user's primary organization (first owned, then first joined)"""
        # First try owned organizations
        owned_org = self.owned_organizations.filter(is_active=True).first()
        if owned_org:
            return owned_org

        # Then try first active membership
        membership = self.organization_memberships.filter(
            is_active=True
        ).select_related('organization').first()

        return membership.organization if membership else None

    def update_last_activity(self, ip_address=None):
        """Update user's last activity timestamp and IP"""
        self.last_activity_at = timezone.now()
        if ip_address:
            self.last_login_ip = ip_address
        self.save(update_fields=['last_activity_at', 'last_login_ip'])

    def can_join_organization(self, organization):
        """Check if user can join an organization"""
        # Check if already a member
        if organization.has_member(self):
            return False, "Already a member of this organization"

        # Check organization limits
        if not organization.can_add_member():
            return False, "Organization has reached member limit"

        return True, "Can join organization"

    @property
    def total_organizations(self):
        """Get total number of organizations user belongs to"""
        return self.organization_memberships.filter(is_active=True).count()

    @property
    def owned_organizations_count(self):
        """Get number of organizations user owns"""
        return self.owned_organizations.filter(is_active=True).count()


class UserPreference(BaseModel):
    """
    User-specific preferences and settings
    """
    user = models.OneToOneField(
        CustomUser,
        on_delete=models.CASCADE,
        related_name='preferences'
    )

    # Dashboard Preferences
    default_organization = models.ForeignKey(
        'teams.Organization',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        help_text="User's default organization"
    )
    dashboard_layout = models.CharField(
        max_length=20,
        choices=[
            ('grid', 'Grid Layout'),
            ('list', 'List Layout'),
            ('compact', 'Compact Layout'),
        ],
        default='grid'
    )

    # Notification Preferences
    notification_frequency = models.CharField(
        max_length=20,
        choices=[
            ('immediate', 'Immediate'),
            ('daily', 'Daily Digest'),
            ('weekly', 'Weekly Digest'),
            ('never', 'Never'),
        ],
        default='immediate'
    )

    # API Preferences
    default_api_format = models.CharField(
        max_length=10,
        choices=[
            ('json', 'JSON'),
            ('xml', 'XML'),
            ('yaml', 'YAML'),
        ],
        default='json'
    )

    # Privacy Settings
    profile_visibility = models.CharField(
        max_length=20,
        choices=[
            ('public', 'Public'),
            ('organization', 'Organization Members Only'),
            ('private', 'Private'),
        ],
        default='organization'
    )

    # Theme Preferences
    theme = models.CharField(
        max_length=10,
        choices=[
            ('light', 'Light'),
            ('dark', 'Dark'),
            ('auto', 'Auto'),
        ],
        default='light'
    )

    class Meta:
        db_table = 'user_preferences'

    def __str__(self):
        return f"Preferences for {self.user.get_display_name()}"


class UserActivity(BaseModel):
    """
    Track user activities and actions
    """
    ACTION_TYPES = [
        ('login', 'User Login'),
        ('logout', 'User Logout'),
        ('profile_update', 'Profile Updated'),
        ('password_change', 'Password Changed'),
        ('organization_create', 'Organization Created'),
        ('organization_join', 'Joined Organization'),
        ('organization_leave', 'Left Organization'),
        ('api_key_create', 'API Key Created'),
        ('api_key_delete', 'API Key Deleted'),
        ('invitation_sent', 'Invitation Sent'),
        ('invitation_accept', 'Invitation Accepted'),
        ('invitation_decline', 'Invitation Declined'),
    ]

    user = models.ForeignKey(
        CustomUser,
        on_delete=models.CASCADE,
        related_name='activities'
    )
    action = models.CharField(max_length=50, choices=ACTION_TYPES)
    description = models.TextField(blank=True)

    # Context Information
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)
    organization = models.ForeignKey(
        'teams.Organization',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        help_text="Related organization if applicable"
    )

    # Additional metadata
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = 'user_activities'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['user', '-created_at']),
            models.Index(fields=['action', '-created_at']),
        ]

    def __str__(self):
        return f"{self.user.get_display_name()} - {self.get_action_display()}"


class UserSession(BaseModel):
    """
    Track user sessions for security and analytics
    """
    user = models.ForeignKey(
        CustomUser,
        on_delete=models.CASCADE,
        related_name='sessions'
    )
    session_key = models.CharField(max_length=40, unique=True)
    ip_address = models.GenericIPAddressField()
    user_agent = models.TextField(blank=True)

    # Location Information (optional)
    country = models.CharField(max_length=100, blank=True)
    city = models.CharField(max_length=100, blank=True)

    # Session Status
    is_active = models.BooleanField(default=True)
    last_activity = models.DateTimeField(auto_now=True)
    expires_at = models.DateTimeField()

    class Meta:
        db_table = 'user_sessions'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['user', '-last_activity']),
            models.Index(fields=['session_key']),
        ]

    def __str__(self):
        return f"{self.user.get_display_name()} - Session {self.session_key[:8]}..."

    @property
    def is_expired(self):
        """Check if session is expired"""
        return timezone.now() > self.expires_at

    def terminate(self):
        """Terminate the session"""
        self.is_active = False
        self.save(update_fields=['is_active'])


class UserNotification(BaseModel):
    """
    In-app notifications for users
    """
    NOTIFICATION_TYPES = [
        ('info', 'Information'),
        ('success', 'Success'),
        ('warning', 'Warning'),
        ('error', 'Error'),
        ('invitation', 'Invitation'),
        ('organization', 'Organization'),
        ('api', 'API Related'),
        ('billing', 'Billing'),
        ('security', 'Security'),
    ]

    user = models.ForeignKey(
        CustomUser,
        on_delete=models.CASCADE,
        related_name='notifications'
    )
    title = models.CharField(max_length=255)
    message = models.TextField()
    notification_type = models.CharField(max_length=20, choices=NOTIFICATION_TYPES)

    # Status
    is_read = models.BooleanField(default=False)
    read_at = models.DateTimeField(null=True, blank=True)

    # Optional links and actions
    action_url = models.URLField(blank=True)
    action_text = models.CharField(max_length=100, blank=True)

    # Related objects
    organization = models.ForeignKey(
        'teams.Organization',
        on_delete=models.CASCADE,
        null=True,
        blank=True
    )

    # Metadata
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = 'user_notifications'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['user', 'is_read', '-created_at']),
            models.Index(fields=['notification_type', '-created_at']),
        ]

    def __str__(self):
        return f"{self.user.get_display_name()} - {self.title}"

    def mark_as_read(self):
        """Mark notification as read"""
        if not self.is_read:
            self.is_read = True
            self.read_at = timezone.now()
            self.save(update_fields=['is_read', 'read_at'])

    @classmethod
    def create_notification(cls, user, title, message, notification_type='info', **kwargs):
        """Helper method to create notifications"""
        return cls.objects.create(
            user=user,
            title=title,
            message=message,
            notification_type=notification_type,
            **kwargs
        )