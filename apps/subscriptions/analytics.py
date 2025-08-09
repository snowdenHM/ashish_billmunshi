"""
Advanced analytics for subscriptions and billing
"""
from django.db.models import Sum, Count, Avg, Q, F
from django.utils import timezone
from datetime import timedelta, datetime
from decimal import Decimal
from typing import Dict, Any, List, Optional
from django.core.cache import cache

from .models import (
    OrganizationSubscription,
    SubscriptionInvoice,
    UsageRecord,
    SubscriptionEvent,
    SubscriptionPlan
)


class SubscriptionAnalytics:
    """
    Advanced subscription analytics and metrics
    """

    def __init__(self, cache_timeout: int = 3600):
        self.cache_timeout = cache_timeout

    def get_revenue_metrics(self, start_date: datetime = None, end_date: datetime = None) -> Dict[str, Any]:
        """
        Get comprehensive revenue metrics

        Args:
            start_date: Start date for analysis
            end_date: End date for analysis

        Returns:
            dict: Revenue metrics
        """
        cache_key = f"revenue_metrics_{start_date}_{end_date}"
        cached_result = cache.get(cache_key)
        if cached_result:
            return cached_result

        if not end_date:
            end_date = timezone.now()
        if not start_date:
            start_date = end_date - timedelta(days=365)

        # Total revenue
        total_revenue = SubscriptionInvoice.objects.filter(
            status='paid',
            paid_date__range=[start_date, end_date]
        ).aggregate(total=Sum('total_amount'))['total'] or Decimal('0')

        # Monthly Recurring Revenue (MRR)
        mrr = self._calculate_mrr()

        # Annual Recurring Revenue (ARR)
        arr = mrr * 12

        # Average Revenue Per User (ARPU)
        active_subscriptions_count = OrganizationSubscription.objects.filter(
            status__in=['trial', 'active']
        ).count()

        arpu = mrr / active_subscriptions_count if active_subscriptions_count > 0 else Decimal('0')

        # Revenue growth rate
        previous_period_start = start_date - (end_date - start_date)
        previous_revenue = SubscriptionInvoice.objects.filter(
            status='paid',
            paid_date__range=[previous_period_start, start_date]
        ).aggregate(total=Sum('total_amount'))['total'] or Decimal('0')

        growth_rate = (
            ((total_revenue - previous_revenue) / previous_revenue * 100)
            if previous_revenue > 0 else Decimal('0')
        )

        # Revenue by plan
        revenue_by_plan = SubscriptionInvoice.objects.filter(
            status='paid',
            paid_date__range=[start_date, end_date]
        ).values(
            'subscription__plan__name'
        ).annotate(
            revenue=Sum('total_amount'),
            count=Count('id')
        ).order_by('-revenue')

        result = {
            'total_revenue': float(total_revenue),
            'mrr': float(mrr),
            'arr': float(arr),
            'arpu': float(arpu),
            'growth_rate': float(growth_rate),
            'active_subscriptions': active_subscriptions_count,
            'revenue_by_plan': list(revenue_by_plan),
            'period': {
                'start': start_date.isoformat(),
                'end': end_date.isoformat()
            }
        }

        cache.set(cache_key, result, self.cache_timeout)
        return result

    def get_subscription_metrics(self) -> Dict[str, Any]:
        """
        Get subscription-related metrics

        Returns:
            dict: Subscription metrics
        """
        cache_key = "subscription_metrics"
        cached_result = cache.get(cache_key)
        if cached_result:
            return cached_result

        # Current subscription counts
        subscription_counts = OrganizationSubscription.objects.aggregate(
            total=Count('id'),
            active=Count('id', filter=Q(status='active')),
            trial=Count('id', filter=Q(status='trial')),
            cancelled=Count('id', filter=Q(status='cancelled')),
            expired=Count('id', filter=Q(status='expired')),
            past_due=Count('id', filter=Q(status='past_due'))
        )

        # Churn rate (last 30 days)
        thirty_days_ago = timezone.now() - timedelta(days=30)
        churned_subscriptions = OrganizationSubscription.objects.filter(
            status='cancelled',
            cancelled_at__gte=thirty_days_ago
        ).count()

        active_at_start = OrganizationSubscription.objects.filter(
            created_at__lt=thirty_days_ago,
            status__in=['active', 'trial']
        ).count()

        churn_rate = (
            (churned_subscriptions / active_at_start * 100)
            if active_at_start > 0 else 0
        )

        # Trial conversion rate
        trial_conversions = SubscriptionEvent.objects.filter(
            event_type='activated',
            created_at__gte=thirty_days_ago
        ).count()

        trial_starts = SubscriptionEvent.objects.filter(
            event_type='trial_started',
            created_at__gte=thirty_days_ago
        ).count()

        trial_conversion_rate = (
            (trial_conversions / trial_starts * 100)
            if trial_starts > 0 else 0
        )

        # Plan distribution
        plan_distribution = OrganizationSubscription.objects.filter(
            status__in=['trial', 'active']
        ).values(
            'plan__name', 'plan__plan_type'
        ).annotate(
            count=Count('id')
        ).order_by('-count')

        result = {
            'counts': subscription_counts,
            'churn_rate': churn_rate,
            'trial_conversion_rate': trial_conversion_rate,
            'plan_distribution': list(plan_distribution),
            'updated_at': timezone.now().isoformat()
        }

        cache.set(cache_key, result, self.cache_timeout)
        return result

    def get_usage_analytics(self, subscription_id: int = None) -> Dict[str, Any]:
        """
        Get usage analytics

        Args:
            subscription_id: Optional specific subscription ID

        Returns:
            dict: Usage analytics
        """
        cache_key = f"usage_analytics_{subscription_id}"
        cached_result = cache.get(cache_key)
        if cached_result:
            return cached_result

        queryset = UsageRecord.objects.all()
        if subscription_id:
            queryset = queryset.filter(subscription_id=subscription_id)

        # Last 30 days usage
        thirty_days_ago = timezone.now() - timedelta(days=30)
        recent_usage = queryset.filter(created_at__gte=thirty_days_ago)

        # Usage by type
        usage_by_type = recent_usage.values('usage_type').annotate(
            total=Sum('quantity'),
            count=Count('id')
        ).order_by('-total')

        # Daily usage trend (API calls)
        daily_api_usage = recent_usage.filter(
            usage_type='api_call'
        ).extra(
            select={'day': 'DATE(created_at)'}
        ).values('day').annotate(
            total=Sum('quantity')
        ).order_by('day')

        # Top organizations by usage
        top_usage_orgs = recent_usage.values(
            'subscription__organization__name'
        ).annotate(
            total_usage=Sum('quantity')
        ).order_by('-total_usage')[:10]

        # Average usage per subscription
        avg_usage = recent_usage.aggregate(
            avg_api_calls=Avg('quantity', filter=Q(usage_type='api_call')),
            avg_storage=Avg('quantity', filter=Q(usage_type='storage'))
        )

        result = {
            'usage_by_type': list(usage_by_type),
            'daily_api_usage': list(daily_api_usage),
            'top_organizations': list(top_usage_orgs),
            'averages': avg_usage,
            'period_days': 30,
            'updated_at': timezone.now().isoformat()
        }

        cache.set(cache_key, result, self.cache_timeout)
        return result

    def get_cohort_analysis(self, months: int = 12) -> Dict[str, Any]:
        """
        Get cohort analysis for subscription retention

        Args:
            months: Number of months to analyze

        Returns:
            dict: Cohort analysis data
        """
        cache_key = f"cohort_analysis_{months}"
        cached_result = cache.get(cache_key)
        if cached_result:
            return cached_result

        cohorts = []
        end_date = timezone.now()

        for i in range(months):
            # Define cohort period
            cohort_start = end_date - timedelta(days=30 * (i + 1))
            cohort_end = end_date - timedelta(days=30 * i)

            # Get subscriptions that started in this period
            cohort_subscriptions = OrganizationSubscription.objects.filter(
                created_at__range=[cohort_start, cohort_end]
            )

            cohort_size = cohort_subscriptions.count()
            if cohort_size == 0:
                continue

            # Calculate retention for each subsequent month
            retention_data = []
            for month in range(min(6, i + 1)):  # Max 6 months retention
                retention_period_start = cohort_end + timedelta(days=30 * month)
                retention_period_end = cohort_end + timedelta(days=30 * (month + 1))

                active_in_period = cohort_subscriptions.filter(
                    Q(status__in=['active', 'trial']) |
                    Q(cancelled_at__gt=retention_period_start)
                ).filter(
                    created_at__lt=retention_period_start
                ).count()

                retention_rate = (active_in_period / cohort_size * 100) if cohort_size > 0 else 0
                retention_data.append({
                    'month': month,
                    'active_count': active_in_period,
                    'retention_rate': retention_rate
                })

            cohorts.append({
                'cohort_month': cohort_start.strftime('%Y-%m'),
                'cohort_size': cohort_size,
                'retention': retention_data
            })

        result = {
            'cohorts': cohorts,
            'analysis_period_months': months,
            'generated_at': timezone.now().isoformat()
        }

        cache.set(cache_key, result, self.cache_timeout)
        return result

    def get_plan_performance(self) -> Dict[str, Any]:
        """
        Get plan performance metrics

        Returns:
            dict: Plan performance data
        """
        cache_key = "plan_performance"
        cached_result = cache.get(cache_key)
        if cached_result:
            return cached_result

        plans_data = []

        for plan in SubscriptionPlan.objects.filter(is_active=True):
            # Current subscriptions
            current_subs = OrganizationSubscription.objects.filter(
                plan=plan,
                status__in=['trial', 'active']
            )

            # Revenue metrics
            revenue = SubscriptionInvoice.objects.filter(
                subscription__plan=plan,
                status='paid'
            ).aggregate(total=Sum('total_amount'))['total'] or Decimal('0')

            # Churn metrics
            thirty_days_ago = timezone.now() - timedelta(days=30)
            churned = OrganizationSubscription.objects.filter(
                plan=plan,
                status='cancelled',
                cancelled_at__gte=thirty_days_ago
            ).count()

            # Usage metrics
            avg_usage = UsageRecord.objects.filter(
                subscription__plan=plan,
                usage_type='api_call',
                created_at__gte=thirty_days_ago
            ).aggregate(avg=Avg('quantity'))['avg'] or 0

            plans_data.append({
                'plan_name': plan.name,
                'plan_type': plan.plan_type,
                'price': float(plan.price),
                'current_subscriptions': current_subs.count(),
                'total_revenue': float(revenue),
                'monthly_churn': churned,
                'avg_usage': float(avg_usage),
                'conversion_rate': self._calculate_plan_conversion_rate(plan)
            })

        result = {
            'plans': plans_data,
            'updated_at': timezone.now().isoformat()
        }

        cache.set(cache_key, result, self.cache_timeout)
        return result

    def _calculate_mrr(self) -> Decimal:
        """Calculate Monthly Recurring Revenue"""
        active_subscriptions = OrganizationSubscription.objects.filter(
            status__in=['trial', 'active']
        ).select_related('plan')

        mrr = Decimal('0')
        for subscription in active_subscriptions:
            monthly_price = subscription.effective_price

            if subscription.plan.billing_interval == 'yearly':
                monthly_price = monthly_price / 12
            elif subscription.plan.billing_interval == 'quarterly':
                monthly_price = monthly_price / 3

            mrr += monthly_price

        return mrr

    def _calculate_plan_conversion_rate(self, plan) -> float:
        """Calculate conversion rate for a specific plan"""
        thirty_days_ago = timezone.now() - timedelta(days=30)

        # Trials started for this plan
        trials_started = OrganizationSubscription.objects.filter(
            plan=plan,
            status='trial',
            created_at__gte=thirty_days_ago
        ).count()

        # Trials converted to active
        trials_converted = SubscriptionEvent.objects.filter(
            subscription__plan=plan,
            event_type='activated',
            created_at__gte=thirty_days_ago
        ).count()

        return (trials_converted / trials_started * 100) if trials_started > 0 else 0


class BillingAnalytics:
    """
    Billing-specific analytics
    """

    def __init__(self, cache_timeout: int = 3600):
        self.cache_timeout = cache_timeout

    def get_payment_metrics(self) -> Dict[str, Any]:
        """
        Get payment success/failure metrics

        Returns:
            dict: Payment metrics
        """
        cache_key = "payment_metrics"
        cached_result = cache.get(cache_key)
        if cached_result:
            return cached_result

        thirty_days_ago = timezone.now() - timedelta(days=30)

        # Payment events
        payment_events = SubscriptionEvent.objects.filter(
            created_at__gte=thirty_days_ago,
            event_type__in=['payment_succeeded', 'payment_failed']
        )

        total_attempts = payment_events.count()
        successful_payments = payment_events.filter(event_type='payment_succeeded').count()
        failed_payments = payment_events.filter(event_type='payment_failed').count()

        success_rate = (successful_payments / total_attempts * 100) if total_attempts > 0 else 0

        # Failed payment reasons (from metadata)
        failure_reasons = payment_events.filter(
            event_type='payment_failed'
        ).values_list('metadata', flat=True)

        # Process failure reasons
        reason_counts = {}
        for metadata in failure_reasons:
            if isinstance(metadata, dict):
                reason = metadata.get('failure_reason', 'unknown')
                reason_counts[reason] = reason_counts.get(reason, 0) + 1

        # Overdue invoices
        overdue_invoices = SubscriptionInvoice.objects.filter(
            status__in=['sent'],
            due_date__lt=timezone.now()
        )

        overdue_amount = overdue_invoices.aggregate(
            total=Sum('total_amount')
        )['total'] or Decimal('0')

        result = {
            'payment_success_rate': success_rate,
            'total_payment_attempts': total_attempts,
            'successful_payments': successful_payments,
            'failed_payments': failed_payments,
            'failure_reasons': reason_counts,
            'overdue_invoices_count': overdue_invoices.count(),
            'overdue_amount': float(overdue_amount),
            'period_days': 30
        }

        cache.set(cache_key, result, self.cache_timeout)
        return result

    def get_invoice_metrics(self) -> Dict[str, Any]:
        """
        Get invoice-related metrics

        Returns:
            dict: Invoice metrics
        """
        cache_key = "invoice_metrics"
        cached_result = cache.get(cache_key)
        if cached_result:
            return cached_result

        thirty_days_ago = timezone.now() - timedelta(days=30)

        # Invoice status distribution
        invoice_status = SubscriptionInvoice.objects.filter(
            created_at__gte=thirty_days_ago
        ).values('status').annotate(
            count=Count('id'),
            total_amount=Sum('total_amount')
        )

        # Average time to payment
        paid_invoices = SubscriptionInvoice.objects.filter(
            status='paid',
            paid_date__gte=thirty_days_ago,
            paid_date__isnull=False
        )

        payment_times = []
        for invoice in paid_invoices:
            if invoice.paid_date and invoice.issue_date:
                days_to_payment = (invoice.paid_date.date() - invoice.issue_date.date()).days
                payment_times.append(days_to_payment)

        avg_payment_time = sum(payment_times) / len(payment_times) if payment_times else 0

        # Revenue by currency
        revenue_by_currency = SubscriptionInvoice.objects.filter(
            status='paid',
            paid_date__gte=thirty_days_ago
        ).values('currency').annotate(
            total=Sum('total_amount'),
            count=Count('id')
        )

        result = {
            'invoice_status_distribution': list(invoice_status),
            'average_payment_time_days': avg_payment_time,
            'revenue_by_currency': list(revenue_by_currency),
            'period_days': 30
        }

        cache.set(cache_key, result, self.cache_timeout)
        return result


class OrganizationAnalytics:
    """
    Organization-specific analytics
    """

    def __init__(self, organization):
        self.organization = organization
        self.subscription = getattr(organization, 'subscription', None)

    def get_usage_trends(self, days: int = 30) -> Dict[str, Any]:
        """
        Get usage trends for the organization

        Args:
            days: Number of days to analyze

        Returns:
            dict: Usage trend data
        """
        if not self.subscription:
            return {'error': 'No subscription found'}

        start_date = timezone.now() - timedelta(days=days)

        # Daily usage
        daily_usage = UsageRecord.objects.filter(
            subscription=self.subscription,
            created_at__gte=start_date
        ).extra(
            select={'day': 'DATE(created_at)'}
        ).values('day', 'usage_type').annotate(
            total=Sum('quantity')
        ).order_by('day')

        # Format for charting
        usage_data = {}
        for record in daily_usage:
            day = record['day']
            usage_type = record['usage_type']

            if day not in usage_data:
                usage_data[day] = {}
            usage_data[day][usage_type] = record['total']

        # Usage vs limits
        current_usage = self.subscription.get_usage_summary()

        # Projected usage
        avg_daily_api_calls = UsageRecord.objects.filter(
            subscription=self.subscription,
            usage_type='api_call',
            created_at__gte=start_date
        ).aggregate(avg=Avg('quantity'))['avg'] or 0

        days_in_month = 30
        projected_monthly_usage = avg_daily_api_calls * days_in_month

        return {
            'daily_usage': usage_data,
            'current_usage_summary': current_usage,
            'projected_monthly_api_calls': projected_monthly_usage,
            'usage_efficiency': self._calculate_usage_efficiency(),
            'period_days': days
        }

    def get_billing_summary(self) -> Dict[str, Any]:
        """
        Get billing summary for the organization

        Returns:
            dict: Billing summary
        """
        if not self.subscription:
            return {'error': 'No subscription found'}

        # Recent invoices
        recent_invoices = SubscriptionInvoice.objects.filter(
            subscription=self.subscription
        ).order_by('-created_at')[:12]

        # Total spent
        total_spent = SubscriptionInvoice.objects.filter(
            subscription=self.subscription,
            status='paid'
        ).aggregate(total=Sum('total_amount'))['total'] or Decimal('0')

        # Upcoming charges
        next_billing_date = self.subscription.next_billing_date
        next_amount = self.subscription.effective_price

        # Payment history
        payment_history = []
        for invoice in recent_invoices:
            payment_history.append({
                'date': invoice.issue_date,
                'amount': float(invoice.total_amount),
                'status': invoice.status,
                'invoice_number': invoice.invoice_number
            })

        return {
            'current_plan': {
                'name': self.subscription.plan.name,
                'price': float(self.subscription.effective_price),
                'billing_interval': self.subscription.plan.billing_interval
            },
            'total_spent': float(total_spent),
            'next_billing_date': next_billing_date,
            'next_amount': float(next_amount) if next_amount else 0,
            'payment_history': payment_history,
            'subscription_status': self.subscription.status
        }

    def get_cost_optimization_suggestions(self) -> List[Dict[str, Any]]:
        """
        Get cost optimization suggestions

        Returns:
            list: Optimization suggestions
        """
        if not self.subscription:
            return []

        suggestions = []
        usage_summary = self.subscription.get_usage_summary()

        # Check for underutilization
        for usage_type, usage_data in usage_summary.items():
            if usage_data['percentage'] < 50:  # Using less than 50%
                suggestions.append({
                    'type': 'downgrade_suggestion',
                    'title': f'Consider downgrading your plan',
                    'description': f'You\'re only using {usage_data["percentage"]:.1f}% of your {usage_type.replace("_", " ")} allowance',
                    'potential_savings': self._calculate_downgrade_savings(),
                    'priority': 'medium'
                })

        # Check for overutilization
        for usage_type, usage_data in usage_summary.items():
            if usage_data['percentage'] > 90:  # Using more than 90%
                suggestions.append({
                    'type': 'upgrade_suggestion',
                    'title': f'Consider upgrading your plan',
                    'description': f'You\'re using {usage_data["percentage"]:.1f}% of your {usage_type.replace("_", " ")} allowance',
                    'risk': 'Service interruption possible',
                    'priority': 'high'
                })

        # Annual billing suggestion
        if self.subscription.plan.billing_interval == 'monthly':
            annual_savings = self.subscription.effective_price * Decimal('2')  # Assume 2 months free
            suggestions.append({
                'type': 'billing_frequency',
                'title': 'Switch to annual billing',
                'description': 'Save money by switching to annual billing',
                'potential_savings': float(annual_savings),
                'priority': 'low'
            })

        return suggestions

    def _calculate_usage_efficiency(self) -> float:
        """Calculate how efficiently the organization uses their plan"""
        if not self.subscription:
            return 0

        usage_summary = self.subscription.get_usage_summary()

        # Calculate weighted efficiency based on plan limits
        total_weight = 0
        weighted_usage = 0

        for usage_type, usage_data in usage_summary.items():
            if usage_data['limit'] > 0:
                weight = usage_data['limit']  # Higher limits have more weight
                usage_percentage = min(usage_data['percentage'], 100)  # Cap at 100%

                weighted_usage += usage_percentage * weight
                total_weight += weight

        return weighted_usage / total_weight if total_weight > 0 else 0

    def _calculate_downgrade_savings(self) -> float:
        """Calculate potential savings from downgrading"""
        if not self.subscription:
            return 0

        current_price = self.subscription.effective_price

        # Find cheaper plans
        cheaper_plans = SubscriptionPlan.objects.filter(
            price__lt=current_price,
            is_active=True,
            is_public=True
        ).order_by('-price')

        if cheaper_plans.exists():
            next_cheaper = cheaper_plans.first()
            monthly_savings = current_price - next_cheaper.price

            if self.subscription.plan.billing_interval == 'yearly':
                return float(monthly_savings * 12)
            else:
                return float(monthly_savings)

        return 0


# Global analytics instances
subscription_analytics = SubscriptionAnalytics()
billing_analytics = BillingAnalytics()


def get_organization_analytics(organization):
    """Get analytics for a specific organization"""
    return OrganizationAnalytics(organization)