from rest_framework import serializers
from django.utils import timezone
from decimal import Decimal
from datetime import timedelta

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


class SubscriptionFeatureSerializer(serializers.ModelSerializer):
    """Serializer for subscription features"""

    class Meta:
        model = SubscriptionFeature
        fields = [
            'id', 'name', 'description', 'feature_key', 'feature_type',
            'default_boolean_value', 'default_numeric_value', 'default_text_value',
            'is_active'
        ]


class PlanFeatureSerializer(serializers.ModelSerializer):
    """Serializer for plan features"""
    feature = SubscriptionFeatureSerializer(read_only=True)
    value = serializers.ReadOnlyField()

    class Meta:
        model = PlanFeature
        fields = [
            'id', 'feature', 'boolean_value', 'numeric_value',
            'text_value', 'value'
        ]


class SubscriptionPlanSerializer(serializers.ModelSerializer):
    """Serializer for subscription plans"""
    monthly_price = serializers.ReadOnlyField()
    is_free = serializers.ReadOnlyField()
    feature_list = serializers.ReadOnlyField(source='get_feature_list')
    plan_features = PlanFeatureSerializer(many=True, read_only=True)

    class Meta:
        model = SubscriptionPlan
        fields = [
            'id', 'name', 'description', 'plan_type', 'price', 'monthly_price',
            'billing_interval', 'currency', 'is_free', 'max_users',
            'max_organizations', 'max_api_calls_per_month', 'max_api_keys',
            'max_storage_gb', 'custom_branding', 'priority_support',
            'advanced_analytics', 'sso_integration', 'api_rate_limit_boost',
            'white_label', 'is_active', 'is_public', 'trial_days',
            'setup_fee', 'sort_order', 'featured', 'feature_list',
            'plan_features', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class SubscriptionPlanPublicSerializer(serializers.ModelSerializer):
    """Public serializer for subscription plans (limited fields)"""
    monthly_price = serializers.ReadOnlyField()
    is_free = serializers.ReadOnlyField()
    feature_list = serializers.ReadOnlyField(source='get_feature_list')

    class Meta:
        model = SubscriptionPlan
        fields = [
            'id', 'name', 'description', 'plan_type', 'price', 'monthly_price',
            'billing_interval', 'currency', 'is_free', 'max_users',
            'max_organizations', 'max_api_calls_per_month', 'max_api_keys',
            'max_storage_gb', 'custom_branding', 'priority_support',
            'advanced_analytics', 'sso_integration', 'api_rate_limit_boost',
            'white_label', 'trial_days', 'setup_fee', 'featured', 'feature_list'
        ]


class UsageRecordSerializer(serializers.ModelSerializer):
    """Serializer for usage records"""
    usage_type_display = serializers.CharField(source='get_usage_type_display', read_only=True)

    class Meta:
        model = UsageRecord
        fields = [
            'id', 'usage_type', 'usage_type_display', 'quantity',
            'description', 'metadata', 'usage_date', 'created_at'
        ]
        read_only_fields = ['id', 'created_at']


class OrganizationSubscriptionSerializer(serializers.ModelSerializer):
    """Serializer for organization subscriptions"""
    plan = SubscriptionPlanSerializer(read_only=True)
    organization_name = serializers.CharField(source='organization.name', read_only=True)
    effective_price = serializers.ReadOnlyField()
    is_trial = serializers.ReadOnlyField()
    is_active = serializers.ReadOnlyField()
    is_expired = serializers.ReadOnlyField()
    days_until_renewal = serializers.ReadOnlyField()
    usage_summary = serializers.ReadOnlyField(source='get_usage_summary')

    class Meta:
        model = OrganizationSubscription
        fields = [
            'id', 'organization', 'organization_name', 'plan', 'status',
            'start_date', 'end_date', 'trial_end_date', 'cancelled_at',
            'current_period_start', 'current_period_end', 'next_billing_date',
            'api_calls_used', 'storage_used_gb', 'custom_price', 'effective_price',
            'subscription_id', 'notes', 'is_trial', 'is_active', 'is_expired',
            'days_until_renewal', 'usage_summary', 'created_at', 'updated_at'
        ]
        read_only_fields = [
            'id', 'subscription_id', 'created_at', 'updated_at',
            'effective_price', 'is_trial', 'is_active', 'is_expired',
            'days_until_renewal', 'usage_summary'
        ]


class SubscriptionCreateSerializer(serializers.Serializer):
    """Serializer for creating subscriptions"""
    plan_id = serializers.IntegerField()
    discount_code = serializers.CharField(required=False, allow_blank=True)
    trial_days = serializers.IntegerField(required=False, min_value=0, max_value=365)

    def validate_plan_id(self, value):
        """Validate plan exists and is active"""
        try:
            plan = SubscriptionPlan.objects.get(id=value, is_active=True)
            if not plan.is_public:
                raise serializers.ValidationError("Selected plan is not available.")
            return value
        except SubscriptionPlan.DoesNotExist:
            raise serializers.ValidationError("Invalid plan selected.")

    def validate_discount_code(self, value):
        """Validate discount code if provided"""
        if not value:
            return value

        try:
            discount = SubscriptionDiscount.objects.get(code=value)
            if not discount.is_valid:
                raise serializers.ValidationError("Discount code is not valid.")
            return value
        except SubscriptionDiscount.DoesNotExist:
            raise serializers.ValidationError("Invalid discount code.")

    def validate(self, data):
        """Cross-field validation"""
        plan = SubscriptionPlan.objects.get(id=data['plan_id'])
        organization = self.context['organization']

        # Check if organization already has a subscription
        if hasattr(organization, 'subscription'):
            raise serializers.ValidationError("Organization already has an active subscription.")

        # Validate discount code for this plan
        if data.get('discount_code'):
            discount = SubscriptionDiscount.objects.get(code=data['discount_code'])
            if not discount.can_apply_to_plan(plan):
                raise serializers.ValidationError("Discount code cannot be applied to selected plan.")

        return data


class SubscriptionUpdateSerializer(serializers.ModelSerializer):
    """Serializer for updating subscription details"""

    class Meta:
        model = OrganizationSubscription
        fields = ['notes', 'custom_price']

    def validate_custom_price(self, value):
        """Validate custom price"""
        if value is not None and value < 0:
            raise serializers.ValidationError("Custom price cannot be negative.")
        return value


class PlanChangeSerializer(serializers.Serializer):
    """Serializer for changing subscription plans"""
    new_plan_id = serializers.IntegerField()
    effective_date = serializers.DateTimeField(required=False)
    prorate = serializers.BooleanField(default=True)

    def validate_new_plan_id(self, value):
        """Validate new plan"""
        try:
            plan = SubscriptionPlan.objects.get(id=value, is_active=True)
            return value
        except SubscriptionPlan.DoesNotExist:
            raise serializers.ValidationError("Invalid plan selected.")

    def validate_effective_date(self, value):
        """Validate effective date"""
        if value and value < timezone.now():
            raise serializers.ValidationError("Effective date cannot be in the past.")
        return value

    def validate(self, data):
        """Validate plan change"""
        subscription = self.context['subscription']
        new_plan = SubscriptionPlan.objects.get(id=data['new_plan_id'])

        if subscription.plan.id == new_plan.id:
            raise serializers.ValidationError("Cannot change to the same plan.")

        # Check if organization meets requirements for new plan
        organization = subscription.organization
        if organization.member_count > new_plan.max_users:
            raise serializers.ValidationError(
                f"Organization has {organization.member_count} users but new plan only allows {new_plan.max_users} users."
            )

        return data


class SubscriptionInvoiceSerializer(serializers.ModelSerializer):
    """Serializer for subscription invoices"""
    organization_name = serializers.CharField(source='subscription.organization.name', read_only=True)
    plan_name = serializers.CharField(source='subscription.plan.name', read_only=True)
    is_overdue = serializers.ReadOnlyField()

    class Meta:
        model = SubscriptionInvoice
        fields = [
            'id', 'invoice_number', 'status', 'organization_name', 'plan_name',
            'subtotal', 'tax_rate', 'tax_amount', 'total_amount', 'currency',
            'issue_date', 'due_date', 'paid_date', 'period_start', 'period_end',
            'notes', 'is_overdue', 'created_at', 'updated_at'
        ]
        read_only_fields = [
            'id', 'invoice_number', 'tax_amount', 'total_amount',
            'is_overdue', 'created_at', 'updated_at'
        ]


class SubscriptionEventSerializer(serializers.ModelSerializer):
    """Serializer for subscription events"""
    event_type_display = serializers.CharField(source='get_event_type_display', read_only=True)
    organization_name = serializers.CharField(source='subscription.organization.name', read_only=True)

    class Meta:
        model = SubscriptionEvent
        fields = [
            'id', 'event_type', 'event_type_display', 'description',
            'organization_name', 'metadata', 'created_at'
        ]
        read_only_fields = ['id', 'created_at']


class SubscriptionDiscountSerializer(serializers.ModelSerializer):
    """Serializer for subscription discounts"""
    is_valid = serializers.ReadOnlyField()
    applicable_plan_names = serializers.SerializerMethodField()

    class Meta:
        model = SubscriptionDiscount
        fields = [
            'id', 'code', 'name', 'description', 'discount_type',
            'percentage_off', 'amount_off', 'free_trial_days',
            'max_redemptions', 'current_redemptions', 'valid_from',
            'valid_until', 'is_active', 'is_valid', 'first_time_customers_only',
            'applicable_plan_names', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'current_redemptions', 'created_at', 'updated_at']

    def get_applicable_plan_names(self, obj):
        """Get names of applicable plans"""
        if not obj.applicable_plans.exists():
            return ["All Plans"]
        return [plan.name for plan in obj.applicable_plans.all()]


class ValidateDiscountSerializer(serializers.Serializer):
    """Serializer for validating discount codes"""
    code = serializers.CharField()
    plan_id = serializers.IntegerField()

    def validate_code(self, value):
        """Validate discount code exists"""
        try:
            discount = SubscriptionDiscount.objects.get(code=value)
            return value
        except SubscriptionDiscount.DoesNotExist:
            raise serializers.ValidationError("Invalid discount code.")

    def validate_plan_id(self, value):
        """Validate plan exists"""
        try:
            plan = SubscriptionPlan.objects.get(id=value)
            return value
        except SubscriptionPlan.DoesNotExist:
            raise serializers.ValidationError("Invalid plan.")


class UsageStatsSerializer(serializers.Serializer):
    """Serializer for usage statistics"""
    period_start = serializers.DateTimeField()
    period_end = serializers.DateTimeField()
    api_calls_total = serializers.IntegerField()
    api_calls_daily = serializers.ListField(child=serializers.DictField())
    storage_usage = serializers.DecimalField(max_digits=10, decimal_places=2)
    top_endpoints = serializers.ListField(child=serializers.DictField())
    usage_by_type = serializers.DictField()


class BillingHistorySerializer(serializers.Serializer):
    """Serializer for billing history"""
    invoice = SubscriptionInvoiceSerializer()
    payment_method = serializers.CharField()
    payment_status = serializers.CharField()
    payment_date = serializers.DateTimeField()
    amount_paid = serializers.DecimalField(max_digits=10, decimal_places=2)


class SubscriptionSummarySerializer(serializers.Serializer):
    """Serializer for subscription summary dashboard"""
    subscription = OrganizationSubscriptionSerializer()
    current_usage = serializers.DictField()
    upcoming_invoice = SubscriptionInvoiceSerializer(allow_null=True)
    recent_events = SubscriptionEventSerializer(many=True)
    usage_alerts = serializers.ListField(child=serializers.DictField())


class BulkUsageUpdateSerializer(serializers.Serializer):
    """Serializer for bulk usage updates"""
    usage_records = serializers.ListField(
        child=serializers.DictField(),
        min_length=1,
        max_length=1000
    )

    def validate_usage_records(self, value):
        """Validate usage records format"""
        required_fields = ['usage_type', 'quantity']

        for record in value:
            for field in required_fields:
                if field not in record:
                    raise serializers.ValidationError(f"Missing required field: {field}")

            if not isinstance(record['quantity'], int) or record['quantity'] < 0:
                raise serializers.ValidationError("Quantity must be a positive integer.")

            if record['usage_type'] not in dict(UsageRecord.USAGE_TYPES):
                raise serializers.ValidationError(f"Invalid usage type: {record['usage_type']}")

        return value


class SubscriptionAnalyticsSerializer(serializers.Serializer):
    """Serializer for subscription analytics"""
    total_revenue = serializers.DecimalField(max_digits=12, decimal_places=2)
    monthly_recurring_revenue = serializers.DecimalField(max_digits=12, decimal_places=2)
    annual_recurring_revenue = serializers.DecimalField(max_digits=12, decimal_places=2)
    churn_rate = serializers.DecimalField(max_digits=5, decimal_places=2)

    active_subscriptions = serializers.IntegerField()
    trial_subscriptions = serializers.IntegerField()
    cancelled_subscriptions = serializers.IntegerField()

    plan_distribution = serializers.ListField(child=serializers.DictField())
    revenue_by_plan = serializers.ListField(child=serializers.DictField())

    growth_metrics = serializers.DictField()
    usage_metrics = serializers.DictField()


class WebhookEventSerializer(serializers.Serializer):
    """Serializer for webhook events"""
    event_type = serializers.CharField()
    subscription_id = serializers.UUIDField()
    data = serializers.DictField()
    timestamp = serializers.DateTimeField()


class TrialExtensionSerializer(serializers.Serializer):
    """Serializer for extending trial periods"""
    additional_days = serializers.IntegerField(min_value=1, max_value=90)
    reason = serializers.CharField(max_length=500, required=False)

    def validate_additional_days(self, value):
        """Validate trial extension days"""
        subscription = self.context['subscription']

        if not subscription.is_trial:
            raise serializers.ValidationError("Subscription is not in trial period.")

        return value


class CancelSubscriptionSerializer(serializers.Serializer):
    """Serializer for subscription cancellation"""
    CANCELLATION_REASONS = [
        ('too_expensive', 'Too Expensive'),
        ('missing_features', 'Missing Features'),
        ('poor_support', 'Poor Support'),
        ('switching_provider', 'Switching to Another Provider'),
        ('no_longer_needed', 'No Longer Needed'),
        ('other', 'Other'),
    ]

    reason = serializers.ChoiceField(choices=CANCELLATION_REASONS)
    feedback = serializers.CharField(max_length=1000, required=False, allow_blank=True)
    cancel_immediately = serializers.BooleanField(default=False)

    def validate(self, data):
        """Validate cancellation request"""
        subscription = self.context['subscription']

        if subscription.status in ['cancelled', 'expired']:
            raise serializers.ValidationError("Subscription is already cancelled or expired.")

        return data


class ReactivateSubscriptionSerializer(serializers.Serializer):
    """Serializer for subscription reactivation"""
    plan_id = serializers.IntegerField(required=False)

    def validate_plan_id(self, value):
        """Validate plan if provided"""
        if value:
            try:
                plan = SubscriptionPlan.objects.get(id=value, is_active=True)
                return value
            except SubscriptionPlan.DoesNotExist:
                raise serializers.ValidationError("Invalid plan selected.")
        return value

    def validate(self, data):
        """Validate reactivation request"""
        subscription = self.context['subscription']

        if subscription.status not in ['cancelled', 'expired', 'suspended']:
            raise serializers.ValidationError("Subscription cannot be reactivated from current status.")

        return data