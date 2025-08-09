from django.contrib import admin
from django.utils.html import format_html
from django.urls import reverse
from django.utils.safestring import mark_safe
from django.db.models import Sum, Count
from decimal import Decimal

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


class PlanFeatureInline(admin.TabularInline):
    model = PlanFeature
    extra = 0
    fields = ['feature', 'boolean_value', 'numeric_value', 'text_value']
    autocomplete_fields = ['feature']


@admin.register(SubscriptionPlan)
class SubscriptionPlanAdmin(admin.ModelAdmin):
    list_display = [
        'name', 'plan_type', 'price_display', 'billing_interval',
        'max_users', 'max_api_calls_per_month', 'is_active_display',
        'is_public', 'featured', 'subscription_count'
    ]
    list_filter = [
        'plan_type', 'billing_interval', 'is_active', 'is_public',
        'featured', 'custom_branding', 'priority_support'
    ]
    search_fields = ['name', 'description']
    readonly_fields = ['monthly_price', 'is_free', 'created_at', 'updated_at']

    fieldsets = (
        ('Basic Information', {
            'fields': ('name', 'description', 'plan_type')
        }),
        ('Pricing', {
            'fields': ('price', 'monthly_price', 'billing_interval', 'currency', 'setup_fee', 'is_free')
        }),
        ('Limits', {
            'fields': (
                'max_users', 'max_organizations', 'max_api_calls_per_month',
                'max_api_keys', 'max_storage_gb'
            )
        }),
        ('Features', {
            'fields': (
                'custom_branding', 'priority_support', 'advanced_analytics',
                'sso_integration', 'api_rate_limit_boost', 'white_label'
            )
        }),
        ('Settings', {
            'fields': ('is_active', 'is_public', 'trial_days', 'sort_order', 'featured')
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        })
    )

    inlines = [PlanFeatureInline]

    def price_display(self, obj):
        return f"{obj.currency} {obj.price} / {obj.get_billing_interval_display()}"

    price_display.short_description = 'Price'

    def is_active_display(self, obj):
        if obj.is_active:
            return format_html('<span style="color: green;">✓ Active</span>')
        return format_html('<span style="color: red;">✗ Inactive</span>')

    is_active_display.short_description = 'Status'

    def subscription_count(self, obj):
        count = obj.subscriptions.filter(status__in=['trial', 'active']).count()
        if count > 0:
            url = reverse('admin:subscriptions_organizationsubscription_changelist') + f'?plan__id={obj.id}'
            return format_html('<a href="{}">{} subscriptions</a>', url, count)
        return '0 subscriptions'

    subscription_count.short_description = 'Active Subscriptions'


@admin.register(SubscriptionFeature)
class SubscriptionFeatureAdmin(admin.ModelAdmin):
    list_display = ['name', 'feature_key', 'feature_type', 'is_active']
    list_filter = ['feature_type', 'is_active']
    search_fields = ['name', 'feature_key', 'description']
    readonly_fields = ['created_at', 'updated_at']


class SubscriptionEventInline(admin.TabularInline):
    model = SubscriptionEvent
    extra = 0
    readonly_fields = ['event_type', 'description', 'created_at']
    fields = ['event_type', 'description', 'created_at']
    ordering = ['-created_at']

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(OrganizationSubscription)
class OrganizationSubscriptionAdmin(admin.ModelAdmin):
    list_display = [
        'organization_display', 'plan', 'status_display', 'effective_price_display',
        'trial_status', 'usage_summary_display', 'next_billing_date', 'created_at'
    ]
    list_filter = [
        'status', 'plan', 'plan__plan_type', 'created_at',
        'current_period_end', 'trial_end_date'
    ]
    search_fields = [
        'organization__name', 'plan__name', 'subscription_id', 'notes'
    ]
    readonly_fields = [
        'subscription_id', 'effective_price', 'is_trial', 'is_active',
        'is_expired', 'days_until_renewal', 'created_at', 'updated_at'
    ]
    autocomplete_fields = ['organization', 'plan']

    fieldsets = (
        ('Subscription Details', {
            'fields': ('organization', 'plan', 'status', 'subscription_id')
        }),
        ('Pricing', {
            'fields': ('custom_price', 'effective_price')
        }),
        ('Dates', {
            'fields': (
                'start_date', 'end_date', 'trial_end_date', 'cancelled_at',
                'current_period_start', 'current_period_end', 'next_billing_date'
            )
        }),
        ('Usage', {
            'fields': ('api_calls_used', 'storage_used_gb')
        }),
        ('Status', {
            'fields': ('is_trial', 'is_active', 'is_expired', 'days_until_renewal')
        }),
        ('Notes', {
            'fields': ('notes',)
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        })
    )

    inlines = [SubscriptionEventInline]

    def organization_display(self, obj):
        return obj.organization.name

    organization_display.short_description = 'Organization'

    def status_display(self, obj):
        status_colors = {
            'trial': 'orange',
            'active': 'green',
            'past_due': 'red',
            'cancelled': 'gray',
            'expired': 'red',
            'suspended': 'red'
        }
        color = status_colors.get(obj.status, 'black')
        return format_html(
            '<span style="color: {};">{}</span>',
            color,
            obj.get_status_display()
        )

    status_display.short_description = 'Status'

    def effective_price_display(self, obj):
        return f"{obj.plan.currency} {obj.effective_price}"

    effective_price_display.short_description = 'Price'

    def trial_status(self, obj):
        if obj.is_trial:
            days_left = (obj.trial_end_date - timezone.now()).days if obj.trial_end_date else 0
            return format_html('<span style="color: orange;">Trial ({} days left)</span>', days_left)
        return 'No trial'

    trial_status.short_description = 'Trial'

    def usage_summary_display(self, obj):
        usage = obj.get_usage_summary()
        api_usage = usage.get('api_calls', {}).get('percentage', 0)
        storage_usage = usage.get('storage', {}).get('percentage', 0)

        return format_html(
            'API: {:.1f}% | Storage: {:.1f}%',
            api_usage,
            storage_usage
        )

    usage_summary_display.short_description = 'Usage'

    actions = ['extend_trial', 'suspend_subscription', 'reactivate_subscription']

    def extend_trial(self, request, queryset):
        """Extend trial by 7 days"""
        for subscription in queryset.filter(status='trial'):
            if subscription.trial_end_date:
                subscription.trial_end_date += timedelta(days=7)
                subscription.save()

        self.message_user(request, f'Extended trial for {queryset.count()} subscriptions.')

    extend_trial.short_description = 'Extend trial by 7 days'

    def suspend_subscription(self, request, queryset):
        """Suspend selected subscriptions"""
        count = queryset.update(status='suspended')
        self.message_user(request, f'Suspended {count} subscriptions.')

    suspend_subscription.short_description = 'Suspend selected subscriptions'

    def reactivate_subscription(self, request, queryset):
        """Reactivate suspended subscriptions"""
        count = queryset.filter(status='suspended').update(status='active')
        self.message_user(request, f'Reactivated {count} subscriptions.')

    reactivate_subscription.short_description = 'Reactivate selected subscriptions'


@admin.register(SubscriptionInvoice)
class SubscriptionInvoiceAdmin(admin.ModelAdmin):
    list_display = [
        'invoice_number', 'organization_display', 'status_display',
        'total_amount_display', 'issue_date', 'due_date', 'is_overdue_display'
    ]
    list_filter = ['status', 'issue_date', 'due_date', 'currency']
    search_fields = [
        'invoice_number', 'subscription__organization__name',
        'subscription__plan__name'
    ]
    readonly_fields = [
        'invoice_number', 'tax_amount', 'total_amount', 'is_overdue',
        'created_at', 'updated_at'
    ]

    fieldsets = (
        ('Invoice Details', {
            'fields': ('subscription', 'invoice_number', 'status')
        }),
        ('Amounts', {
            'fields': ('subtotal', 'tax_rate', 'tax_amount', 'total_amount', 'currency')
        }),
        ('Dates', {
            'fields': ('issue_date', 'due_date', 'paid_date', 'is_overdue')
        }),
        ('Period', {
            'fields': ('period_start', 'period_end')
        }),
        ('Notes', {
            'fields': ('notes',)
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        })
    )

    def organization_display(self, obj):
        return obj.subscription.organization.name

    organization_display.short_description = 'Organization'

    def status_display(self, obj):
        status_colors = {
            'draft': 'gray',
            'sent': 'blue',
            'paid': 'green',
            'overdue': 'red',
            'cancelled': 'gray',
            'refunded': 'orange'
        }
        color = status_colors.get(obj.status, 'black')
        return format_html(
            '<span style="color: {};">{}</span>',
            color,
            obj.get_status_display()
        )

    status_display.short_description = 'Status'

    def total_amount_display(self, obj):
        return f"{obj.currency} {obj.total_amount}"

    total_amount_display.short_description = 'Total'

    def is_overdue_display(self, obj):
        if obj.is_overdue:
            return format_html('<span style="color: red;">✓ Overdue</span>')
        return format_html('<span style="color: green;">✗ Not overdue</span>')

    is_overdue_display.short_description = 'Overdue'


@admin.register(UsageRecord)
class UsageRecordAdmin(admin.ModelAdmin):
    list_display = [
        'subscription_display', 'usage_type_display', 'quantity',
        'usage_date', 'created_at'
    ]
    list_filter = ['usage_type', 'usage_date', 'created_at']
    search_fields = [
        'subscription__organization__name', 'description'
    ]
    readonly_fields = ['created_at', 'updated_at']
    date_hierarchy = 'usage_date'

    fieldsets = (
        ('Usage Details', {
            'fields': ('subscription', 'usage_type', 'quantity', 'usage_date')
        }),
        ('Description', {
            'fields': ('description', 'metadata')
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        })
    )

    def subscription_display(self, obj):
        return f"{obj.subscription.organization.name} ({obj.subscription.plan.name})"

    subscription_display.short_description = 'Subscription'

    def usage_type_display(self, obj):
        return obj.get_usage_type_display()

    usage_type_display.short_description = 'Type'

    def has_add_permission(self, request):
        """Disable manual addition of usage records"""
        return False

    def has_change_permission(self, request, obj=None):
        """Make usage records read-only"""
        return False


@admin.register(SubscriptionEvent)
class SubscriptionEventAdmin(admin.ModelAdmin):
    list_display = [
        'subscription_display', 'event_type_display', 'description_short',
        'created_at'
    ]
    list_filter = ['event_type', 'created_at']
    search_fields = [
        'subscription__organization__name', 'description'
    ]
    readonly_fields = ['created_at', 'updated_at']
    date_hierarchy = 'created_at'

    fieldsets = (
        ('Event Details', {
            'fields': ('subscription', 'event_type', 'description')
        }),
        ('Related Objects', {
            'fields': ('invoice', 'previous_plan', 'new_plan')
        }),
        ('Metadata', {
            'fields': ('metadata',),
            'classes': ('collapse',)
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        })
    )

    def subscription_display(self, obj):
        return obj.subscription.organization.name

    subscription_display.short_description = 'Organization'

    def event_type_display(self, obj):
        return obj.get_event_type_display()

    event_type_display.short_description = 'Event Type'

    def description_short(self, obj):
        return obj.description[:50] + '...' if len(obj.description) > 50 else obj.description

    description_short.short_description = 'Description'

    def has_add_permission(self, request):
        """Disable manual addition of events"""
        return False

    def has_change_permission(self, request, obj=None):
        """Make events read-only"""
        return False


@admin.register(SubscriptionDiscount)
class SubscriptionDiscountAdmin(admin.ModelAdmin):
    list_display = [
        'code', 'name', 'discount_type', 'value_display',
        'redemptions_display', 'is_valid_display', 'valid_until'
    ]
    list_filter = [
        'discount_type', 'is_active', 'first_time_customers_only',
        'valid_from', 'valid_until'
    ]
    search_fields = ['code', 'name', 'description']
    readonly_fields = ['current_redemptions', 'is_valid', 'created_at', 'updated_at']
    filter_horizontal = ['applicable_plans']

    fieldsets = (
        ('Discount Details', {
            'fields': ('code', 'name', 'description', 'discount_type')
        }),
        ('Discount Value', {
            'fields': ('percentage_off', 'amount_off', 'free_trial_days')
        }),
        ('Usage Limits', {
            'fields': ('max_redemptions', 'current_redemptions')
        }),
        ('Validity', {
            'fields': ('valid_from', 'valid_until', 'is_active', 'is_valid')
        }),
        ('Restrictions', {
            'fields': ('applicable_plans', 'first_time_customers_only')
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        })
    )

    def value_display(self, obj):
        if obj.discount_type == 'percentage':
            return f"{obj.percentage_off}% off"
        elif obj.discount_type == 'fixed_amount':
            return f"${obj.amount_off} off"
        elif obj.discount_type == 'free_trial':
            return f"{obj.free_trial_days} days free trial"
        return "N/A"

    value_display.short_description = 'Value'

    def redemptions_display(self, obj):
        if obj.max_redemptions:
            return f"{obj.current_redemptions} / {obj.max_redemptions}"
        return f"{obj.current_redemptions} / ∞"

    redemptions_display.short_description = 'Redemptions'

    def is_valid_display(self, obj):
        if obj.is_valid:
            return format_html('<span style="color: green;">✓ Valid</span>')
        return format_html('<span style="color: red;">✗ Invalid</span>')

    is_valid_display.short_description = 'Status'


# Custom admin views for analytics
class SubscriptionAnalyticsAdmin(admin.ModelAdmin):
    """
    Custom admin view for subscription analytics
    """
    change_list_template = 'admin/subscriptions_analytics.html'

    def changelist_view(self, request, extra_context=None):
        from django.utils import timezone
        from datetime import timedelta

        # Calculate analytics
        now = timezone.now()
        last_30_days = now - timedelta(days=30)

        # Basic metrics
        total_subscriptions = OrganizationSubscription.objects.count()
        active_subscriptions = OrganizationSubscription.objects.filter(
            status__in=['trial', 'active']
        ).count()
        trial_subscriptions = OrganizationSubscription.objects.filter(
            status='trial'
        ).count()

        # Revenue metrics
        total_revenue = SubscriptionInvoice.objects.filter(
            status='paid'
        ).aggregate(
            total=Sum('total_amount')
        )['total'] or Decimal('0')

        revenue_last_30_days = SubscriptionInvoice.objects.filter(
            status='paid',
            paid_date__gte=last_30_days
        ).aggregate(
            total=Sum('total_amount')
        )['total'] or Decimal('0')

        # Plan distribution
        plan_distribution = OrganizationSubscription.objects.filter(
            status__in=['trial', 'active']
        ).values('plan__name').annotate(
            count=Count('id')
        ).order_by('-count')

        # Usage statistics
        api_calls_last_30_days = UsageRecord.objects.filter(
            usage_type='api_call',
            usage_date__gte=last_30_days
        ).aggregate(
            total=Sum('quantity')
        )['total'] or 0

        extra_context = extra_context or {}
        extra_context.update({
            'total_subscriptions': total_subscriptions,
            'active_subscriptions': active_subscriptions,
            'trial_subscriptions': trial_subscriptions,
            'total_revenue': total_revenue,
            'revenue_last_30_days': revenue_last_30_days,
            'plan_distribution': list(plan_distribution),
            'api_calls_last_30_days': api_calls_last_30_days,
        })

        return super().changelist_view(request, extra_context=extra_context)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


# Register the analytics admin (commented out as it needs a model)
# admin.site.register(SubscriptionAnalytics, SubscriptionAnalyticsAdmin)

# Customize admin site
admin.site.site_header = 'Billmunshi Subscriptions Administration'
admin.site.site_title = 'Subscriptions Admin'
admin.site.index_title = 'Subscription Management'