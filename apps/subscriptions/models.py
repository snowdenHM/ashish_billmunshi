from django.db import models
from django.contrib.auth import get_user_model
from django.utils import timezone
from django.core.exceptions import ValidationError
from decimal import Decimal
from datetime import timedelta
import uuid

from apps.utils.models import BaseModel

User = get_user_model()


class SubscriptionPlan(BaseModel):
    """
    Subscription plans with features and pricing
    """
    BILLING_INTERVALS = [
        ('monthly', 'Monthly'),
        ('yearly', 'Yearly'),
        ('quarterly', 'Quarterly'),
        ('one_time', 'One Time'),
    ]

    PLAN_TYPES = [
        ('free', 'Free'),
        ('basic', 'Basic'),
        ('pro', 'Professional'),
        ('enterprise', 'Enterprise'),
        ('custom', 'Custom'),
    ]

    name = models.CharField(max_length=100)
    description = models.TextField()
    plan_type = models.CharField(max_length=20, choices=PLAN_TYPES)

    # Pricing
    price = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    billing_interval = models.CharField(max_length=20, choices=BILLING_INTERVALS)
    currency = models.CharField(max_length=3, default='USD')

    # Features and Limits
    max_users = models.PositiveIntegerField(default=1, help_text="Maximum users allowed")
    max_organizations = models.PositiveIntegerField(default=1, help_text="Maximum organizations allowed")
    max_api_calls_per_month = models.PositiveIntegerField(default=1000, help_text="API calls per month")
    max_api_keys = models.PositiveIntegerField(default=1, help_text="Maximum API keys")
    max_storage_gb = models.PositiveIntegerField(default=1, help_text="Storage limit in GB")

    # Feature Flags
    custom_branding = models.BooleanField(default=False)
    priority_support = models.BooleanField(default=False)
    advanced_analytics = models.BooleanField(default=False)
    sso_integration = models.BooleanField(default=False)
    api_rate_limit_boost = models.BooleanField(default=False)
    white_label = models.BooleanField(default=False)

    # Plan Settings
    is_active = models.BooleanField(default=True)
    is_public = models.BooleanField(default=True, help_text="Visible in public pricing")
    trial_days = models.PositiveIntegerField(default=0, help_text="Free trial period in days")
    setup_fee = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    # Display Settings
    sort_order = models.PositiveIntegerField(default=0)
    featured = models.BooleanField(default=False)

    class Meta:
        db_table = 'subscription_plans'
        ordering = ['sort_order', 'price']

    def __str__(self):
        return f"{self.name} - {self.get_billing_interval_display()}"

    @property
    def monthly_price(self):
        """Convert price to monthly equivalent for comparison"""
        if self.billing_interval == 'monthly':
            return self.price
        elif self.billing_interval == 'yearly':
            return self.price / 12
        elif self.billing_interval == 'quarterly':
            return self.price / 3
        return self.price

    @property
    def is_free(self):
        """Check if this is a free plan"""
        return self.price == 0 or self.plan_type == 'free'

    def get_feature_list(self):
        """Get list of features for this plan"""
        features = []

        # Basic features
        features.append(f"Up to {self.max_users} {'user' if self.max_users == 1 else 'users'}")
        features.append(
            f"Up to {self.max_organizations} {'organization' if self.max_organizations == 1 else 'organizations'}")
        features.append(f"{self.max_api_calls_per_month:,} API calls/month")
        features.append(f"Up to {self.max_api_keys} API {'key' if self.max_api_keys == 1 else 'keys'}")
        features.append(f"{self.max_storage_gb} GB storage")

        # Advanced features
        if self.custom_branding:
            features.append("Custom branding")
        if self.priority_support:
            features.append("Priority support")
        if self.advanced_analytics:
            features.append("Advanced analytics")
        if self.sso_integration:
            features.append("SSO integration")
        if self.api_rate_limit_boost:
            features.append("Higher API rate limits")
        if self.white_label:
            features.append("White-label solution")

        return features


class OrganizationSubscription(BaseModel):
    """
    Organization's subscription to a plan
    """
    STATUS_CHOICES = [
        ('trial', 'Trial'),
        ('active', 'Active'),
        ('past_due', 'Past Due'),
        ('cancelled', 'Cancelled'),
        ('expired', 'Expired'),
        ('suspended', 'Suspended'),
    ]

    organization = models.OneToOneField(
        'teams.Organization',
        on_delete=models.CASCADE,
        related_name='subscription'
    )
    plan = models.ForeignKey(
        SubscriptionPlan,
        on_delete=models.PROTECT,
        related_name='subscriptions'
    )

    # Subscription Details
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='trial')

    # Dates
    start_date = models.DateTimeField(default=timezone.now)
    end_date = models.DateTimeField(null=True, blank=True)
    trial_end_date = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)

    # Billing
    current_period_start = models.DateTimeField(default=timezone.now)
    current_period_end = models.DateTimeField()
    next_billing_date = models.DateTimeField(null=True, blank=True)

    # Usage Tracking
    api_calls_used = models.PositiveIntegerField(default=0)
    storage_used_gb = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    # Pricing (can override plan pricing for custom deals)
    custom_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)

    # Metadata
    subscription_id = models.UUIDField(default=uuid.uuid4, unique=True)
    notes = models.TextField(blank=True)

    class Meta:
        db_table = 'organization_subscriptions'
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.organization.name} - {self.plan.name} ({self.status})"

    @property
    def effective_price(self):
        """Get the effective price (custom or plan price)"""
        return self.custom_price if self.custom_price is not None else self.plan.price

    @property
    def is_trial(self):
        """Check if subscription is in trial period"""
        if not self.trial_end_date:
            return False
        return timezone.now() < self.trial_end_date

    @property
    def is_active(self):
        """Check if subscription is active"""
        return self.status in ['trial', 'active']

    @property
    def is_expired(self):
        """Check if subscription is expired"""
        if not self.end_date:
            return False
        return timezone.now() > self.end_date

    @property
    def days_until_renewal(self):
        """Get days until next billing"""
        if not self.next_billing_date:
            return None
        delta = self.next_billing_date.date() - timezone.now().date()
        return delta.days

    def calculate_usage_percentage(self, usage_type):
        """Calculate usage percentage for different metrics"""
        if usage_type == 'api_calls':
            if self.plan.max_api_calls_per_month == 0:
                return 0
            return min((self.api_calls_used / self.plan.max_api_calls_per_month) * 100, 100)
        elif usage_type == 'storage':
            if self.plan.max_storage_gb == 0:
                return 0
            return min((float(self.storage_used_gb) / self.plan.max_storage_gb) * 100, 100)
        elif usage_type == 'users':
            user_count = self.organization.member_count
            if self.plan.max_users == 0:
                return 0
            return min((user_count / self.plan.max_users) * 100, 100)
        return 0

    def is_usage_limit_exceeded(self, usage_type):
        """Check if usage limit is exceeded"""
        return self.calculate_usage_percentage(usage_type) >= 100

    def get_usage_summary(self):
        """Get comprehensive usage summary"""
        return {
            'api_calls': {
                'used': self.api_calls_used,
                'limit': self.plan.max_api_calls_per_month,
                'percentage': self.calculate_usage_percentage('api_calls'),
                'exceeded': self.is_usage_limit_exceeded('api_calls')
            },
            'storage': {
                'used': float(self.storage_used_gb),
                'limit': self.plan.max_storage_gb,
                'percentage': self.calculate_usage_percentage('storage'),
                'exceeded': self.is_usage_limit_exceeded('storage')
            },
            'users': {
                'used': self.organization.member_count,
                'limit': self.plan.max_users,
                'percentage': self.calculate_usage_percentage('users'),
                'exceeded': self.is_usage_limit_exceeded('users')
            },
            'api_keys': {
                'used': self.organization.api_key_count,
                'limit': self.plan.max_api_keys,
                'percentage': min((self.organization.api_key_count / self.plan.max_api_keys) * 100,
                                  100) if self.plan.max_api_keys > 0 else 0,
                'exceeded': self.organization.api_key_count >= self.plan.max_api_keys
            }
        }

    def save(self, *args, **kwargs):
        """Override save to set billing dates"""
        if not self.current_period_end:
            self.current_period_end = self.calculate_period_end()

        if not self.next_billing_date and self.status == 'active':
            self.next_billing_date = self.current_period_end

        super().save(*args, **kwargs)

    def calculate_period_end(self):
        """Calculate the end of current billing period"""
        start = self.current_period_start

        if self.plan.billing_interval == 'monthly':
            return start + timedelta(days=30)
        elif self.plan.billing_interval == 'yearly':
            return start + timedelta(days=365)
        elif self.plan.billing_interval == 'quarterly':
            return start + timedelta(days=90)
        else:
            return start + timedelta(days=30)


class SubscriptionFeature(BaseModel):
    """
    Custom features that can be added to plans
    """
    name = models.CharField(max_length=100)
    description = models.TextField()
    feature_key = models.CharField(max_length=50, unique=True)

    # Feature Type
    FEATURE_TYPES = [
        ('boolean', 'Boolean (Yes/No)'),
        ('numeric', 'Numeric Limit'),
        ('text', 'Text Value'),
    ]
    feature_type = models.CharField(max_length=20, choices=FEATURE_TYPES)

    # Default Values
    default_boolean_value = models.BooleanField(default=False)
    default_numeric_value = models.PositiveIntegerField(default=0)
    default_text_value = models.TextField(blank=True)

    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = 'subscription_features'
        ordering = ['name']

    def __str__(self):
        return self.name


class PlanFeature(BaseModel):
    """
    Features included in specific plans
    """
    plan = models.ForeignKey(
        SubscriptionPlan,
        on_delete=models.CASCADE,
        related_name='plan_features'
    )
    feature = models.ForeignKey(
        SubscriptionFeature,
        on_delete=models.CASCADE,
        related_name='plan_features'
    )

    # Feature Values
    boolean_value = models.BooleanField(null=True, blank=True)
    numeric_value = models.PositiveIntegerField(null=True, blank=True)
    text_value = models.TextField(blank=True)

    class Meta:
        db_table = 'plan_features'
        unique_together = ['plan', 'feature']

    def __str__(self):
        return f"{self.plan.name} - {self.feature.name}"

    @property
    def value(self):
        """Get the appropriate value based on feature type"""
        if self.feature.feature_type == 'boolean':
            return self.boolean_value if self.boolean_value is not None else self.feature.default_boolean_value
        elif self.feature.feature_type == 'numeric':
            return self.numeric_value if self.numeric_value is not None else self.feature.default_numeric_value
        else:
            return self.text_value if self.text_value else self.feature.default_text_value


class UsageRecord(BaseModel):
    """
    Track usage for billing and analytics
    """
    USAGE_TYPES = [
        ('api_call', 'API Call'),
        ('storage', 'Storage'),
        ('user_addition', 'User Addition'),
        ('api_key_creation', 'API Key Creation'),
        ('organization_creation', 'Organization Creation'),
    ]

    subscription = models.ForeignKey(
        OrganizationSubscription,
        on_delete=models.CASCADE,
        related_name='usage_records'
    )
    usage_type = models.CharField(max_length=50, choices=USAGE_TYPES)
    quantity = models.PositiveIntegerField(default=1)

    # Metadata
    description = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    # Date tracking
    usage_date = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = 'usage_records'
        ordering = ['-usage_date']
        indexes = [
            models.Index(fields=['subscription', 'usage_type', '-usage_date']),
            models.Index(fields=['usage_date']),
        ]

    def __str__(self):
        return f"{self.subscription.organization.name} - {self.usage_type} ({self.quantity})"


class SubscriptionInvoice(BaseModel):
    """
    Invoices for subscription billing
    """
    INVOICE_STATUS = [
        ('draft', 'Draft'),
        ('sent', 'Sent'),
        ('paid', 'Paid'),
        ('overdue', 'Overdue'),
        ('cancelled', 'Cancelled'),
        ('refunded', 'Refunded'),
    ]

    subscription = models.ForeignKey(
        OrganizationSubscription,
        on_delete=models.CASCADE,
        related_name='invoices'
    )

    # Invoice Details
    invoice_number = models.CharField(max_length=50, unique=True)
    status = models.CharField(max_length=20, choices=INVOICE_STATUS, default='draft')

    # Amounts
    subtotal = models.DecimalField(max_digits=10, decimal_places=2)
    tax_rate = models.DecimalField(max_digits=5, decimal_places=4, default=0)
    tax_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    total_amount = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=3, default='USD')

    # Dates
    issue_date = models.DateTimeField(default=timezone.now)
    due_date = models.DateTimeField()
    paid_date = models.DateTimeField(null=True, blank=True)

    # Billing Period
    period_start = models.DateTimeField()
    period_end = models.DateTimeField()

    # Notes
    notes = models.TextField(blank=True)

    class Meta:
        db_table = 'subscription_invoices'
        ordering = ['-issue_date']

    def __str__(self):
        return f"Invoice {self.invoice_number} - {self.subscription.organization.name}"

    @property
    def is_overdue(self):
        """Check if invoice is overdue"""
        return self.status in ['sent'] and timezone.now().date() > self.due_date.date()

    def save(self, *args, **kwargs):
        """Override save to generate invoice number and calculate totals"""
        if not self.invoice_number:
            self.invoice_number = self.generate_invoice_number()

        # Calculate tax and total
        self.tax_amount = self.subtotal * self.tax_rate
        self.total_amount = self.subtotal + self.tax_amount

        super().save(*args, **kwargs)

    def generate_invoice_number(self):
        """Generate unique invoice number"""
        from datetime import datetime
        date_str = datetime.now().strftime('%Y%m')

        # Get last invoice number for this month
        last_invoice = SubscriptionInvoice.objects.filter(
            invoice_number__startswith=f'INV-{date_str}'
        ).order_by('-invoice_number').first()

        if last_invoice:
            last_num = int(last_invoice.invoice_number.split('-')[-1])
            next_num = last_num + 1
        else:
            next_num = 1

        return f'INV-{date_str}-{next_num:04d}'


class SubscriptionEvent(BaseModel):
    """
    Track subscription lifecycle events
    """
    EVENT_TYPES = [
        ('created', 'Subscription Created'),
        ('activated', 'Subscription Activated'),
        ('plan_changed', 'Plan Changed'),
        ('cancelled', 'Subscription Cancelled'),
        ('renewed', 'Subscription Renewed'),
        ('expired', 'Subscription Expired'),
        ('trial_started', 'Trial Started'),
        ('trial_ended', 'Trial Ended'),
        ('payment_succeeded', 'Payment Succeeded'),
        ('payment_failed', 'Payment Failed'),
        ('usage_limit_exceeded', 'Usage Limit Exceeded'),
        ('suspended', 'Subscription Suspended'),
        ('reactivated', 'Subscription Reactivated'),
    ]

    subscription = models.ForeignKey(
        OrganizationSubscription,
        on_delete=models.CASCADE,
        related_name='events'
    )
    event_type = models.CharField(max_length=50, choices=EVENT_TYPES)
    description = models.TextField()

    # Event metadata
    metadata = models.JSONField(default=dict, blank=True)

    # Related objects
    invoice = models.ForeignKey(
        SubscriptionInvoice,
        on_delete=models.SET_NULL,
        null=True,
        blank=True
    )
    previous_plan = models.ForeignKey(
        SubscriptionPlan,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='events_as_previous_plan'
    )
    new_plan = models.ForeignKey(
        SubscriptionPlan,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='events_as_new_plan'
    )

    class Meta:
        db_table = 'subscription_events'
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.subscription.organization.name} - {self.get_event_type_display()}"


class SubscriptionDiscount(BaseModel):
    """
    Discounts and coupons for subscriptions
    """
    DISCOUNT_TYPES = [
        ('percentage', 'Percentage'),
        ('fixed_amount', 'Fixed Amount'),
        ('free_trial', 'Extended Free Trial'),
    ]

    code = models.CharField(max_length=50, unique=True)
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)

    discount_type = models.CharField(max_length=20, choices=DISCOUNT_TYPES)
    percentage_off = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    amount_off = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    free_trial_days = models.PositiveIntegerField(null=True, blank=True)

    # Usage Limits
    max_redemptions = models.PositiveIntegerField(null=True, blank=True)
    current_redemptions = models.PositiveIntegerField(default=0)

    # Validity
    valid_from = models.DateTimeField(default=timezone.now)
    valid_until = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    # Restrictions
    applicable_plans = models.ManyToManyField(
        SubscriptionPlan,
        blank=True,
        help_text="Leave empty to apply to all plans"
    )
    first_time_customers_only = models.BooleanField(default=False)

    class Meta:
        db_table = 'subscription_discounts'
        ordering = ['name']

    def __str__(self):
        return f"{self.code} - {self.name}"

    @property
    def is_valid(self):
        """Check if discount is currently valid"""
        now = timezone.now()

        if not self.is_active:
            return False

        if now < self.valid_from:
            return False

        if self.valid_until and now > self.valid_until:
            return False

        if self.max_redemptions and self.current_redemptions >= self.max_redemptions:
            return False

        return True

    def can_apply_to_plan(self, plan):
        """Check if discount can be applied to specific plan"""
        if not self.applicable_plans.exists():
            return True
        return self.applicable_plans.filter(id=plan.id).exists()

    def calculate_discount(self, amount):
        """Calculate discount amount"""
        if self.discount_type == 'percentage':
            return amount * (self.percentage_off / 100)
        elif self.discount_type == 'fixed_amount':
            return min(self.amount_off, amount)
        return 0