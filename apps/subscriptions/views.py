from django.shortcuts import get_object_or_404
from django.db.models import Count, Sum, Q, F
from django.utils import timezone
from django.db import transaction
from datetime import timedelta, datetime
from decimal import Decimal

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

from apps.teams.permissions import IsOrganizationMember, IsOrganizationOwnerOrAdmin

from .models import (
    SubscriptionPlan,
    OrganizationSubscription,
    SubscriptionFeature,
    PlanFeature,
    UsageRecord,
    SubscriptionInvoice,
    SubscriptionEvent,
    SubscriptionDiscount
)
from .serializers import (
    SubscriptionPlanSerializer,
    SubscriptionPlanPublicSerializer,
    OrganizationSubscriptionSerializer,
    SubscriptionCreateSerializer,
    SubscriptionUpdateSerializer,
    PlanChangeSerializer,
    SubscriptionInvoiceSerializer,
    SubscriptionEventSerializer,
    SubscriptionDiscountSerializer,
    ValidateDiscountSerializer,
    UsageRecordSerializer,
    UsageStatsSerializer,
    SubscriptionSummarySerializer,
    BulkUsageUpdateSerializer,
    TrialExtensionSerializer,
    CancelSubscriptionSerializer,
    ReactivateSubscriptionSerializer,
    SubscriptionAnalyticsSerializer
)
from .permissions import CanManageSubscription, CanViewSubscription
from .utils import SubscriptionManager, UsageTracker, BillingCalculator


@extend_schema_view(
    list=extend_schema(
        summary="List subscription plans",
        description="Get all available subscription plans.",
        responses={200: SubscriptionPlanPublicSerializer(many=True)}
    ),
    retrieve=extend_schema(
        summary="Get subscription plan details",
        description="Get detailed information about a specific subscription plan.",
        responses={200: SubscriptionPlanPublicSerializer}
    )
)
class SubscriptionPlanViewSet(viewsets.ReadOnlyModelViewSet):
    """
    ViewSet for viewing subscription plans
    """
    serializer_class = SubscriptionPlanPublicSerializer
    permission_classes = [permissions.AllowAny]

    def get_queryset(self):
        """Return active public plans"""
        return SubscriptionPlan.objects.filter(
            is_active=True,
            is_public=True
        ).order_by('sort_order', 'price')

    @extend_schema(
        summary="Compare subscription plans",
        description="Get a comparison matrix of all available plans.",
        responses={200: SubscriptionPlanPublicSerializer(many=True)}
    )
    @action(detail=False, methods=['get'])
    def compare(self, request):
        """Get plan comparison data"""
        plans = self.get_queryset()
        serializer = self.get_serializer(plans, many=True)

        # Add comparison metadata
        comparison_data = {
            'plans': serializer.data,
            'comparison_features': [
                'max_users', 'max_organizations', 'max_api_calls_per_month',
                'max_api_keys', 'max_storage_gb', 'custom_branding',
                'priority_support', 'advanced_analytics', 'sso_integration'
            ]
        }

        return Response(comparison_data)


@extend_schema_view(
    list=extend_schema(
        summary="List organization subscriptions",
        description="Get subscriptions for organizations where user is a member.",
        responses={200: OrganizationSubscriptionSerializer(many=True)}
    ),
    create=extend_schema(
        summary="Create subscription",
        description="Create a new subscription for an organization.",
        request=SubscriptionCreateSerializer,
        responses={201: OrganizationSubscriptionSerializer}
    ),
    retrieve=extend_schema(
        summary="Get subscription details",
        description="Get detailed information about a specific subscription.",
        responses={200: OrganizationSubscriptionSerializer}
    ),
    update=extend_schema(
        summary="Update subscription",
        description="Update subscription details.",
        request=SubscriptionUpdateSerializer,
        responses={200: OrganizationSubscriptionSerializer}
    ),
    partial_update=extend_schema(
        summary="Partially update subscription",
        description="Partially update subscription details.",
        request=SubscriptionUpdateSerializer,
        responses={200: OrganizationSubscriptionSerializer}
    )
)
class OrganizationSubscriptionViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing organization subscriptions
    """
    serializer_class = OrganizationSubscriptionSerializer
    permission_classes = [permissions.IsAuthenticated, IsOrganizationMember, CanViewSubscription]

    def get_queryset(self):
        """Return subscriptions for user's organizations"""
        user_org_ids = self.request.user.organization_memberships.filter(
            is_active=True
        ).values_list('organization_id', flat=True)

        return OrganizationSubscription.objects.filter(
            organization_id__in=user_org_ids
        ).select_related('organization', 'plan').order_by('-created_at')

    def get_permissions(self):
        """Set permissions based on action"""
        if self.action in ['create', 'update', 'partial_update', 'destroy']:
            return [permissions.IsAuthenticated(), IsOrganizationOwnerOrAdmin(), CanManageSubscription()]
        return super().get_permissions()

    def get_serializer_class(self):
        """Return appropriate serializer based on action"""
        if self.action == 'create':
            return SubscriptionCreateSerializer
        elif self.action in ['update', 'partial_update']:
            return SubscriptionUpdateSerializer
        return OrganizationSubscriptionSerializer

    def create(self, request):
        """Create a new subscription"""
        org_id = request.data.get('organization_id')
        if not org_id:
            return Response(
                {'error': 'organization_id is required'},
                status=status.HTTP_400_BAD_REQUEST
            )

        organization = get_object_or_404(Organization, id=org_id)

        # Check if user can manage this organization
        if not organization.has_member(request.user):
            return Response(
                {'error': 'You are not a member of this organization'},
                status=status.HTTP_403_FORBIDDEN
            )

        serializer = self.get_serializer(
            data=request.data,
            context={'organization': organization, 'user': request.user}
        )

        if serializer.is_valid():
            subscription = SubscriptionManager.create_subscription(
                organization=organization,
                plan_id=serializer.validated_data['plan_id'],
                user=request.user,
                discount_code=serializer.validated_data.get('discount_code'),
                trial_days=serializer.validated_data.get('trial_days')
            )

            response_serializer = OrganizationSubscriptionSerializer(subscription)
            return Response(response_serializer.data, status=status.HTTP_201_CREATED)

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @extend_schema(
        summary="Get subscription summary",
        description="Get comprehensive subscription summary with usage and billing info.",
        responses={200: SubscriptionSummarySerializer}
    )
    @action(detail=True, methods=['get'])
    def summary(self, request, pk=None):
        """Get subscription summary"""
        subscription = self.get_object()

        # Get current usage
        usage_summary = subscription.get_usage_summary()

        # Get upcoming invoice
        upcoming_invoice = subscription.invoices.filter(
            status='draft',
            due_date__gte=timezone.now()
        ).first()

        # Get recent events
        recent_events = subscription.events.all()[:10]

        # Check for usage alerts
        usage_alerts = []
        for usage_type, usage_data in usage_summary.items():
            if usage_data['percentage'] > 80:
                usage_alerts.append({
                    'type': usage_type,
                    'message': f"{usage_type.title()} usage is at {usage_data['percentage']:.1f}%",
                    'severity': 'warning' if usage_data['percentage'] < 100 else 'critical'
                })

        summary_data = {
            'subscription': OrganizationSubscriptionSerializer(subscription).data,
            'current_usage': usage_summary,
            'upcoming_invoice': SubscriptionInvoiceSerializer(upcoming_invoice).data if upcoming_invoice else None,
            'recent_events': SubscriptionEventSerializer(recent_events, many=True).data,
            'usage_alerts': usage_alerts
        }

        serializer = SubscriptionSummarySerializer(summary_data)
        return Response(serializer.data)

    @extend_schema(
        summary="Change subscription plan",
        description="Change the subscription to a different plan.",
        request=PlanChangeSerializer,
        responses={200: OrganizationSubscriptionSerializer}
    )
    @action(detail=True, methods=['post'], permission_classes=[permissions.IsAuthenticated, CanManageSubscription])
    def change_plan(self, request, pk=None):
        """Change subscription plan"""
        subscription = self.get_object()
        serializer = PlanChangeSerializer(
            data=request.data,
            context={'subscription': subscription}
        )

        if serializer.is_valid():
            updated_subscription = SubscriptionManager.change_plan(
                subscription=subscription,
                new_plan_id=serializer.validated_data['new_plan_id'],
                effective_date=serializer.validated_data.get('effective_date'),
                prorate=serializer.validated_data.get('prorate', True),
                user=request.user
            )

            response_serializer = OrganizationSubscriptionSerializer(updated_subscription)
            return Response(response_serializer.data)

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @extend_schema(
        summary="Cancel subscription",
        description="Cancel the subscription.",
        request=CancelSubscriptionSerializer,
        responses={200: OrganizationSubscriptionSerializer}
    )
    @action(detail=True, methods=['post'], permission_classes=[permissions.IsAuthenticated, CanManageSubscription])
    def cancel(self, request, pk=None):
        """Cancel subscription"""
        subscription = self.get_object()
        serializer = CancelSubscriptionSerializer(
            data=request.data,
            context={'subscription': subscription}
        )

        if serializer.is_valid():
            cancelled_subscription = SubscriptionManager.cancel_subscription(
                subscription=subscription,
                reason=serializer.validated_data['reason'],
                feedback=serializer.validated_data.get('feedback'),
                cancel_immediately=serializer.validated_data.get('cancel_immediately', False),
                user=request.user
            )

            response_serializer = OrganizationSubscriptionSerializer(cancelled_subscription)
            return Response(response_serializer.data)

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @extend_schema(
        summary="Reactivate subscription",
        description="Reactivate a cancelled or expired subscription.",
        request=ReactivateSubscriptionSerializer,
        responses={200: OrganizationSubscriptionSerializer}
    )
    @action(detail=True, methods=['post'], permission_classes=[permissions.IsAuthenticated, CanManageSubscription])
    def reactivate(self, request, pk=None):
        """Reactivate subscription"""
        subscription = self.get_object()
        serializer = ReactivateSubscriptionSerializer(
            data=request.data,
            context={'subscription': subscription}
        )

        if serializer.is_valid():
            reactivated_subscription = SubscriptionManager.reactivate_subscription(
                subscription=subscription,
                new_plan_id=serializer.validated_data.get('plan_id'),
                user=request.user
            )

            response_serializer = OrganizationSubscriptionSerializer(reactivated_subscription)
            return Response(response_serializer.data)

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @extend_schema(
        summary="Extend trial",
        description="Extend the trial period for a subscription.",
        request=TrialExtensionSerializer,
        responses={200: OrganizationSubscriptionSerializer}
    )
    @action(detail=True, methods=['post'], permission_classes=[permissions.IsAuthenticated, CanManageSubscription])
    def extend_trial(self, request, pk=None):
        """Extend trial period"""
        subscription = self.get_object()
        serializer = TrialExtensionSerializer(
            data=request.data,
            context={'subscription': subscription}
        )

        if serializer.is_valid():
            additional_days = serializer.validated_data['additional_days']
            reason = serializer.validated_data.get('reason', '')

            # Extend trial
            if subscription.trial_end_date:
                subscription.trial_end_date += timedelta(days=additional_days)
            else:
                subscription.trial_end_date = timezone.now() + timedelta(days=additional_days)

            subscription.save()

            # Log event
            SubscriptionEvent.objects.create(
                subscription=subscription,
                event_type='trial_extended',
                description=f"Trial extended by {additional_days} days. Reason: {reason}",
                metadata={
                    'additional_days': additional_days,
                    'reason': reason,
                    'extended_by': request.user.email
                }
            )

            response_serializer = OrganizationSubscriptionSerializer(subscription)
            return Response(response_serializer.data)

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @extend_schema(
        summary="Get usage statistics",
        description="Get detailed usage statistics for the subscription.",
        parameters=[
            OpenApiParameter(
                name='period',
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                description='Period for stats (current_month, last_month, current_year)',
                enum=['current_month', 'last_month', 'current_year']
            )
        ],
        responses={200: UsageStatsSerializer}
    )
    @action(detail=True, methods=['get'])
    def usage_stats(self, request, pk=None):
        """Get usage statistics"""
        subscription = self.get_object()
        period = request.query_params.get('period', 'current_month')

        stats = UsageTracker.get_usage_stats(subscription, period)

        serializer = UsageStatsSerializer(stats)
        return Response(serializer.data)


@extend_schema_view(
    list=extend_schema(
        summary="List subscription invoices",
        description="Get invoices for organization subscriptions.",
        responses={200: SubscriptionInvoiceSerializer(many=True)}
    ),
    retrieve=extend_schema(
        summary="Get invoice details",
        description="Get detailed information about a specific invoice.",
        responses={200: SubscriptionInvoiceSerializer}
    )
)
class SubscriptionInvoiceViewSet(viewsets.ReadOnlyModelViewSet):
    """
    ViewSet for viewing subscription invoices
    """
    serializer_class = SubscriptionInvoiceSerializer
    permission_classes = [permissions.IsAuthenticated, IsOrganizationMember, CanViewSubscription]

    def get_queryset(self):
        """Return invoices for user's organization subscriptions"""
        user_org_ids = self.request.user.organization_memberships.filter(
            is_active=True
        ).values_list('organization_id', flat=True)

        return SubscriptionInvoice.objects.filter(
            subscription__organization_id__in=user_org_ids
        ).select_related('subscription__organization', 'subscription__plan').order_by('-issue_date')

    @extend_schema(
        summary="Download invoice PDF",
        description="Download invoice as PDF file.",
        responses={200: {'description': 'PDF file'}}
    )
    @action(detail=True, methods=['get'])
    def download_pdf(self, request, pk=None):
        """Download invoice as PDF"""
        invoice = self.get_object()

        # TODO: Implement PDF generation
        # For now, return invoice data
        serializer = self.get_serializer(invoice)
        return Response({
            'message': 'PDF download not implemented yet',
            'invoice_data': serializer.data
        })


@extend_schema_view(
    list=extend_schema(
        summary="List usage records",
        description="Get usage records for organization subscriptions.",
        parameters=[
            OpenApiParameter(
                name='usage_type',
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                description='Filter by usage type'
            ),
            OpenApiParameter(
                name='start_date',
                type=OpenApiTypes.DATE,
                location=OpenApiParameter.QUERY,
                description='Start date for filtering'
            ),
            OpenApiParameter(
                name='end_date',
                type=OpenApiTypes.DATE,
                location=OpenApiParameter.QUERY,
                description='End date for filtering'
            )
        ],
        responses={200: UsageRecordSerializer(many=True)}
    ),
    create=extend_schema(
        summary="Create usage record",
        description="Create a new usage record.",
        request=UsageRecordSerializer,
        responses={201: UsageRecordSerializer}
    )
)
class UsageRecordViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing usage records
    """
    serializer_class = UsageRecordSerializer
    permission_classes = [permissions.IsAuthenticated, IsOrganizationMember]
    http_method_names = ['get', 'post']

    def get_queryset(self):
        """Return usage records for user's organization subscriptions"""
        user_org_ids = self.request.user.organization_memberships.filter(
            is_active=True
        ).values_list('organization_id', flat=True)

        queryset = UsageRecord.objects.filter(
            subscription__organization_id__in=user_org_ids
        ).select_related('subscription__organization').order_by('-usage_date')

        # Apply filters
        usage_type = self.request.query_params.get('usage_type')
        if usage_type:
            queryset = queryset.filter(usage_type=usage_type)

        start_date = self.request.query_params.get('start_date')
        if start_date:
            queryset = queryset.filter(usage_date__gte=start_date)

        end_date = self.request.query_params.get('end_date')
        if end_date:
            queryset = queryset.filter(usage_date__lte=end_date)

        return queryset

    @extend_schema(
        summary="Bulk create usage records",
        description="Create multiple usage records at once.",
        request=BulkUsageUpdateSerializer,
        responses={201: {'description': 'Usage records created successfully'}}
    )
    @action(detail=False, methods=['post'])
    def bulk_create(self, request):
        """Bulk create usage records"""
        serializer = BulkUsageUpdateSerializer(data=request.data)

        if serializer.is_valid():
            usage_records = serializer.validated_data['usage_records']

            # Get organization from request (assuming single organization context)
            org_id = request.data.get('organization_id')
            if not org_id:
                return Response(
                    {'error': 'organization_id is required'},
                    status=status.HTTP_400_BAD_REQUEST
                )

            organization = get_object_or_404(Organization, id=org_id)

            if not organization.has_member(request.user):
                return Response(
                    {'error': 'You are not a member of this organization'},
                    status=status.HTTP_403_FORBIDDEN
                )

            # Create usage records
            created_records = UsageTracker.bulk_create_usage_records(
                subscription=organization.subscription,
                usage_records=usage_records
            )

            return Response({
                'message': f'Created {len(created_records)} usage records',
                'records_created': len(created_records)
            }, status=status.HTTP_201_CREATED)

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@extend_schema(
    summary="Validate discount code",
    description="Validate a discount code for a specific plan.",
    request=ValidateDiscountSerializer,
    responses={200: {'description': 'Discount validation result'}}
)
class ValidateDiscountView(APIView):
    """
    View for validating discount codes
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        """Validate discount code"""
        serializer = ValidateDiscountSerializer(data=request.data)

        if serializer.is_valid():
            code = serializer.validated_data['code']
            plan_id = serializer.validated_data['plan_id']

            try:
                discount = SubscriptionDiscount.objects.get(code=code)
                plan = SubscriptionPlan.objects.get(id=plan_id)

                # Validate discount
                is_valid = discount.is_valid
                can_apply = discount.can_apply_to_plan(plan)

                if not is_valid:
                    return Response({
                        'valid': False,
                        'message': 'Discount code is expired or no longer valid'
                    })

                if not can_apply:
                    return Response({
                        'valid': False,
                        'message': 'Discount code cannot be applied to selected plan'
                    })

                # Calculate discount amount
                discount_amount = discount.calculate_discount(plan.price)

                return Response({
                    'valid': True,
                    'discount': SubscriptionDiscountSerializer(discount).data,
                    'discount_amount': discount_amount,
                    'final_price': max(plan.price - discount_amount, 0)
                })

            except (SubscriptionDiscount.DoesNotExist, SubscriptionPlan.DoesNotExist):
                return Response({
                    'valid': False,
                    'message': 'Invalid discount code or plan'
                })

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@extend_schema(
    summary="Get subscription analytics",
    description="Get comprehensive subscription analytics and metrics.",
    parameters=[
        OpenApiParameter(
            name='period',
            type=OpenApiTypes.STR,
            location=OpenApiParameter.QUERY,
            description='Period for analytics (last_30_days, last_90_days, last_year)',
            enum=['last_30_days', 'last_90_days', 'last_year']
        )
    ],
    responses={200: SubscriptionAnalyticsSerializer}
)
class SubscriptionAnalyticsView(APIView):
    """
    View for subscription analytics (admin only)
    """
    permission_classes = [permissions.IsAuthenticated, permissions.IsAdminUser]

    def get(self, request):
        """Get subscription analytics"""
        period = request.query_params.get('period', 'last_30_days')

        # Calculate date range
        now = timezone.now()
        if period == 'last_30_days':
            start_date = now - timedelta(days=30)
        elif period == 'last_90_days':
            start_date = now - timedelta(days=90)
        elif period == 'last_year':
            start_date = now - timedelta(days=365)
        else:
            start_date = now - timedelta(days=30)

        # Get analytics data
        analytics = self.calculate_analytics(start_date, now)

        serializer = SubscriptionAnalyticsSerializer(analytics)
        return Response(serializer.data)

    def calculate_analytics(self, start_date, end_date):
        """Calculate subscription analytics"""
        # Basic subscription counts
        active_subscriptions = OrganizationSubscription.objects.filter(
            status__in=['trial', 'active']
        ).count()

        trial_subscriptions = OrganizationSubscription.objects.filter(
            status='trial'
        ).count()

        cancelled_subscriptions = OrganizationSubscription.objects.filter(
            status='cancelled',
            cancelled_at__gte=start_date
        ).count()

        # Revenue calculations
        paid_invoices = SubscriptionInvoice.objects.filter(
            status='paid',
            paid_date__gte=start_date,
            paid_date__lte=end_date
        )

        total_revenue = paid_invoices.aggregate(
            total=Sum('total_amount')
        )['total'] or Decimal('0')

        # MRR calculation (simplified)
        active_subs_with_pricing = OrganizationSubscription.objects.filter(
            status='active'
        ).select_related('plan')

        mrr = Decimal('0')
        for sub in active_subs_with_pricing:
            monthly_price = sub.effective_price
            if sub.plan.billing_interval == 'yearly':
                monthly_price = monthly_price / 12
            elif sub.plan.billing_interval == 'quarterly':
                monthly_price = monthly_price / 3
            mrr += monthly_price

        arr = mrr * 12

        # Churn rate calculation (simplified)
        total_subs_start_period = OrganizationSubscription.objects.filter(
            created_at__lt=start_date
        ).count()

        churn_rate = Decimal('0')
        if total_subs_start_period > 0:
            churn_rate = (cancelled_subscriptions / total_subs_start_period) * 100

        # Plan distribution
        plan_distribution = list(
            OrganizationSubscription.objects.filter(
                status__in=['trial', 'active']
            ).values('plan__name').annotate(
                count=Count('id')
            ).order_by('-count')
        )

        # Revenue by plan
        revenue_by_plan = list(
            paid_invoices.values(
                'subscription__plan__name'
            ).annotate(
                revenue=Sum('total_amount')
            ).order_by('-revenue')
        )

        return {
            'total_revenue': total_revenue,
            'monthly_recurring_revenue': mrr,
            'annual_recurring_revenue': arr,
            'churn_rate': churn_rate,
            'active_subscriptions': active_subscriptions,
            'trial_subscriptions': trial_subscriptions,
            'cancelled_subscriptions': cancelled_subscriptions,
            'plan_distribution': plan_distribution,
            'revenue_by_plan': revenue_by_plan,
            'growth_metrics': {
                'new_subscriptions': OrganizationSubscription.objects.filter(
                    created_at__gte=start_date
                ).count(),
                'upgrades': SubscriptionEvent.objects.filter(
                    event_type='plan_changed',
                    created_at__gte=start_date
                ).count(),
            },
            'usage_metrics': {
                'total_api_calls': UsageRecord.objects.filter(
                    usage_type='api_call',
                    usage_date__gte=start_date
                ).aggregate(total=Sum('quantity'))['total'] or 0,
                'average_storage_usage': OrganizationSubscription.objects.filter(
                    status__in=['trial', 'active']
                ).aggregate(avg=models.Avg('storage_used_gb'))['avg'] or 0,
            }
        }


@extend_schema(
    summary="Process webhook",
    description="Process webhook events from payment providers.",
    request={'application/json': {'type': 'object'}},
    responses={200: {'description': 'Webhook processed successfully'}}
)
class WebhookView(APIView):
    """
    View for processing webhooks from payment providers
    """
    permission_classes = []  # Webhooks don't use authentication

    def post(self, request):
        """Process webhook event"""
        # TODO: Implement webhook processing logic
        # This would typically handle events from Stripe, PayPal, etc.

        event_type = request.data.get('type')
        subscription_id = request.data.get('subscription_id')

        if not event_type or not subscription_id:
            return Response(
                {'error': 'Missing required webhook data'},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            # Find subscription by external ID
            subscription = OrganizationSubscription.objects.get(
                subscription_id=subscription_id
            )

            # Process different event types
            if event_type == 'payment.succeeded':
                self.handle_payment_success(subscription, request.data)
            elif event_type == 'payment.failed':
                self.handle_payment_failure(subscription, request.data)
            elif event_type == 'subscription.cancelled':
                self.handle_subscription_cancelled(subscription, request.data)

            return Response({'status': 'processed'})

        except OrganizationSubscription.DoesNotExist:
            return Response(
                {'error': 'Subscription not found'},
                status=status.HTTP_404_NOT_FOUND
            )

    def handle_payment_success(self, subscription, webhook_data):
        """Handle successful payment"""
        # Create invoice record
        invoice_data = webhook_data.get('invoice', {})

        SubscriptionInvoice.objects.create(
            subscription=subscription,
            invoice_number=invoice_data.get('number', ''),
            status='paid',
            subtotal=Decimal(str(invoice_data.get('subtotal', 0))),
            total_amount=Decimal(str(invoice_data.get('total', 0))),
            paid_date=timezone.now()
        )

        # Update subscription status
        if subscription.status != 'active':
            subscription.status = 'active'
            subscription.save()

        # Log event
        SubscriptionEvent.objects.create(
            subscription=subscription,
            event_type='payment_succeeded',
            description='Payment processed successfully',
            metadata=webhook_data
        )

    def handle_payment_failure(self, subscription, webhook_data):
        """Handle failed payment"""
        # Update subscription status
        subscription.status = 'past_due'
        subscription.save()

        # Log event
        SubscriptionEvent.objects.create(
            subscription=subscription,
            event_type='payment_failed',
            description='Payment failed',
            metadata=webhook_data
        )

    def handle_subscription_cancelled(self, subscription, webhook_data):
        """Handle subscription cancellation"""
        subscription.status = 'cancelled'
        subscription.cancelled_at = timezone.now()
        subscription.save()

        # Log event
        SubscriptionEvent.objects.create(
            subscription=subscription,
            event_type='cancelled',
            description='Subscription cancelled via webhook',
            metadata=webhook_data
        )
