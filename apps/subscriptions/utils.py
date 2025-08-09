from django.utils import timezone
from django.db import transaction
from django.contrib.auth import get_user_model
from decimal import Decimal
from datetime import timedelta, datetime
from typing import Optional, Dict, List, Any

from .models import (
    SubscriptionPlan,
    OrganizationSubscription,
    SubscriptionEvent,
    SubscriptionInvoice,
    UsageRecord,
    SubscriptionDiscount
)

User = get_user_model()


class SubscriptionManager:
    """
    Manager class for subscription operations
    """

    @staticmethod
    def create_subscription(
            organization,
            plan_id: int,
            user: User,
            discount_code: Optional[str] = None,
            trial_days: Optional[int] = None
    ) -> OrganizationSubscription:
        """
        Create a new subscription for an organization
        """
        with transaction.atomic():
            plan = SubscriptionPlan.objects.get(id=plan_id)

            # Calculate trial end date
            trial_end_date = None
            if trial_days or plan.trial_days:
                days = trial_days or plan.trial_days
                trial_end_date = timezone.now() + timedelta(days=days)

            # Create subscription
            subscription = OrganizationSubscription.objects.create(
                organization=organization,
                plan=plan,
                status='trial' if trial_end_date else 'active',
                trial_end_date=trial_end_date,
                current_period_start=timezone.now(),
                current_period_end=timezone.now() + timedelta(days=30)  # Default to 30 days
            )

            # Apply discount if provided
            if discount_code:
                discount = SubscriptionDiscount.objects.get(code=discount_code)
                if discount.is_valid and discount.can_apply_to_plan(plan):
                    # Apply discount logic here
                    if discount.discount_type == 'fixed_amount':
                        subscription.custom_price = max(plan.price - discount.amount_off, 0)
                    elif discount.discount_type == 'percentage':
                        subscription.custom_price = plan.price * (1 - discount.percentage_off / 100)
                    elif discount.discount_type == 'free_trial' and discount.free_trial_days:
                        if trial_end_date:
                            subscription.trial_end_date += timedelta(days=discount.free_trial_days)
                        else:
                            subscription.trial_end_date = timezone.now() + timedelta(days=discount.free_trial_days)
                            subscription.status = 'trial'

                    subscription.save()

                    # Increment discount usage
                    discount.current_redemptions += 1
                    discount.save()

            # Create subscription event
            SubscriptionEvent.objects.create(
                subscription=subscription,
                event_type='created',
                description=f'Subscription created for plan: {plan.name}',
                metadata={
                    'plan_id': plan.id,
                    'plan_name': plan.name,
                    'created_by': user.email,
                    'discount_code': discount_code,
                    'trial_days': trial_days
                }
            )

            # Create user notification
            from apps.users.models import UserNotification
            UserNotification.create_notification(
                user=user,
                title="Subscription Created",
                message=f"Successfully subscribed to {plan.name} plan for {organization.name}",
                notification_type='billing',
                organization=organization
            )

            return subscription

    @staticmethod
    def change_plan(
            subscription: OrganizationSubscription,
            new_plan_id: int,
            effective_date: Optional[datetime] = None,
            prorate: bool = True,
            user: Optional[User] = None
    ) -> OrganizationSubscription:
        """
        Change subscription to a different plan
        """
        with transaction.atomic():
            old_plan = subscription.plan
            new_plan = SubscriptionPlan.objects.get(id=new_plan_id)

            if effective_date is None:
                effective_date = timezone.now()

            # Calculate proration if needed
            proration_credit = Decimal('0')
            if prorate and effective_date <= subscription.current_period_end:
                days_remaining = (subscription.current_period_end - effective_date).days
                total_days = (subscription.current_period_end - subscription.current_period_start).days

                if total_days > 0:
                    proration_credit = (subscription.effective_price * days_remaining) / total_days

            # Update subscription
            subscription.plan = new_plan
            subscription.custom_price = None  # Reset custom pricing
            subscription.save()

            # Create event
            SubscriptionEvent.objects.create(
                subscription=subscription,
                event_type='plan_changed',
                description=f'Plan changed from {old_plan.name} to {new_plan.name}',
                previous_plan=old_plan,
                new_plan=new_plan,
                metadata={
                    'effective_date': effective_date.isoformat(),
                    'prorate': prorate,
                    'proration_credit': str(proration_credit),
                    'changed_by': user.email if user else None
                }
            )

            # Create notification
            if user:
                from apps.users.models import UserNotification
                UserNotification.create_notification(
                    user=user,
                    title="Plan Changed",
                    message=f"Successfully changed plan from {old_plan.name} to {new_plan.name}",
                    notification_type='billing',
                    organization=subscription.organization
                )

            return subscription

    @staticmethod
    def cancel_subscription(
            subscription: OrganizationSubscription,
            reason: str,
            feedback: Optional[str] = None,
            cancel_immediately: bool = False,
            user: Optional[User] = None
    ) -> OrganizationSubscription:
        """
        Cancel a subscription
        """
        with transaction.atomic():
            if cancel_immediately:
                subscription.status = 'cancelled'
                subscription.end_date = timezone.now()
            else:
                subscription.status = 'cancelled'
                subscription.end_date = subscription.current_period_end

            subscription.cancelled_at = timezone.now()
            subscription.save()

            # Create event
            SubscriptionEvent.objects.create(
                subscription=subscription,
                event_type='cancelled',
                description=f'Subscription cancelled. Reason: {reason}',
                metadata={
                    'reason': reason,
                    'feedback': feedback,
                    'cancel_immediately': cancel_immediately,
                    'cancelled_by': user.email if user else None
                }
            )

            # Create notification
            if user:
                from apps.users.models import UserNotification
                UserNotification.create_notification(
                    user=user,
                    title="Subscription Cancelled",
                    message=f"Subscription for {subscription.organization.name} has been cancelled",
                    notification_type='billing',
                    organization=subscription.organization
                )

            return subscription

    @staticmethod
    def reactivate_subscription(
            subscription: OrganizationSubscription,
            new_plan_id: Optional[int] = None,
            user: Optional[User] = None
    ) -> OrganizationSubscription:
        """
        Reactivate a cancelled or expired subscription
        """
        with transaction.atomic():
            old_plan = subscription.plan

            if new_plan_id:
                new_plan = SubscriptionPlan.objects.get(id=new_plan_id)
                subscription.plan = new_plan

            # Reset subscription dates
            subscription.status = 'active'
            subscription.start_date = timezone.now()
            subscription.current_period_start = timezone.now()
            subscription.current_period_end = timezone.now() + timedelta(days=30)
            subscription.next_billing_date = subscription.current_period_end
            subscription.end_date = None
            subscription.cancelled_at = None
            subscription.save()

            # Create event
            SubscriptionEvent.objects.create(
                subscription=subscription,
                event_type='reactivated',
                description='Subscription reactivated',
                previous_plan=old_plan if new_plan_id else None,
                new_plan=subscription.plan if new_plan_id else None,
                metadata={
                    'reactivated_by': user.email if user else None,
                    'new_plan_id': new_plan_id
                }
            )

            # Create notification
            if user:
                from apps.users.models import UserNotification
                UserNotification.create_notification(
                    user=user,
                    title="Subscription Reactivated",
                    message=f"Subscription for {subscription.organization.name} has been reactivated",
                    notification_type='billing',
                    organization=subscription.organization
                )

            return subscription


class UsageTracker:
    """
    Manager class for usage tracking operations
    """

    @staticmethod
    def record_usage(
            subscription: OrganizationSubscription,
            usage_type: str,
            quantity: int = 1,
            description: str = '',
            metadata: Optional[Dict] = None
    ) -> UsageRecord:
        """
        Record usage for a subscription
        """
        usage_record = UsageRecord.objects.create(
            subscription=subscription,
            usage_type=usage_type,
            quantity=quantity,
            description=description,
            metadata=metadata or {}
        )

        # Update subscription usage counters
        if usage_type == 'api_call':
            subscription.api_calls_used += quantity
            subscription.save(update_fields=['api_calls_used'])

        # Check for usage limit alerts
        UsageTracker.check_usage_limits(subscription)

        return usage_record

    @staticmethod
    def bulk_create_usage_records(
            subscription: OrganizationSubscription,
            usage_records: List[Dict]
    ) -> List[UsageRecord]:
        """
        Bulk create usage records
        """
        records_to_create = []
        api_calls_total = 0

        for record_data in usage_records:
            record = UsageRecord(
                subscription=subscription,
                usage_type=record_data['usage_type'],
                quantity=record_data['quantity'],
                description=record_data.get('description', ''),
                metadata=record_data.get('metadata', {}),
                usage_date=record_data.get('usage_date', timezone.now())
            )
            records_to_create.append(record)

            if record_data['usage_type'] == 'api_call':
                api_calls_total += record_data['quantity']

        # Bulk create records
        created_records = UsageRecord.objects.bulk_create(records_to_create)

        # Update subscription counters
        if api_calls_total > 0:
            subscription.api_calls_used += api_calls_total
            subscription.save(update_fields=['api_calls_used'])

        # Check usage limits
        UsageTracker.check_usage_limits(subscription)

        return created_records

    @staticmethod
    def check_usage_limits(subscription: OrganizationSubscription):
        """
        Check if usage limits are exceeded and create alerts
        """
        usage_summary = subscription.get_usage_summary()

        for usage_type, usage_data in usage_summary.items():
            percentage = usage_data['percentage']

            # Create alerts at 80% and 100% usage
            if percentage >= 100 and not usage_data.get('alert_sent_100'):
                UsageTracker.create_usage_alert(subscription, usage_type, 100)
            elif percentage >= 80 and not usage_data.get('alert_sent_80'):
                UsageTracker.create_usage_alert(subscription, usage_type, 80)

    @staticmethod
    def create_usage_alert(
            subscription: OrganizationSubscription,
            usage_type: str,
            percentage: int
    ):
        """
        Create usage alert notification
        """
        # Create event
        SubscriptionEvent.objects.create(
            subscription=subscription,
            event_type='usage_limit_exceeded' if percentage >= 100 else 'usage_warning',
            description=f'{usage_type.title()} usage at {percentage}%',
            metadata={
                'usage_type': usage_type,
                'percentage': percentage
            }
        )

        # Notify organization members
        from apps.users.models import UserNotification
        from apps.teams.models import Role

        # Notify owners and admins
        members_to_notify = subscription.organization.members.filter(
            is_active=True,
            role__name__in=[Role.OWNER, Role.ADMIN]
        )

        for member in members_to_notify:
            UserNotification.create_notification(
                user=member.user,
                title=f"Usage Alert: {usage_type.title()}",
                message=f"Your {usage_type.replace('_', ' ')} usage is at {percentage}% of your plan limit",
                notification_type='warning' if percentage < 100 else 'error',
                organization=subscription.organization
            )

    @staticmethod
    def get_usage_stats(subscription: OrganizationSubscription, period: str) -> Dict[str, Any]:
        """
        Get usage statistics for a subscription
        """
        now = timezone.now()

        # Calculate date range
        if period == 'current_month':
            start_date = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            end_date = now
        elif period == 'last_month':
            last_month = now.replace(day=1) - timedelta(days=1)
            start_date = last_month.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            end_date = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        elif period == 'current_year':
            start_date = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
            end_date = now
        else:
            start_date = now - timedelta(days=30)
            end_date = now

        # Get usage records
        usage_records = subscription.usage_records.filter(
            usage_date__gte=start_date,
            usage_date__lte=end_date
        )

        # Calculate API call statistics
        api_calls = usage_records.filter(usage_type='api_call')
        api_calls_total = sum(record.quantity for record in api_calls)

        # Daily API calls
        api_calls_daily = []
        current_date = start_date.date()
        while current_date <= end_date.date():
            daily_calls = sum(
                record.quantity for record in api_calls
                if record.usage_date.date() == current_date
            )
            api_calls_daily.append({
                'date': current_date.isoformat(),
                'calls': daily_calls
            })
            current_date += timedelta(days=1)

        # Usage by type
        usage_by_type = {}
        for record in usage_records:
            usage_type = record.usage_type
            if usage_type not in usage_by_type:
                usage_by_type[usage_type] = 0
            usage_by_type[usage_type] += record.quantity

        # Top endpoints (from metadata if available)
        top_endpoints = []
        endpoint_counts = {}
        for record in api_calls:
            endpoint = record.metadata.get('endpoint')
            if endpoint:
                endpoint_counts[endpoint] = endpoint_counts.get(endpoint, 0) + record.quantity

        for endpoint, count in sorted(endpoint_counts.items(), key=lambda x: x[1], reverse=True)[:10]:
            top_endpoints.append({
                'endpoint': endpoint,
                'calls': count
            })

        return {
            'period_start': start_date,
            'period_end': end_date,
            'api_calls_total': api_calls_total,
            'api_calls_daily': api_calls_daily,
            'storage_usage': subscription.storage_used_gb,
            'top_endpoints': top_endpoints,
            'usage_by_type': usage_by_type
        }

    @staticmethod
    def reset_monthly_usage(subscription: OrganizationSubscription):
        """
        Reset monthly usage counters (called at billing cycle)
        """
        subscription.api_calls_used = 0
        subscription.save(update_fields=['api_calls_used'])

        # Log reset event
        SubscriptionEvent.objects.create(
            subscription=subscription,
            event_type='usage_reset',
            description='Monthly usage counters reset',
            metadata={
                'reset_date': timezone.now().isoformat()
            }
        )


class BillingCalculator:
    """
    Manager class for billing calculations
    """

    @staticmethod
    def calculate_subscription_cost(
            plan: SubscriptionPlan,
            custom_price: Optional[Decimal] = None,
            discount: Optional[SubscriptionDiscount] = None,
            proration_days: Optional[int] = None
    ) -> Dict[str, Decimal]:
        """
        Calculate subscription cost with discounts and proration
        """
        base_price = custom_price if custom_price is not None else plan.price

        # Apply discount
        discount_amount = Decimal('0')
        if discount and discount.is_valid:
            discount_amount = discount.calculate_discount(base_price)

        # Calculate subtotal
        subtotal = base_price - discount_amount

        # Apply proration
        if proration_days:
            if plan.billing_interval == 'monthly':
                total_days = 30
            elif plan.billing_interval == 'yearly':
                total_days = 365
            elif plan.billing_interval == 'quarterly':
                total_days = 90
            else:
                total_days = 30

            subtotal = (subtotal * proration_days) / total_days

        # Add setup fee
        setup_fee = plan.setup_fee

        # Calculate tax (simplified - 0% for now)
        tax_rate = Decimal('0.00')
        tax_amount = subtotal * tax_rate

        # Calculate total
        total = subtotal + setup_fee + tax_amount

        return {
            'base_price': base_price,
            'discount_amount': discount_amount,
            'setup_fee': setup_fee,
            'subtotal': subtotal,
            'tax_rate': tax_rate,
            'tax_amount': tax_amount,
            'total': total
        }

    @staticmethod
    def generate_invoice(
            subscription: OrganizationSubscription,
            period_start: datetime,
            period_end: datetime,
            custom_amount: Optional[Decimal] = None
    ) -> SubscriptionInvoice:
        """
        Generate an invoice for a subscription
        """
        # Calculate amount
        if custom_amount:
            amount = custom_amount
        else:
            amount = subscription.effective_price

        # Create invoice
        invoice = SubscriptionInvoice.objects.create(
            subscription=subscription,
            subtotal=amount,
            tax_rate=Decimal('0.00'),  # No tax for now
            due_date=timezone.now() + timedelta(days=30),
            period_start=period_start,
            period_end=period_end
        )

        # Create event
        SubscriptionEvent.objects.create(
            subscription=subscription,
            event_type='invoice_generated',
            description=f'Invoice {invoice.invoice_number} generated',
            invoice=invoice,
            metadata={
                'invoice_number': invoice.invoice_number,
                'amount': str(invoice.total_amount)
            }
        )

        return invoice

    @staticmethod
    def calculate_upgrade_proration(
            subscription: OrganizationSubscription,
            new_plan: SubscriptionPlan,
            upgrade_date: Optional[datetime] = None
    ) -> Dict[str, Decimal]:
        """
        Calculate proration for plan upgrades
        """
        if upgrade_date is None:
            upgrade_date = timezone.now()

        # Calculate remaining days in current period
        days_remaining = (subscription.current_period_end - upgrade_date).days
        total_days = (subscription.current_period_end - subscription.current_period_start).days

        if total_days <= 0:
            return {
                'credit_amount': Decimal('0'),
                'new_charge': new_plan.price,
                'proration_days': 0
            }

        # Calculate credit for unused portion of current plan
        credit_amount = (subscription.effective_price * days_remaining) / total_days

        # Calculate prorated charge for new plan
        new_charge = (new_plan.price * days_remaining) / total_days

        return {
            'credit_amount': credit_amount,
            'new_charge': new_charge,
            'net_amount': new_charge - credit_amount,
            'proration_days': days_remaining
        }


class SubscriptionAnalytics:
    """
    Manager class for subscription analytics
    """

    @staticmethod
    def calculate_mrr() -> Decimal:
        """
        Calculate Monthly Recurring Revenue
        """
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

    @staticmethod
    def calculate_churn_rate(period_days: int = 30) -> Decimal:
        """
        Calculate churn rate for a given period
        """
        end_date = timezone.now()
        start_date = end_date - timedelta(days=period_days)

        # Subscriptions active at start of period
        active_at_start = OrganizationSubscription.objects.filter(
            status__in=['trial', 'active'],
            created_at__lt=start_date
        ).count()

        # Subscriptions cancelled during period
        cancelled_during_period = OrganizationSubscription.objects.filter(
            status='cancelled',
            cancelled_at__gte=start_date,
            cancelled_at__lt=end_date
        ).count()

        if active_at_start == 0:
            return Decimal('0')

        churn_rate = (cancelled_during_period / active_at_start) * 100
        return Decimal(str(churn_rate))

    @staticmethod
    def get_plan_distribution() -> List[Dict[str, Any]]:
        """
        Get distribution of subscriptions by plan
        """
        from django.db.models import Count

        distribution = OrganizationSubscription.objects.filter(
            status__in=['trial', 'active']
        ).values(
            'plan__name', 'plan__plan_type'
        ).annotate(
            count=Count('id')
        ).order_by('-count')

        return list(distribution)

    @staticmethod
    def get_revenue_trends(months: int = 12) -> List[Dict[str, Any]]:
        """
        Get revenue trends over time
        """
        trends = []

        for i in range(months):
            month_start = timezone.now().replace(day=1) - timedelta(days=30 * i)
            month_end = month_start + timedelta(days=30)

            revenue = SubscriptionInvoice.objects.filter(
                status='paid',
                paid_date__gte=month_start,
                paid_date__lt=month_end
            ).aggregate(
                total=models.Sum('total_amount')
            )['total'] or Decimal('0')

            trends.append({
                'month': month_start.strftime('%Y-%m'),
                'revenue': revenue
            })

        return list(reversed(trends))