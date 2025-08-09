from celery import shared_task
from django.utils import timezone
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.conf import settings
from django.db.models import Sum, Count, Q, F
from datetime import timedelta, datetime
from decimal import Decimal
import logging

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3)
def send_subscription_email(self, subscription_id, template_name, context=None, subject=None):
    """
    Send subscription-related email
    """
    try:
        from .models import OrganizationSubscription

        subscription = OrganizationSubscription.objects.select_related(
            'organization', 'plan'
        ).get(id=subscription_id)

        context = context or {}
        context.update({
            'subscription': subscription,
            'organization': subscription.organization,
            'plan': subscription.plan,
            'owner': subscription.organization.owner,
            'site_name': getattr(settings, 'PROJECT_METADATA', {}).get('NAME', 'Billmunshi'),
            'site_url': getattr(settings, 'FRONTEND_ADDRESS', 'http://localhost:3000')
        })

        html_message = render_to_string(f'subscriptions/emails/{template_name}.html', context)
        text_message = render_to_string(f'subscriptions/emails/{template_name}.txt', context)

        if not subject:
            subject = f"Subscription Update - {subscription.organization.name}"

        send_mail(
            subject=subject,
            message=text_message,
            html_message=html_message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[subscription.organization.owner.email],
            fail_silently=False
        )

        logger.info(f"Subscription email sent to {subscription.organization.owner.email}")
        return f"Email sent to {subscription.organization.owner.email}"

    except Exception as exc:
        logger.error(f"Failed to send subscription email: {str(exc)}")
        raise self.retry(exc=exc, countdown=60 * (2 ** self.request.retries))


@shared_task
def process_daily_usage():
    """
    Process and aggregate daily usage records
    """
    try:
        from .models import OrganizationSubscription, UsageRecord

        # Get all active subscriptions
        active_subscriptions = OrganizationSubscription.objects.filter(
            status__in=['trial', 'active']
        )

        today = timezone.now().date()
        processed_count = 0

        for subscription in active_subscriptions:
            # Aggregate API calls for today
            daily_api_calls = UsageRecord.objects.filter(
                subscription=subscription,
                usage_type='api_call',
                usage_date__date=today
            ).aggregate(total=Sum('quantity'))['total'] or 0

            # Update subscription usage if needed
            if daily_api_calls > 0:
                # This could be more sophisticated, like updating daily totals
                logger.info(f"Subscription {subscription.id} used {daily_api_calls} API calls today")
                processed_count += 1

        logger.info(f"Processed daily usage for {processed_count} subscriptions")
        return f"Processed usage for {processed_count} subscriptions"

    except Exception as exc:
        logger.error(f"Failed to process daily usage: {str(exc)}")
        raise exc


@shared_task
def check_subscription_renewals():
    """
    Check for subscriptions that need renewal and send notifications
    """
    try:
        from .models import OrganizationSubscription, SubscriptionEvent

        # Check subscriptions expiring in the next 7 days
        next_week = timezone.now() + timedelta(days=7)
        expiring_subscriptions = OrganizationSubscription.objects.filter(
            status='active',
            current_period_end__lte=next_week,
            current_period_end__gt=timezone.now()
        ).select_related('organization', 'plan')

        for subscription in expiring_subscriptions:
            days_until_renewal = (subscription.current_period_end - timezone.now()).days

            # Send notification based on days until renewal
            if days_until_renewal in [7, 3, 1]:
                # Check if we already sent notification for this period
                existing_event = SubscriptionEvent.objects.filter(
                    subscription=subscription,
                    event_type='renewal_reminder',
                    created_at__date=timezone.now().date()
                ).exists()

                if not existing_event:
                    # Send renewal reminder
                    context = {
                        'days_until_renewal': days_until_renewal,
                        'renewal_date': subscription.current_period_end,
                        'amount': subscription.effective_price
                    }

                    send_subscription_email.delay(
                        subscription.id,
                        'renewal_reminder',
                        context,
                        f'Subscription Renewal Reminder - {days_until_renewal} days'
                    )

                    # Create event
                    SubscriptionEvent.objects.create(
                        subscription=subscription,
                        event_type='renewal_reminder',
                        description=f'Renewal reminder sent ({days_until_renewal} days)',
                        metadata={'days_until_renewal': days_until_renewal}
                    )

        # Check overdue subscriptions
        overdue_subscriptions = OrganizationSubscription.objects.filter(
            status='active',
            current_period_end__lt=timezone.now()
        ).select_related('organization', 'plan')

        for subscription in overdue_subscriptions:
            # Mark as past due
            subscription.status = 'past_due'
            subscription.save()

            # Send overdue notification
            send_subscription_email.delay(
                subscription.id,
                'subscription_overdue',
                {},
                'Subscription Payment Overdue'
            )

            # Create event
            SubscriptionEvent.objects.create(
                subscription=subscription,
                event_type='payment_overdue',
                description='Subscription marked as past due'
            )

        logger.info(f"Checked renewals for {expiring_subscriptions.count()} expiring subscriptions")
        return f"Checked {expiring_subscriptions.count()} renewals, {overdue_subscriptions.count()} overdue"

    except Exception as exc:
        logger.error(f"Failed to check subscription renewals: {str(exc)}")
        raise exc


@shared_task
def send_usage_alerts():
    """
    Send usage limit alerts to organizations
    """
    try:
        from .models import OrganizationSubscription, SubscriptionEvent
        from apps.users.models import UserNotification

        active_subscriptions = OrganizationSubscription.objects.filter(
            status__in=['trial', 'active']
        ).select_related('organization', 'plan')

        alerts_sent = 0

        for subscription in active_subscriptions:
            usage_summary = subscription.get_usage_summary()

            for usage_type, usage_data in usage_summary.items():
                percentage = usage_data['percentage']

                # Send alerts at 80% and 95% usage
                if percentage >= 95:
                    alert_threshold = 95
                elif percentage >= 80:
                    alert_threshold = 80
                else:
                    continue

                # Check if we already sent this alert today
                today = timezone.now().date()
                existing_alert = SubscriptionEvent.objects.filter(
                    subscription=subscription,
                    event_type='usage_warning',
                    created_at__date=today,
                    metadata__usage_type=usage_type,
                    metadata__threshold=alert_threshold
                ).exists()

                if not existing_alert:
                    # Create notification for organization owner
                    UserNotification.create_notification(
                        user=subscription.organization.owner,
                        title=f"Usage Alert: {usage_type.title()}",
                        message=f"Your {usage_type.replace('_', ' ')} usage is at {percentage:.1f}% of your plan limit",
                        notification_type='warning' if alert_threshold < 95 else 'error',
                        organization=subscription.organization,
                        action_url=f"/organizations/{subscription.organization.id}/billing",
                        action_text="View Usage"
                    )

                    # Send email alert
                    context = {
                        'usage_type': usage_type,
                        'percentage': percentage,
                        'threshold': alert_threshold,
                        'current_usage': usage_data['used'],
                        'limit': usage_data['limit']
                    }

                    send_subscription_email.delay(
                        subscription.id,
                        'usage_alert',
                        context,
                        f'Usage Alert: {usage_type.title()} at {percentage:.1f}%'
                    )

                    # Create event
                    SubscriptionEvent.objects.create(
                        subscription=subscription,
                        event_type='usage_warning',
                        description=f'{usage_type.title()} usage at {percentage:.1f}%',
                        metadata={
                            'usage_type': usage_type,
                            'percentage': percentage,
                            'threshold': alert_threshold
                        }
                    )

                    alerts_sent += 1

        logger.info(f"Sent {alerts_sent} usage alerts")
        return f"Sent {alerts_sent} usage alerts"

    except Exception as exc:
        logger.error(f"Failed to send usage alerts: {str(exc)}")
        raise exc


@shared_task
def generate_monthly_reports():
    """
    Generate monthly usage and billing reports
    """
    try:
        from .models import OrganizationSubscription, UsageRecord, SubscriptionInvoice
        from django.db.models import Sum

        # Check if it's the first day of the month
        today = timezone.now().date()
        if today.day != 1:
            return "Not the first day of the month, skipping"

        # Get last month's date range
        last_month = today.replace(day=1) - timedelta(days=1)
        month_start = last_month.replace(day=1)

        active_subscriptions = OrganizationSubscription.objects.filter(
            status__in=['trial', 'active']
        ).select_related('organization', 'plan')

        reports_generated = 0

        for subscription in active_subscriptions:
            # Generate usage report for last month
            usage_data = {}

            for usage_type, _ in UsageRecord.USAGE_TYPES:
                monthly_usage = UsageRecord.objects.filter(
                    subscription=subscription,
                    usage_type=usage_type,
                    usage_date__month=last_month.month,
                    usage_date__year=last_month.year
                ).aggregate(total=Sum('quantity'))['total'] or 0

                usage_data[usage_type] = monthly_usage

            # Calculate total API calls and other metrics
            total_api_calls = usage_data.get('api_call', 0)

            # Send monthly report email
            context = {
                'report_month': last_month.strftime('%B %Y'),
                'usage_data': usage_data,
                'total_api_calls': total_api_calls,
                'plan_limit': subscription.plan.max_api_calls_per_month,
                'usage_percentage': (
                            total_api_calls / subscription.plan.max_api_calls_per_month * 100) if subscription.plan.max_api_calls_per_month > 0 else 0
            }

            send_subscription_email.delay(
                subscription.id,
                'monthly_report',
                context,
                f'Monthly Usage Report - {last_month.strftime("%B %Y")}'
            )

            reports_generated += 1

        logger.info(f"Generated {reports_generated} monthly reports")
        return f"Generated {reports_generated} monthly reports"

    except Exception as exc:
        logger.error(f"Failed to generate monthly reports: {str(exc)}")
        raise exc


@shared_task
def cleanup_old_usage_records(days=365):
    """
    Clean up old usage records (keep last N days)
    """
    try:
        from .models import UsageRecord

        cutoff_date = timezone.now() - timedelta(days=days)

        # Delete old usage records but keep daily aggregates
        deleted_count, _ = UsageRecord.objects.filter(
            created_at__lt=cutoff_date
        ).delete()

        logger.info(f"Cleaned up {deleted_count} old usage records")
        return f"Cleaned up {deleted_count} old usage records"

    except Exception as exc:
        logger.error(f"Failed to cleanup old usage records: {str(exc)}")
        raise exc


@shared_task(bind=True, max_retries=3)
def process_subscription_webhook(self, webhook_data):
    """
    Process subscription webhook from payment providers
    """
    try:
        from .models import OrganizationSubscription, SubscriptionEvent, SubscriptionInvoice

        event_type = webhook_data.get('type')
        subscription_id = webhook_data.get('subscription_id')

        if not event_type or not subscription_id:
            raise ValueError("Missing required webhook data")

        subscription = OrganizationSubscription.objects.get(
            subscription_id=subscription_id
        )

        if event_type == 'payment_succeeded':
            # Handle successful payment
            invoice_data = webhook_data.get('invoice', {})

            # Create or update invoice
            invoice, created = SubscriptionInvoice.objects.get_or_create(
                subscription=subscription,
                invoice_number=invoice_data.get('number', ''),
                defaults={
                    'status': 'paid',
                    'subtotal': Decimal(str(invoice_data.get('subtotal', 0))),
                    'total_amount': Decimal(str(invoice_data.get('total', 0))),
                    'paid_date': timezone.now(),
                    'period_start': subscription.current_period_start,
                    'period_end': subscription.current_period_end
                }
            )

            # Update subscription status
            if subscription.status != 'active':
                subscription.status = 'active'
                subscription.save()

            # Create event
            SubscriptionEvent.objects.create(
                subscription=subscription,
                event_type='payment_succeeded',
                description='Payment processed successfully',
                invoice=invoice,
                metadata=webhook_data
            )

            # Send confirmation email
            send_subscription_email.delay(
                subscription.id,
                'payment_success',
                {'invoice': invoice},
                'Payment Confirmation'
            )

        elif event_type == 'payment_failed':
            # Handle failed payment
            subscription.status = 'past_due'
            subscription.save()

            SubscriptionEvent.objects.create(
                subscription=subscription,
                event_type='payment_failed',
                description='Payment failed',
                metadata=webhook_data
            )

            # Send failure notification
            send_subscription_email.delay(
                subscription.id,
                'payment_failed',
                {},
                'Payment Failed'
            )

        elif event_type == 'subscription_cancelled':
            # Handle subscription cancellation
            subscription.status = 'cancelled'
            subscription.cancelled_at = timezone.now()
            subscription.save()

            SubscriptionEvent.objects.create(
                subscription=subscription,
                event_type='cancelled',
                description='Subscription cancelled via webhook',
                metadata=webhook_data
            )

        logger.info(f"Processed webhook {event_type} for subscription {subscription_id}")
        return f"Processed {event_type} webhook"

    except Exception as exc:
        logger.error(f"Failed to process webhook: {str(exc)}")
        raise self.retry(exc=exc, countdown=60 * (2 ** self.request.retries))


@shared_task
def update_subscription_metrics():
    """
    Update subscription analytics and metrics
    """
    try:
        from .models import OrganizationSubscription, SubscriptionInvoice
        from django.core.cache import cache

        # Calculate MRR (Monthly Recurring Revenue)
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

        # Calculate ARR
        arr = mrr * 12

        # Calculate churn rate (last 30 days)
        thirty_days_ago = timezone.now() - timedelta(days=30)
        cancelled_count = OrganizationSubscription.objects.filter(
            status='cancelled',
            cancelled_at__gte=thirty_days_ago
        ).count()

        active_count = active_subscriptions.count()
        churn_rate = (cancelled_count / (active_count + cancelled_count) * 100) if (
                                                                                               active_count + cancelled_count) > 0 else 0

        # Cache metrics
        metrics = {
            'mrr': float(mrr),
            'arr': float(arr),
            'churn_rate': churn_rate,
            'active_subscriptions': active_count,
            'updated_at': timezone.now().isoformat()
        }

        cache.set('subscription_metrics', metrics, 3600)  # Cache for 1 hour

        logger.info(f"Updated subscription metrics: MRR=${mrr}, ARR=${arr}, Churn={churn_rate}%")
        return f"Updated metrics: MRR=${mrr}, Churn={churn_rate}%"

    except Exception as exc:
        logger.error(f"Failed to update subscription metrics: {str(exc)}")
        raise exc


@shared_task
def process_trial_expiration():
    """
    Process trial subscriptions that are expiring or expired
    """
    try:
        from .models import OrganizationSubscription, SubscriptionEvent

        now = timezone.now()

        # Get trials expiring in next 3 days
        expiring_trials = OrganizationSubscription.objects.filter(
            status='trial',
            trial_end_date__lte=now + timedelta(days=3),
            trial_end_date__gt=now
        ).select_related('organization', 'plan')

        for subscription in expiring_trials:
            days_left = (subscription.trial_end_date - now).days

            if days_left in [3, 1]:
                # Send trial expiration warning
                context = {
                    'days_left': days_left,
                    'trial_end_date': subscription.trial_end_date
                }

                send_subscription_email.delay(
                    subscription.id,
                    'trial_expiring',
                    context,
                    f'Trial Expiring in {days_left} days'
                )

                SubscriptionEvent.objects.create(
                    subscription=subscription,
                    event_type='trial_expiring',
                    description=f'Trial expiring in {days_left} days',
                    metadata={'days_left': days_left}
                )

        # Process expired trials
        expired_trials = OrganizationSubscription.objects.filter(
            status='trial',
            trial_end_date__lte=now
        ).select_related('organization', 'plan')

        for subscription in expired_trials:
            subscription.status = 'expired'
            subscription.save()

            # Send trial expired notification
            send_subscription_email.delay(
                subscription.id,
                'trial_expired',
                {},
                'Trial Period Ended'
            )

            SubscriptionEvent.objects.create(
                subscription=subscription,
                event_type='trial_ended',
                description='Trial period ended'
            )

        logger.info(f"Processed {expiring_trials.count()} expiring trials, {expired_trials.count()} expired")
        return f"Processed {expiring_trials.count()} expiring, {expired_trials.count()} expired trials"

    except Exception as exc:
        logger.error(f"Failed to process trial expiration: {str(exc)}")
        raise exc


@shared_task(bind=True, max_retries=3)
def generate_invoice_pdf(self, invoice_id):
    """
    Generate PDF for invoice (placeholder for actual PDF generation)
    """
    try:
        from .models import SubscriptionInvoice

        invoice = SubscriptionInvoice.objects.select_related(
            'subscription__organization', 'subscription__plan'
        ).get(id=invoice_id)

        # TODO: Implement actual PDF generation using ReportLab or WeasyPrint
        # For now, just create a simple text representation

        invoice_content = f"""
        Invoice #{invoice.invoice_number}

        Bill To:
        {invoice.subscription.organization.name}

        Service Period: {invoice.period_start} - {invoice.period_end}
        Plan: {invoice.subscription.plan.name}

        Subtotal: ${invoice.subtotal}
        Tax: ${invoice.tax_amount}
        Total: ${invoice.total_amount}

        Due Date: {invoice.due_date}
        """

        # In production, save PDF to cloud storage and update invoice with file URL

        logger.info(f"Generated PDF for invoice {invoice.invoice_number}")
        return f"PDF generated for invoice {invoice.invoice_number}"

    except Exception as exc:
        logger.error(f"Failed to generate invoice PDF: {str(exc)}")
        raise self.retry(exc=exc, countdown=60 * (2 ** self.request.retries))


@shared_task
def sync_payment_provider_data():
    """
    Sync data with external payment providers (Stripe, PayPal, etc.)
    """
    try:
        from .models import OrganizationSubscription

        # Placeholder for payment provider sync
        # This would typically fetch data from Stripe, PayPal, etc.

        subscriptions_synced = 0

        # Example: Sync with Stripe
        # for subscription in OrganizationSubscription.objects.filter(status='active'):
        #     # Fetch subscription data from Stripe
        #     # Update local subscription with remote data
        #     subscriptions_synced += 1

        logger.info(f"Synced {subscriptions_synced} subscriptions with payment provider")
        return f"Synced {subscriptions_synced} subscriptions"

    except Exception as exc:
        logger.error(f"Failed to sync payment provider data: {str(exc)}")
        raise exc


@shared_task
def send_billing_notifications():
    """
    Send various billing-related notifications
    """
    try:
        from .models import OrganizationSubscription
        from apps.users.models import UserNotification

        notifications_sent = 0

        # Send notifications for subscriptions with high usage
        high_usage_subscriptions = OrganizationSubscription.objects.filter(
            status__in=['trial', 'active']
        ).select_related('organization', 'plan')

        for subscription in high_usage_subscriptions:
            usage_summary = subscription.get_usage_summary()

            # Check if any usage type is above 90%
            for usage_type, usage_data in usage_summary.items():
                if usage_data['percentage'] > 90:
                    # Create upgrade suggestion notification
                    UserNotification.create_notification(
                        user=subscription.organization.owner,
                        title="Consider Upgrading Your Plan",
                        message=f"Your {usage_type.replace('_', ' ')} usage is at {usage_data['percentage']:.1f}%. Consider upgrading to avoid service interruption.",
                        notification_type='info',
                        organization=subscription.organization,
                        action_url=f"/organizations/{subscription.organization.id}/billing/upgrade",
                        action_text="Upgrade Plan"
                    )
                    notifications_sent += 1
                    break  # Only send one notification per subscription

        logger.info(f"Sent {notifications_sent} billing notifications")
        return f"Sent {notifications_sent} billing notifications"

    except Exception as exc:
        logger.error(f"Failed to send billing notifications: {str(exc)}")
        raise exc