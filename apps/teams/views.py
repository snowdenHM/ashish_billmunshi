from django.shortcuts import get_object_or_404
from django.db.models import Count, Q
from django.utils import timezone
from django.db import transaction
from datetime import datetime, timedelta

from rest_framework import viewsets, status, permissions
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from drf_spectacular.utils import (
    extend_schema,
    extend_schema_view,
    OpenApiParameter,
    OpenApiResponse
)
from drf_spectacular.types import OpenApiTypes

from .models import (
    Organization,
    OrganizationMember,
    Role,
    Invitation,
    OrganizationAPIKey,
    APIKeyUsageLog
)
from .serializers import (
    OrganizationSerializer,
    OrganizationDetailSerializer,
    OrganizationMemberSerializer,
    RoleSerializer,
    InvitationSerializer,
    InvitationResponseSerializer,
    OrganizationAPIKeySerializer,
    OrganizationAPIKeyCreateResponseSerializer,
    APIKeyUsageLogSerializer,
    OrganizationStatsSerializer,
    BulkMemberActionSerializer,
    UserBasicSerializer
)
from .permissions import (
    IsOrganizationMember,
    IsOrganizationOwnerOrAdmin,
    IsOrganizationOwner,
    CanManageAPIKeys,
    CanManageMembers,
    CanViewAnalytics,
    IsOwnerOrReadOnly
)


@extend_schema_view(
    list=extend_schema(
        summary="List user's organizations",
        description="Get all organizations where the authenticated user is a member.",
        responses={200: OrganizationSerializer(many=True)}
    ),
    create=extend_schema(
        summary="Create new organization",
        description="Create a new organization. The authenticated user becomes the owner.",
        responses={201: OrganizationSerializer}
    ),
    retrieve=extend_schema(
        summary="Get organization details",
        description="Get detailed information about a specific organization.",
        responses={200: OrganizationDetailSerializer}
    ),
    update=extend_schema(
        summary="Update organization",
        description="Update organization details. Only owners and admins can update.",
        responses={200: OrganizationSerializer}
    ),
    partial_update=extend_schema(
        summary="Partially update organization",
        description="Partially update organization details. Only owners and admins can update.",
        responses={200: OrganizationSerializer}
    ),
    destroy=extend_schema(
        summary="Delete organization",
        description="Delete organization. Only owners can delete.",
        responses={204: None}
    )
)
class OrganizationViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing organizations
    """
    serializer_class = OrganizationSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        """Return organizations where user is a member"""
        return Organization.objects.filter(
            members__user=self.request.user,
            members__is_active=True
        ).distinct().order_by('-created_at')

    def get_serializer_class(self):
        """Return appropriate serializer based on action"""
        if self.action == 'retrieve':
            return OrganizationDetailSerializer
        return OrganizationSerializer

    def get_permissions(self):
        """Set permissions based on action"""
        if self.action in ['update', 'partial_update']:
            return [permissions.IsAuthenticated(), IsOrganizationOwnerOrAdmin()]
        elif self.action == 'destroy':
            return [permissions.IsAuthenticated(), IsOrganizationOwner()]
        return super().get_permissions()

    @extend_schema(
        summary="Get organization statistics",
        description="Get statistics for the organization including member count, API usage, etc.",
        responses={200: OrganizationStatsSerializer}
    )
    @action(detail=True, methods=['get'], permission_classes=[IsOrganizationMember, CanViewAnalytics])
    def stats(self, request, pk=None):
        """Get organization statistics"""
        organization = self.get_object()
        today = timezone.now().date()
        month_start = today.replace(day=1)

        # Calculate statistics
        stats = {
            'total_members': organization.members.count(),
            'active_members': organization.members.filter(is_active=True).count(),
            'total_api_keys': organization.api_keys.count(),
            'active_api_keys': organization.api_keys.filter(is_active=True).count(),
            'total_api_calls_today': APIKeyUsageLog.objects.filter(
                api_key__organization=organization,
                created_at__date=today
            ).count(),
            'total_api_calls_this_month': APIKeyUsageLog.objects.filter(
                api_key__organization=organization,
                created_at__date__gte=month_start
            ).count(),
        }

        serializer = OrganizationStatsSerializer(stats)
        return Response(serializer.data)

    @extend_schema(
        summary="Get organization members",
        description="Get all members of the organization.",
        responses={200: OrganizationMemberSerializer(many=True)}
    )
    @action(detail=True, methods=['get'], permission_classes=[IsOrganizationMember])
    def members(self, request, pk=None):
        """Get organization members"""
        organization = self.get_object()
        members = organization.members.filter(is_active=True).select_related('user', 'role', 'invited_by')
        serializer = OrganizationMemberSerializer(members, many=True)
        return Response(serializer.data)

    @extend_schema(
        summary="Remove member from organization",
        description="Remove a member from the organization. Only owners and admins can remove members.",
        parameters=[
            OpenApiParameter(
                name='user_id',
                type=OpenApiTypes.INT,
                location=OpenApiParameter.PATH,
                description='ID of the user to remove'
            )
        ],
        responses={204: None}
    )
    @action(detail=True, methods=['delete'], url_path='members/(?P<user_id>[^/.]+)',
            permission_classes=[IsOrganizationMember, CanManageMembers])
    def remove_member(self, request, pk=None, user_id=None):
        """Remove member from organization"""
        organization = self.get_object()

        try:
            member = organization.members.get(user_id=user_id, is_active=True)

            # Prevent removing the owner
            if member.role.name == Role.OWNER:
                return Response(
                    {'error': 'Cannot remove organization owner'},
                    status=status.HTTP_400_BAD_REQUEST
                )

            # Prevent non-owners from removing admins
            user_role = organization.get_user_role(request.user)
            if member.role.name == Role.ADMIN and user_role.name != Role.OWNER:
                return Response(
                    {'error': 'Only owners can remove admins'},
                    status=status.HTTP_403_FORBIDDEN
                )

            member.is_active = False
            member.save()

            return Response(status=status.HTTP_204_NO_CONTENT)

        except OrganizationMember.DoesNotExist:
            return Response(
                {'error': 'Member not found'},
                status=status.HTTP_404_NOT_FOUND
            )

    @extend_schema(
        summary="Bulk member actions",
        description="Perform bulk actions on multiple members.",
        request=BulkMemberActionSerializer,
        responses={200: {'description': 'Action completed successfully'}}
    )
    @action(detail=True, methods=['post'], url_path='members/bulk-action',
            permission_classes=[IsOrganizationMember, CanManageMembers])
    def bulk_member_action(self, request, pk=None):
        """Perform bulk actions on members"""
        organization = self.get_object()
        serializer = BulkMemberActionSerializer(data=request.data)

        if serializer.is_valid():
            user_ids = serializer.validated_data['user_ids']
            action = serializer.validated_data['action']

            members = organization.members.filter(user_id__in=user_ids)

            if action == 'remove':
                # Prevent removing owners
                if members.filter(role__name=Role.OWNER).exists():
                    return Response(
                        {'error': 'Cannot remove organization owners'},
                        status=status.HTTP_400_BAD_REQUEST
                    )
                members.update(is_active=False)

            elif action == 'activate':
                members.update(is_active=True)

            elif action == 'deactivate':
                # Prevent deactivating owners
                if members.filter(role__name=Role.OWNER).exists():
                    return Response(
                        {'error': 'Cannot deactivate organization owners'},
                        status=status.HTTP_400_BAD_REQUEST
                    )
                members.update(is_active=False)

            return Response({'message': f'{action.title()} action completed successfully'})

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@extend_schema_view(
    list=extend_schema(
        summary="List invitations",
        description="Get all invitations for the organization.",
        responses={200: InvitationSerializer(many=True)}
    ),
    create=extend_schema(
        summary="Send invitation",
        description="Send an invitation to join the organization.",
        responses={201: InvitationSerializer}
    ),
    retrieve=extend_schema(
        summary="Get invitation details",
        description="Get details of a specific invitation.",
        responses={200: InvitationSerializer}
    ),
    destroy=extend_schema(
        summary="Cancel invitation",
        description="Cancel a pending invitation.",
        responses={204: None}
    )
)
class InvitationViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing invitations
    """
    serializer_class = InvitationSerializer
    permission_classes = [permissions.IsAuthenticated, IsOrganizationMember, CanManageMembers]
    http_method_names = ['get', 'post', 'delete']

    def get_queryset(self):
        """Return invitations for the organization"""
        org_id = self.kwargs.get('organization_id')
        return Invitation.objects.filter(
            organization_id=org_id
        ).select_related('organization', 'role', 'invited_by').order_by('-created_at')

    def perform_create(self, serializer):
        """Create invitation for the organization"""
        org_id = self.kwargs.get('organization_id')
        organization = get_object_or_404(Organization, id=org_id)
        serializer.save(organization=organization)


@extend_schema(
    summary="Respond to invitation",
    description="Accept or decline an invitation using the invitation token.",
    request=InvitationResponseSerializer,
    responses={
        200: {'description': 'Invitation accepted successfully'},
        400: {'description': 'Invalid action or invitation'}
    }
)
class InvitationResponseView(APIView):
    """
    View for accepting/declining invitations
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, token):
        """Accept or decline invitation"""
        try:
            invitation = Invitation.objects.get(token=token)
        except Invitation.DoesNotExist:
            return Response(
                {'error': 'Invalid invitation token'},
                status=status.HTTP_404_NOT_FOUND
            )

        serializer = InvitationResponseSerializer(data=request.data)
        if serializer.is_valid():
            action = serializer.validated_data['action']

            try:
                if action == 'accept':
                    invitation.accept(request.user)
                    return Response({'message': 'Invitation accepted successfully'})
                else:  # decline
                    invitation.decline()
                    return Response({'message': 'Invitation declined'})

            except Exception as e:
                return Response(
                    {'error': str(e)},
                    status=status.HTTP_400_BAD_REQUEST
                )

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@extend_schema_view(
    list=extend_schema(
        summary="List organization API keys",
        description="Get all API keys for the organization.",
        responses={200: OrganizationAPIKeySerializer(many=True)}
    ),
    create=extend_schema(
        summary="Create API key",
        description="Create a new API key for the organization.",
        responses={201: OrganizationAPIKeyCreateResponseSerializer}
    ),
    retrieve=extend_schema(
        summary="Get API key details",
        description="Get details of a specific API key.",
        responses={200: OrganizationAPIKeySerializer}
    ),
    update=extend_schema(
        summary="Update API key",
        description="Update API key settings.",
        responses={200: OrganizationAPIKeySerializer}
    ),
    partial_update=extend_schema(
        summary="Partially update API key",
        description="Partially update API key settings.",
        responses={200: OrganizationAPIKeySerializer}
    ),
    destroy=extend_schema(
        summary="Delete API key",
        description="Delete an API key.",
        responses={204: None}
    )
)
class OrganizationAPIKeyViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing organization API keys
    """
    serializer_class = OrganizationAPIKeySerializer
    permission_classes = [permissions.IsAuthenticated, IsOrganizationMember, CanManageAPIKeys]

    def get_queryset(self):
        """Return API keys for the organization"""
        org_id = self.kwargs.get('organization_id')
        return OrganizationAPIKey.objects.filter(
            organization_id=org_id
        ).select_related('organization', 'created_by').order_by('-created_at')

    def get_serializer_class(self):
        """Return appropriate serializer based on action"""
        if self.action == 'create':
            return OrganizationAPIKeyCreateResponseSerializer
        return OrganizationAPIKeySerializer

    def perform_create(self, serializer):
        """Create API key for the organization"""
        org_id = self.kwargs.get('organization_id')
        organization = get_object_or_404(Organization, id=org_id)
        serializer.save(organization=organization)

    @extend_schema(
        summary="Regenerate API key",
        description="Regenerate the API key with a new value.",
        responses={200: OrganizationAPIKeyCreateResponseSerializer}
    )
    @action(detail=True, methods=['post'])
    def regenerate(self, request, organization_id=None, pk=None):
        """Regenerate API key"""
        api_key = self.get_object()

        # Generate new key
        api_key.key = OrganizationAPIKey.generate_key()
        api_key.prefix = api_key.key[:8]
        api_key.save()

        serializer = OrganizationAPIKeyCreateResponseSerializer(api_key)
        return Response(serializer.data)

    @extend_schema(
        summary="Get API key usage statistics",
        description="Get usage statistics for the API key.",
        parameters=[
            OpenApiParameter(
                name='days',
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                description='Number of days to look back (default: 30)'
            )
        ],
        responses={200: APIKeyUsageLogSerializer(many=True)}
    )
    @action(detail=True, methods=['get'], url_path='usage')
    def usage_stats(self, request, organization_id=None, pk=None):
        """Get API key usage statistics"""
        api_key = self.get_object()
        days = int(request.query_params.get('days', 30))

        start_date = timezone.now() - timedelta(days=days)
        usage_logs = api_key.usage_logs.filter(
            created_at__gte=start_date
        ).order_by('-created_at')[:1000]  # Limit to last 1000 entries

        serializer = APIKeyUsageLogSerializer(usage_logs, many=True)
        return Response(serializer.data)


@extend_schema_view(
    list=extend_schema(
        summary="List all roles",
        description="Get all available roles in the system.",
        responses={200: RoleSerializer(many=True)}
    )
)
class RoleViewSet(viewsets.ReadOnlyModelViewSet):
    """
    ViewSet for viewing roles
    """
    queryset = Role.objects.all()
    serializer_class = RoleSerializer
    permission_classes = [permissions.IsAuthenticated]


class OrganizationMemberViewSet(viewsets.ReadOnlyModelViewSet):
    """
    ViewSet for viewing organization members
    """
    serializer_class = OrganizationMemberSerializer
    permission_classes = [permissions.IsAuthenticated, IsOrganizationMember]

    def get_queryset(self):
        """Return members for the organization"""
        org_id = self.kwargs.get('organization_id')
        return OrganizationMember.objects.filter(
            organization_id=org_id,
            is_active=True
        ).select_related('user', 'role', 'invited_by').order_by('-created_at')


@extend_schema(
    summary="Check organization slug availability",
    description="Check if an organization slug is available.",
    parameters=[
        OpenApiParameter(
            name='slug',
            type=OpenApiTypes.STR,
            location=OpenApiParameter.QUERY,
            description='Slug to check'
        )
    ],
    responses={
        200: {'description': 'Slug availability status'},
        400: {'description': 'Slug parameter required'}
    }
)
class CheckSlugAvailabilityView(APIView):
    """
    View to check organization slug availability
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        """Check if slug is available"""
        slug = request.query_params.get('slug')
        if not slug:
            return Response(
                {'error': 'Slug parameter is required'},
                status=status.HTTP_400_BAD_REQUEST
            )

        is_available = not Organization.objects.filter(slug=slug).exists()
        return Response({'available': is_available, 'slug': slug})


@extend_schema(
    summary="Search users by email",
    description="Search for users by email address for inviting to organization.",
    parameters=[
        OpenApiParameter(
            name='email',
            type=OpenApiTypes.STR,
            location=OpenApiParameter.QUERY,
            description='Email to search for'
        )
    ],
    responses={
        200: UserBasicSerializer(many=True),
        400: {'description': 'Email parameter required'}
    }
)
class SearchUsersView(APIView):
    """
    View to search users by email for invitations
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        """Search users by email"""
        email = request.query_params.get('email')
        if not email:
            return Response(
                {'error': 'Email parameter is required'},
                status=status.HTTP_400_BAD_REQUEST
            )

        from django.contrib.auth import get_user_model
        User = get_user_model()

        users = User.objects.filter(
            email__icontains=email
        )[:10]  # Limit to 10 results

        serializer = UserBasicSerializer(users, many=True)
        return Response(serializer.data)
