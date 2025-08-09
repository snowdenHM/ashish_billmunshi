from rest_framework import serializers
from django.contrib.auth import get_user_model
from django.utils import timezone
from django.utils.text import slugify
from django.db import transaction
from datetime import timedelta
from .models import (
    Organization,
    OrganizationMember,
    Role,
    Invitation,
    OrganizationAPIKey,
    APIKeyUsageLog
)

User = get_user_model()


class RoleSerializer(serializers.ModelSerializer):
    """Serializer for Role model"""

    class Meta:
        model = Role
        fields = [
            'id', 'name', 'description', 'can_manage_organization',
            'can_manage_members', 'can_manage_api_keys', 'can_view_analytics',
            'can_manage_billing'
        ]


class UserBasicSerializer(serializers.ModelSerializer):
    """Basic user serializer for nested usage"""

    class Meta:
        model = User
        fields = ['id', 'email', 'first_name', 'last_name', 'get_display_name']
        read_only_fields = ['id', 'get_display_name']


class OrganizationMemberSerializer(serializers.ModelSerializer):
    """Serializer for Organization Members"""
    user = UserBasicSerializer(read_only=True)
    role = RoleSerializer(read_only=True)
    role_name = serializers.CharField(source='role.name', read_only=True)
    invited_by = UserBasicSerializer(read_only=True)

    class Meta:
        model = OrganizationMember
        fields = [
            'id', 'user', 'role', 'role_name', 'is_active',
            'joined_at', 'invited_by', 'created_at'
        ]
        read_only_fields = ['id', 'joined_at', 'created_at']


class OrganizationSerializer(serializers.ModelSerializer):
    """Serializer for Organization model"""
    owner = UserBasicSerializer(read_only=True)
    member_count = serializers.ReadOnlyField()
    api_key_count = serializers.ReadOnlyField()
    user_role = serializers.SerializerMethodField()

    class Meta:
        model = Organization
        fields = [
            'id', 'name', 'slug', 'description', 'logo', 'website',
            'phone', 'address', 'is_active', 'max_users', 'owner',
            'member_count', 'api_key_count', 'user_role', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'slug', 'owner', 'created_at', 'updated_at']

    def get_user_role(self, obj):
        """Get current user's role in this organization"""
        user = self.context['request'].user
        if user.is_authenticated:
            return obj.get_user_role(user)
        return None

    def validate_name(self, value):
        """Validate organization name and generate slug"""
        if len(value.strip()) < 2:
            raise serializers.ValidationError("Organization name must be at least 2 characters long.")
        return value.strip()

    def create(self, validated_data):
        """Create organization with owner membership"""
        user = self.context['request'].user

        # Generate unique slug
        base_slug = slugify(validated_data['name'])
        slug = base_slug
        counter = 1
        while Organization.objects.filter(slug=slug).exists():
            slug = f"{base_slug}-{counter}"
            counter += 1

        validated_data['slug'] = slug
        validated_data['owner'] = user

        with transaction.atomic():
            # Create organization
            organization = Organization.objects.create(**validated_data)

            # Create owner role if it doesn't exist
            owner_role, created = Role.objects.get_or_create(
                name=Role.OWNER,
                defaults={
                    'description': 'Organization Owner with full access',
                    'can_manage_organization': True,
                    'can_manage_members': True,
                    'can_manage_api_keys': True,
                    'can_view_analytics': True,
                    'can_manage_billing': True,
                }
            )

            # Create owner membership
            OrganizationMember.objects.create(
                organization=organization,
                user=user,
                role=owner_role
            )

        return organization


class OrganizationDetailSerializer(OrganizationSerializer):
    """Detailed serializer for Organization with members"""
    members = OrganizationMemberSerializer(many=True, read_only=True)

    class Meta(OrganizationSerializer.Meta):
        fields = OrganizationSerializer.Meta.fields + ['members']


class InvitationSerializer(serializers.ModelSerializer):
    """Serializer for Invitation model"""
    organization = OrganizationSerializer(read_only=True)
    organization_id = serializers.IntegerField(write_only=True)
    role = RoleSerializer(read_only=True)
    role_id = serializers.IntegerField(write_only=True)
    invited_by = UserBasicSerializer(read_only=True)
    is_expired = serializers.ReadOnlyField()

    class Meta:
        model = Invitation
        fields = [
            'id', 'organization', 'organization_id', 'email', 'role', 'role_id',
            'invited_by', 'token', 'status', 'expires_at', 'is_expired',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'token', 'status', 'expires_at', 'created_at', 'updated_at']

    def validate(self, data):
        """Validate invitation data"""
        organization_id = data.get('organization_id')
        email = data.get('email')

        # Check if organization exists and user has permission
        try:
            organization = Organization.objects.get(id=organization_id)
        except Organization.DoesNotExist:
            raise serializers.ValidationError("Organization not found.")

        # Check if user can manage members
        user = self.context['request'].user
        user_role = organization.get_user_role(user)
        if not user_role or user_role.name not in [Role.OWNER, Role.ADMIN]:
            raise serializers.ValidationError("You don't have permission to invite members.")

        # Check if organization can add more members
        if not organization.can_add_member():
            raise serializers.ValidationError("Organization has reached maximum user limit.")

        # Check if user is already a member
        if organization.has_member(User.objects.filter(email=email).first()):
            raise serializers.ValidationError("User is already a member of this organization.")

        # Check for existing pending invitation
        if Invitation.objects.filter(
                organization=organization,
                email=email,
                status=Invitation.PENDING
        ).exists():
            raise serializers.ValidationError("Pending invitation already exists for this email.")

        return data

    def create(self, validated_data):
        """Create invitation with expiry"""
        organization_id = validated_data.pop('organization_id')
        role_id = validated_data.pop('role_id')

        organization = Organization.objects.get(id=organization_id)
        role = Role.objects.get(id=role_id)

        # Set expiry to 7 days from now
        expires_at = timezone.now() + timedelta(days=7)

        invitation = Invitation.objects.create(
            organization=organization,
            role=role,
            invited_by=self.context['request'].user,
            expires_at=expires_at,
            **validated_data
        )

        # TODO: Send invitation email here

        return invitation


class InvitationResponseSerializer(serializers.Serializer):
    """Serializer for accepting/declining invitations"""
    action = serializers.ChoiceField(choices=['accept', 'decline'])

    def validate_action(self, value):
        """Validate action"""
        if value not in ['accept', 'decline']:
            raise serializers.ValidationError("Action must be 'accept' or 'decline'.")
        return value


class OrganizationAPIKeySerializer(serializers.ModelSerializer):
    """Serializer for Organization API Keys"""
    organization = OrganizationSerializer(read_only=True)
    created_by = UserBasicSerializer(read_only=True)
    masked_key = serializers.ReadOnlyField()
    is_expired = serializers.ReadOnlyField()
    allowed_ips_list = serializers.ReadOnlyField(source='get_allowed_ips_list')

    # Write-only field to show full key only on creation
    key = serializers.CharField(read_only=True)

    class Meta:
        model = OrganizationAPIKey
        fields = [
            'id', 'organization', 'name', 'key', 'masked_key', 'prefix',
            'is_active', 'last_used_at', 'usage_count', 'rate_limit_per_hour',
            'rate_limit_per_day', 'allowed_ips', 'allowed_ips_list', 'expires_at',
            'is_expired', 'created_by', 'created_at', 'updated_at'
        ]
        read_only_fields = [
            'id', 'key', 'prefix', 'last_used_at', 'usage_count',
            'created_at', 'updated_at'
        ]

    def validate_name(self, value):
        """Validate API key name"""
        if len(value.strip()) < 3:
            raise serializers.ValidationError("API key name must be at least 3 characters long.")
        return value.strip()

    def validate_allowed_ips(self, value):
        """Validate IP addresses format"""
        if not value:
            return value

        import ipaddress
        ips = [ip.strip() for ip in value.split(',') if ip.strip()]

        for ip in ips:
            try:
                ipaddress.ip_address(ip)
            except ValueError:
                raise serializers.ValidationError(f"Invalid IP address: {ip}")

        return value

    def create(self, validated_data):
        """Create API key for organization"""
        # Organization will be set in the view
        validated_data['created_by'] = self.context['request'].user
        return super().create(validated_data)


class OrganizationAPIKeyCreateResponseSerializer(serializers.ModelSerializer):
    """Response serializer that shows the full API key only on creation"""
    organization = OrganizationSerializer(read_only=True)
    created_by = UserBasicSerializer(read_only=True)

    class Meta:
        model = OrganizationAPIKey
        fields = [
            'id', 'organization', 'name', 'key', 'prefix', 'is_active',
            'rate_limit_per_hour', 'rate_limit_per_day', 'allowed_ips',
            'expires_at', 'created_by', 'created_at'
        ]


class APIKeyUsageLogSerializer(serializers.ModelSerializer):
    """Serializer for API Key Usage Logs"""
    api_key_name = serializers.CharField(source='api_key.name', read_only=True)

    class Meta:
        model = APIKeyUsageLog
        fields = [
            'id', 'api_key_name', 'ip_address', 'user_agent', 'endpoint',
            'method', 'status_code', 'response_time_ms', 'created_at'
        ]


class OrganizationStatsSerializer(serializers.Serializer):
    """Serializer for organization statistics"""
    total_members = serializers.IntegerField()
    active_members = serializers.IntegerField()
    total_api_keys = serializers.IntegerField()
    active_api_keys = serializers.IntegerField()
    total_api_calls_today = serializers.IntegerField()
    total_api_calls_this_month = serializers.IntegerField()


class BulkMemberActionSerializer(serializers.Serializer):
    """Serializer for bulk member actions"""
    user_ids = serializers.ListField(
        child=serializers.IntegerField(),
        min_length=1,
        max_length=50
    )
    action = serializers.ChoiceField(choices=['remove', 'activate', 'deactivate'])

    def validate_user_ids(self, value):
        """Validate that all user IDs exist"""
        existing_ids = set(User.objects.filter(id__in=value).values_list('id', flat=True))
        if len(existing_ids) != len(value):
            raise serializers.ValidationError("Some user IDs do not exist.")
        return value