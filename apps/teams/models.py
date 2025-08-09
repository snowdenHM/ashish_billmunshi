import secrets
import uuid
from django.db import models
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from apps.utils.models import BaseModel

User = get_user_model()


class Organization(BaseModel):
    """
    Organization model for multi-tenant architecture
    """
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255, unique=True)
    description = models.TextField(blank=True)
    logo = models.ImageField(upload_to='organization_logos/', blank=True, null=True)
    website = models.URLField(blank=True)
    phone = models.CharField(max_length=20, blank=True)
    address = models.TextField(blank=True)

    # Organization settings
    is_active = models.BooleanField(default=True)
    max_users = models.PositiveIntegerField(default=10, help_text="Maximum users allowed in this organization")

    # Owner of the organization
    owner = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='owned_organizations'
    )

    class Meta:
        db_table = 'organizations'
        ordering = ['-created_at']

    def __str__(self):
        return self.name

    @property
    def member_count(self):
        return self.members.filter(is_active=True).count()

    @property
    def api_key_count(self):
        return self.api_keys.filter(is_active=True).count()

    def can_add_member(self):
        return self.member_count < self.max_users

    def get_user_role(self, user):
        """Get a user role in this organization"""
        try:
            membership = self.members.get(user=user, is_active=True)
            return membership.role
        except OrganizationMember.DoesNotExist:
            return None

    def has_member(self, user):
        """Check if user is a member of this organization"""
        return self.members.filter(user=user, is_active=True).exists()


class Role(models.Model):
    """
    Roles within an organization
    """
    OWNER = 'owner'
    ADMIN = 'admin'
    MEMBER = 'member'
    VIEWER = 'viewer'

    ROLE_CHOICES = [
        (OWNER, 'Owner'),
        (ADMIN, 'Admin'),
        (MEMBER, 'Member'),
        (VIEWER, 'Viewer'),
    ]

    name = models.CharField(max_length=50, choices=ROLE_CHOICES, unique=True)
    description = models.TextField(blank=True)

    # Permissions
    can_manage_organization = models.BooleanField(default=False)
    can_manage_members = models.BooleanField(default=False)
    can_manage_api_keys = models.BooleanField(default=False)
    can_view_analytics = models.BooleanField(default=False)
    can_manage_billing = models.BooleanField(default=False)

    class Meta:
        db_table = 'roles'

    def __str__(self):
        return self.get_name_display()


class OrganizationMember(BaseModel):
    """
    Membership relationship between User and Organization
    """
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name='members'
    )
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='organization_memberships'
    )
    role = models.ForeignKey(
        Role,
        on_delete=models.PROTECT,
        default=3  # Default to Member role
    )

    is_active = models.BooleanField(default=True)
    joined_at = models.DateTimeField(auto_now_add=True)
    invited_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='invited_members'
    )

    class Meta:
        db_table = 'organization_members'
        unique_together = ['organization', 'user']
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.user.email} - {self.organization.name} ({self.role.name})"

    def clean(self):
        # Ensure only one owner per organization
        if self.role.name == Role.OWNER:
            existing_owner = OrganizationMember.objects.filter(
                organization=self.organization,
                role__name=Role.OWNER,
                is_active=True
            ).exclude(pk=self.pk).exists()

            if existing_owner:
                raise ValidationError("Organization can only have one owner.")


class Invitation(BaseModel):
    """
    Invitations sent to users to join organizations
    """
    PENDING = 'pending'
    ACCEPTED = 'accepted'
    DECLINED = 'declined'
    EXPIRED = 'expired'

    STATUS_CHOICES = [
        (PENDING, 'Pending'),
        (ACCEPTED, 'Accepted'),
        (DECLINED, 'Declined'),
        (EXPIRED, 'Expired'),
    ]

    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name='invitations'
    )
    email = models.EmailField()
    role = models.ForeignKey(Role, on_delete=models.CASCADE)
    invited_by = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='sent_invitations'
    )

    token = models.UUIDField(default=uuid.uuid4, unique=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=PENDING)
    expires_at = models.DateTimeField()

    # Optional: If user already exists
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='received_invitations'
    )

    class Meta:
        db_table = 'invitations'
        unique_together = ['organization', 'email', 'status']
        ordering = ['-created_at']

    def __str__(self):
        return f"Invitation to {self.email} for {self.organization.name}"

    @property
    def is_expired(self):
        from django.utils import timezone
        return timezone.now() > self.expires_at

    def accept(self, user=None):
        """Accept the invitation"""
        if self.status != self.PENDING:
            raise ValidationError("Invitation is not in pending status")

        if self.is_expired:
            self.status = self.EXPIRED
            self.save()
            raise ValidationError("Invitation has expired")

        # If user is not provided, try to find by email
        if not user:
            try:
                user = User.objects.get(email=self.email)
            except User.DoesNotExist:
                raise ValidationError("User with this email does not exist")

        # Create organization membership
        OrganizationMember.objects.create(
            organization=self.organization,
            user=user,
            role=self.role,
            invited_by=self.invited_by
        )

        self.status = self.ACCEPTED
        self.user = user
        self.save()

    def decline(self):
        """Decline the invitation"""
        if self.status != self.PENDING:
            raise ValidationError("Invitation is not in pending status")

        self.status = self.DECLINED
        self.save()


class OrganizationAPIKey(BaseModel):
    """
    API Keys for organizations to integrate with external systems
    """
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name='api_keys'
    )
    name = models.CharField(max_length=255, help_text="Descriptive name for this API key")
    key = models.CharField(max_length=128, unique=True)
    prefix = models.CharField(max_length=8)

    # Permissions and settings
    is_active = models.BooleanField(default=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    usage_count = models.PositiveIntegerField(default=0)

    # Rate limiting
    rate_limit_per_hour = models.PositiveIntegerField(default=1000)
    rate_limit_per_day = models.PositiveIntegerField(default=10000)

    # Security
    allowed_ips = models.TextField(
        blank=True,
        help_text="Comma-separated list of allowed IP addresses"
    )
    expires_at = models.DateTimeField(null=True, blank=True)

    created_by = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='created_api_keys'
    )

    class Meta:
        db_table = 'organization_api_keys'
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.organization.name} - {self.name} ({self.prefix}...)"

    def save(self, *args, **kwargs):
        if not self.key:
            self.key = self.generate_key()
            self.prefix = self.key[:8]
        super().save(*args, **kwargs)

    @staticmethod
    def generate_key():
        """Generate a secure API key"""
        return f"bm_{''.join(secrets.choice('abcdefghijklmnopqrstuvwxyz0123456789') for _ in range(40))}"

    @property
    def masked_key(self):
        """Return masked version of the key for display"""
        return f"{self.prefix}{'*' * 32}"

    @property
    def is_expired(self):
        """Check if API key is expired"""
        if not self.expires_at:
            return False
        from django.utils import timezone
        return timezone.now() > self.expires_at

    def increment_usage(self):
        """Increment usage count and update last used timestamp"""
        from django.utils import timezone
        self.usage_count += 1
        self.last_used_at = timezone.now()
        self.save(update_fields=['usage_count', 'last_used_at'])

    def get_allowed_ips_list(self):
        """Get list of allowed IPs"""
        if not self.allowed_ips:
            return []
        return [ip.strip() for ip in self.allowed_ips.split(',') if ip.strip()]

    def is_ip_allowed(self, ip_address):
        """Check if IP address is allowed"""
        allowed_ips = self.get_allowed_ips_list()
        if not allowed_ips:
            return True  # No restrictions
        return ip_address in allowed_ips


class APIKeyUsageLog(BaseModel):
    """
    Log API key usage for analytics and monitoring
    """
    api_key = models.ForeignKey(
        OrganizationAPIKey,
        on_delete=models.CASCADE,
        related_name='usage_logs'
    )
    ip_address = models.GenericIPAddressField()
    user_agent = models.TextField(blank=True)
    endpoint = models.CharField(max_length=255)
    method = models.CharField(max_length=10)
    status_code = models.PositiveIntegerField()
    response_time_ms = models.PositiveIntegerField(help_text="Response time in milliseconds")

    class Meta:
        db_table = 'api_key_usage_logs'
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.api_key.name} - {self.method} {self.endpoint} ({self.status_code})"
