from django.shortcuts import get_object_or_404
from django.db.models import Count, Q
from django.utils import timezone
from django.contrib.auth import get_user_model
from django.contrib.sessions.models import Session
from datetime import timedelta

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
    UserPreference,
    UserActivity,
    UserSession,
    UserNotification
)
from .serializers import (
    UserProfileSerializer,
    UserDetailSerializer,
    UserBasicSerializer,
    UserPreferenceSerializer,
    UserActivitySerializer,
    UserSessionSerializer,
    UserNotificationSerializer,
    PasswordChangeSerializer,
    UserOrganizationSerializer,
    BulkNotificationActionSerializer,
    UserStatsSerializer,
    UserOnboardingSerializer
)
from .permissions import IsOwnerOrReadOnly, CanViewUserData

User = get_user_model()


@extend_schema_view(
    retrieve=extend_schema(
        summary="Get current user profile",
        description="Get detailed information about the authenticated user.",
        responses={200: UserDetailSerializer}
    ),
    update=extend_schema(
        summary="Update user profile",
        description="Update the authenticated user's profile information.",
        responses={200: UserProfileSerializer}
    ),
    partial_update=extend_schema(
        summary="Partially update user profile",
        description="Partially update the authenticated user's profile information.",
        responses={200: UserProfileSerializer}
    )
)
class UserProfileViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing user profiles
    """
    serializer_class = UserProfileSerializer
    permission_classes = [permissions.IsAuthenticated]
    http_method_names = ['get', 'put', 'patch']

    def get_object(self):
        """Return the current user"""
        return self.request.user

    def get_serializer_class(self):
        """Return appropriate serializer based on action"""
        if self.action == 'retrieve':
            return UserDetailSerializer
        return UserProfileSerializer

    @extend_schema(
        summary="Get user statistics",
        description="Get statistics about the user's account and activity.",
        responses={200: UserStatsSerializer}
    )
    @action(detail=False, methods=['get'])
    def stats(self, request):
        """Get user statistics"""
        user = request.user
        today = timezone.now().date()
        account_age = (today - user.date_joined.date()).days

        stats = {
            'total_organizations': user.total_organizations,
            'owned_organizations': user.owned_organizations_count,
            'total_notifications': user.notifications.count(),
            'unread_notifications': user.notifications.filter(is_read=False).count(),
            'recent_activities_count': user.activities.filter(
                created_at__gte=timezone.now() - timedelta(days=30)
            ).count(),
            'active_sessions_count': user.sessions.filter(
                is_active=True,
                expires_at__gt=timezone.now()
            ).count(),
            'account_age_days': account_age,
        }

        serializer = UserStatsSerializer(stats)
        return Response(serializer.data)

    @extend_schema(
        summary="Get user's organizations",
        description="Get all organizations the user is a member of.",
        responses={200: UserOrganizationSerializer(many=True)}
    )
    @action(detail=False, methods=['get'])
    def organizations(self, request):
        """Get user's organization memberships"""
        memberships = request.user.get_organizations()
        serializer = UserOrganizationSerializer(memberships, many=True, context={'request': request})
        return Response(serializer.data)

    @extend_schema(
        summary="Change password",
        description="Change the user's password.",
        request=PasswordChangeSerializer,
        responses={200: {'description': 'Password changed successfully'}}
    )
    @action(detail=False, methods=['post'])
    def change_password(self, request):
        """Change user password"""
        serializer = PasswordChangeSerializer(data=request.data, context={'request': request})

        if serializer.is_valid():
            user = serializer.save()

            # Log password change activity
            UserActivity.objects.create(
                user=user,
                action='password_change',
                description='Password changed successfully',
                ip_address=self.get_client_ip(request)
            )

            return Response({'message': 'Password changed successfully'})

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @extend_schema(
        summary="Complete onboarding",
        description="Complete the user onboarding process.",
        request=UserOnboardingSerializer,
        responses={200: UserDetailSerializer}
    )
    @action(detail=False, methods=['post'])
    def complete_onboarding(self, request):
        """Complete user onboarding"""
        if request.user.is_onboarded:
            return Response(
                {'message': 'User already onboarded'},
                status=status.HTTP_400_BAD_REQUEST
            )

        serializer = UserOnboardingSerializer(data=request.data, context={'request': request})

        if serializer.is_valid():
            user = serializer.save()
            response_serializer = UserDetailSerializer(user, context={'request': request})
            return Response(response_serializer.data)

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def get_client_ip(self, request):
        """Get client IP address"""
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded_for:
            ip = x_forwarded_for.split(',')[0]
        else:
            ip = request.META.get('REMOTE_ADDR')
        return ip


@extend_schema_view(
    retrieve=extend_schema(
        summary="Get user preferences",
        description="Get the authenticated user's preferences.",
        responses={200: UserPreferenceSerializer}
    ),
    update=extend_schema(
        summary="Update user preferences",
        description="Update the authenticated user's preferences.",
        responses={200: UserPreferenceSerializer}
    ),
    partial_update=extend_schema(
        summary="Partially update user preferences",
        description="Partially update the authenticated user's preferences.",
        responses={200: UserPreferenceSerializer}
    )
)
class UserPreferenceViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing user preferences
    """
    serializer_class = UserPreferenceSerializer
    permission_classes = [permissions.IsAuthenticated]
    http_method_names = ['get', 'put', 'patch']

    def get_object(self):
        """Get or create user preferences"""
        preferences, created = UserPreference.objects.get_or_create(
            user=self.request.user
        )
        return preferences


@extend_schema_view(
    list=extend_schema(
        summary="List user activities",
        description="Get the authenticated user's activity history.",
        parameters=[
            OpenApiParameter(
                name='action',
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                description='Filter by action type'
            ),
            OpenApiParameter(
                name='days',
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                description='Number of days to look back (default: 30)'
            )
        ],
        responses={200: UserActivitySerializer(many=True)}
    )
)
class UserActivityViewSet(viewsets.ReadOnlyModelViewSet):
    """
    ViewSet for viewing user activities
    """
    serializer_class = UserActivitySerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        """Return user's activities with optional filtering"""
        queryset = self.request.user.activities.select_related('organization')

        # Filter by action type
        action = self.request.query_params.get('action')
        if action:
            queryset = queryset.filter(action=action)

        # Filter by days
        days = self.request.query_params.get('days')
        if days:
            try:
                days = int(days)
                start_date = timezone.now() - timedelta(days=days)
                queryset = queryset.filter(created_at__gte=start_date)
            except ValueError:
                pass
        else:
            # Default to last 30 days
            start_date = timezone.now() - timedelta(days=30)
            queryset = queryset.filter(created_at__gte=start_date)

        return queryset.order_by('-created_at')


@extend_schema_view(
    list=extend_schema(
        summary="List user sessions",
        description="Get the authenticated user's active sessions.",
        responses={200: UserSessionSerializer(many=True)}
    ),
    destroy=extend_schema(
        summary="Terminate session",
        description="Terminate a specific user session.",
        responses={204: None}
    )
)
class UserSessionViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing user sessions
    """
    serializer_class = UserSessionSerializer
    permission_classes = [permissions.IsAuthenticated]
    http_method_names = ['get', 'delete']

    def get_queryset(self):
        """Return user's active sessions"""
        return self.request.user.sessions.filter(
            is_active=True,
            expires_at__gt=timezone.now()
        ).order_by('-last_activity')

    def destroy(self, request, *args, **kwargs):
        """Terminate a session"""
        session = self.get_object()

        # Don't allow terminating current session
        if hasattr(request, 'session') and request.session.session_key == session.session_key:
            return Response(
                {'error': 'Cannot terminate current session'},
                status=status.HTTP_400_BAD_REQUEST
            )

        session.terminate()

        # Also remove Django session
        try:
            django_session = Session.objects.get(session_key=session.session_key)
            django_session.delete()
        except Session.DoesNotExist:
            pass

        return Response(status=status.HTTP_204_NO_CONTENT)

    @extend_schema(
        summary="Terminate all other sessions",
        description="Terminate all user sessions except the current one.",
        responses={200: {'description': 'Sessions terminated successfully'}}
    )
    @action(detail=False, methods=['post'])
    def terminate_all_others(self, request):
        """Terminate all other sessions"""
        current_session_key = getattr(request.session, 'session_key', None)

        # Terminate all user sessions except current
        sessions_to_terminate = self.get_queryset()
        if current_session_key:
            sessions_to_terminate = sessions_to_terminate.exclude(
                session_key=current_session_key
            )

        terminated_count = 0
        for session in sessions_to_terminate:
            session.terminate()

            # Also remove Django session
            try:
                django_session = Session.objects.get(session_key=session.session_key)
                django_session.delete()
                terminated_count += 1
            except Session.DoesNotExist:
                pass

        return Response({
            'message': f'Terminated {terminated_count} sessions successfully'
        })


@extend_schema_view(
    list=extend_schema(
        summary="List user notifications",
        description="Get the authenticated user's notifications.",
        parameters=[
            OpenApiParameter(
                name='is_read',
                type=OpenApiTypes.BOOL,
                location=OpenApiParameter.QUERY,
                description='Filter by read status'
            ),
            OpenApiParameter(
                name='notification_type',
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                description='Filter by notification type'
            )
        ],
        responses={200: UserNotificationSerializer(many=True)}
    ),
    retrieve=extend_schema(
        summary="Get notification details",
        description="Get details of a specific notification and mark it as read.",
        responses={200: UserNotificationSerializer}
    ),
    destroy=extend_schema(
        summary="Delete notification",
        description="Delete a specific notification.",
        responses={204: None}
    )
)
class UserNotificationViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing user notifications
    """
    serializer_class = UserNotificationSerializer
    permission_classes = [permissions.IsAuthenticated, IsOwnerOrReadOnly]
    http_method_names = ['get', 'patch', 'delete']

    def get_queryset(self):
        """Return user's notifications with optional filtering"""
        queryset = self.request.user.notifications.select_related('organization')

        # Filter by read status
        is_read = self.request.query_params.get('is_read')
        if is_read is not None:
            is_read = is_read.lower() == 'true'
            queryset = queryset.filter(is_read=is_read)

        # Filter by notification type
        notification_type = self.request.query_params.get('notification_type')
        if notification_type:
            queryset = queryset.filter(notification_type=notification_type)

        return queryset.order_by('-created_at')

    def retrieve(self, request, *args, **kwargs):
        """Get notification and mark as read"""
        instance = self.get_object()

        # Mark as read if not already read
        if not instance.is_read:
            instance.mark_as_read()

        serializer = self.get_serializer(instance)
        return Response(serializer.data)

    @extend_schema(
        summary="Mark notification as read",
        description="Mark a specific notification as read.",
        responses={200: UserNotificationSerializer}
    )
    @action(detail=True, methods=['patch'])
    def mark_read(self, request, pk=None):
        """Mark notification as read"""
        notification = self.get_object()
        notification.mark_as_read()
        serializer = self.get_serializer(notification)
        return Response(serializer.data)

    @extend_schema(
        summary="Mark notification as unread",
        description="Mark a specific notification as unread.",
        responses={200: UserNotificationSerializer}
    )
    @action(detail=True, methods=['patch'])
    def mark_unread(self, request, pk=None):
        """Mark notification as unread"""
        notification = self.get_object()
        notification.is_read = False
        notification.read_at = None
        notification.save(update_fields=['is_read', 'read_at'])

        serializer = self.get_serializer(notification)
        return Response(serializer.data)

    @extend_schema(
        summary="Mark all notifications as read",
        description="Mark all user notifications as read.",
        responses={200: {'description': 'All notifications marked as read'}}
    )
    @action(detail=False, methods=['post'])
    def mark_all_read(self, request):
        """Mark all notifications as read"""
        updated_count = self.request.user.notifications.filter(
            is_read=False
        ).update(
            is_read=True,
            read_at=timezone.now()
        )

        return Response({
            'message': f'Marked {updated_count} notifications as read'
        })

    @extend_schema(
        summary="Bulk notification actions",
        description="Perform bulk actions on multiple notifications.",
        request=BulkNotificationActionSerializer,
        responses={200: {'description': 'Bulk action completed successfully'}}
    )
    @action(detail=False, methods=['post'])
    def bulk_action(self, request):
        """Perform bulk actions on notifications"""
        serializer = BulkNotificationActionSerializer(
            data=request.data,
            context={'request': request}
        )

        if serializer.is_valid():
            notification_ids = serializer.validated_data['notification_ids']
            action = serializer.validated_data['action']

            notifications = request.user.notifications.filter(id__in=notification_ids)

            if action == 'mark_read':
                notifications.update(
                    is_read=True,
                    read_at=timezone.now()
                )
                message = f'Marked {len(notification_ids)} notifications as read'

            elif action == 'mark_unread':
                notifications.update(
                    is_read=False,
                    read_at=None
                )
                message = f'Marked {len(notification_ids)} notifications as unread'

            elif action == 'delete':
                notifications.delete()
                message = f'Deleted {len(notification_ids)} notifications'

            return Response({'message': message})

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@extend_schema(
    summary="Search users",
    description="Search for users by email or name (for admins and organization managers).",
    parameters=[
        OpenApiParameter(
            name='q',
            type=OpenApiTypes.STR,
            location=OpenApiParameter.QUERY,
            description='Search query (email, first name, or last name)',
            required=True
        ),
        OpenApiParameter(
            name='limit',
            type=OpenApiTypes.INT,
            location=OpenApiParameter.QUERY,
            description='Maximum number of results (default: 10, max: 50)'
        )
    ],
    responses={200: UserBasicSerializer(many=True)}
)
class UserSearchView(APIView):
    """
    View for searching users
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        """Search for users"""
        query = request.query_params.get('q')
        if not query or len(query.strip()) < 2:
            return Response(
                {'error': 'Search query must be at least 2 characters long'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Get limit parameter
        try:
            limit = int(request.query_params.get('limit', 10))
            limit = min(limit, 50)  # Max 50 results
        except ValueError:
            limit = 10

        # Search users
        users = User.objects.filter(
            Q(email__icontains=query) |
            Q(first_name__icontains=query) |
            Q(last_name__icontains=query)
        ).exclude(
            id=request.user.id  # Exclude current user
        )[:limit]

        serializer = UserBasicSerializer(users, many=True)
        return Response(serializer.data)


@extend_schema(
    summary="Get user by ID",
    description="Get basic information about a user by ID (for organization members).",
    responses={200: UserBasicSerializer}
)
class UserDetailView(APIView):
    """
    View for getting user details by ID
    """
    permission_classes = [permissions.IsAuthenticated, CanViewUserData]

    def get(self, request, user_id):
        """Get user by ID"""
        user = get_object_or_404(User, id=user_id)

        # Check if user can view this user's data
        self.check_object_permissions(request, user)

        serializer = UserBasicSerializer(user)
        return Response(serializer.data)


@extend_schema(
    summary="Delete user account",
    description="Permanently delete the authenticated user's account.",
    request=None,
    responses={204: None}
)
class DeleteAccountView(APIView):
    """
    View for deleting user account
    """
    permission_classes = [permissions.IsAuthenticated]

    def delete(self, request):
        """Delete user account"""
        user = request.user

        # Check if user owns any organizations
        owned_orgs = user.owned_organizations.filter(is_active=True)
        if owned_orgs.exists():
            org_names = [org.name for org in owned_orgs[:3]]
            return Response({
                'error': 'Cannot delete account while owning organizations',
                'owned_organizations': org_names,
                'message': 'Please transfer ownership or delete your organizations first'
            }, status=status.HTTP_400_BAD_REQUEST)

        # Log account deletion
        UserActivity.objects.create(
            user=user,
            action='account_delete',
            description='User account deleted',
            ip_address=self.get_client_ip(request)
        )

        # Delete user account
        user.delete()

        return Response(status=status.HTTP_204_NO_CONTENT)

    def get_client_ip(self, request):
        """Get client IP address"""
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded_for:
            ip = x_forwarded_for.split(',')[0]
        else:
            ip = request.META.get('REMOTE_ADDR')
        return ip