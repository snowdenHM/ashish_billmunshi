from django.urls import path, include
from rest_framework.routers import DefaultRouter

from .views import (
    SubscriptionPlanViewSet,
    OrganizationSubscriptionViewSet,
    SubscriptionInvoiceViewSet,
    UsageRecordViewSet,
    ValidateDiscountView,
    SubscriptionAnalyticsView,
    WebhookView
)

# Create router
router = DefaultRouter()

# Register viewsets
router.register(r'plans', SubscriptionPlanViewSet, basename='subscription-plans')
router.register(r'subscriptions', OrganizationSubscriptionViewSet, basename='subscriptions')
router.register(r'invoices', SubscriptionInvoiceViewSet, basename='invoices')
router.register(r'usage', UsageRecordViewSet, basename='usage-records')

app_name = 'subscriptions'

urlpatterns = [
    # Router URLs
    path('', include(router.urls)),

    # Custom endpoints
    path('validate-discount/', ValidateDiscountView.as_view(), name='validate-discount'),
    path('analytics/', SubscriptionAnalyticsView.as_view(), name='analytics'),
    path('webhooks/', WebhookView.as_view(), name='webhooks'),
]