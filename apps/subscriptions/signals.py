from django.db.models.signals import post_save, pre_save, post_delete
from django.dispatch import receiver
from django.utils import timezone
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.conf import settings
from datetime import timedelta

from .models import (
    OrganizationSubscription,
    SubscriptionEvent,
    SubscriptionInvoice,
    UsageRecord,
    SubscriptionPlan
)


@receiver(post_save, sender=OrganizationSubscription)
def handle_subscription_creation(sender, instance, created, **kwargs):
    """
    Handle subscription creation and status changes
    """
    if created:
        # Send welcome email to organization owner
        send_subscription_welcome_email(instance)

        # Create initial subscription event
        SubscriptionEvent.objects.create(
            subscription=instance,
            event_type='created',
            description=f'Subscription created for plan: {instance.plan.name}',
            metadata={
                'plan_id': instance.plan.id,
                'plan_name': instance.plan.name,
                'trial_days': (instance.trial_end_date - timezone.now()).days if instance.trial_end_date else 0
            }
        )

        # Create user notification
        create_subscription_notification(
            instance,
            'Subscription Created',
            f'Successfully subscribed to {instance.plan.name} plan'
        )


@receiver(pre_save, sender=OrganizationSubscription)
def handle_subscription_status_change(sender, instance, **kwargs):
    """
    Handle subscription status changes
    """
    if instance.pk:  # Only for existing instances
        try:
            old_instance = OrganizationSubscription.objects.get(pk=instance.pk)

            # Check for status changes
            if old_instance.status != instance.status:
                handle_status_transition(old_instance, instance)

            # Check for plan changes
            if old_instance.plan != instance.plan:
                handle_plan_change(old_instance, instance)

        except OrganizationSubscription.DoesNotExist:
            pass


def handle_status_transition(old_subscription, new_subscription):
    """
    Handle subscription status transitions
    """
    old_status = old_subscription.status
    new_status = new_subscription.status

    # Define status transition events
    status_events = {
        ('trial', 'active'): 'activated',
        ('active', 'cancelled'): 'cancelled',
        ('trial', 'cancelled'): 'cancelled',
        ('active', 'suspended'): 'suspended',
        ('suspended', 'active'): 'reactivated',
        ('cancelled', 'active'): 'reactivated',
        ('active', 'expired'): 'expired',
        ('trial', 'expired'): 'expired'
    }

    event_type = status_events.get((old_status, new_status))

    if event_type:
        # Create event after save
        def create_event():
            SubscriptionEvent.objects.create(
                subscription=new_subscription,
                event_type=event_type,
                description=f'Subscription status changed from {old_status} to {new_status}',
                metadata={
                    'old_status': old_status,
                    'new_status': new_status
                }
            )

        # Schedule event creation after save
        from django.db import transaction
        transaction.on_commit(create_event)

        # Send notifications
        if new_status == 'cancelled':
            send_subscription_cancelled_email(new_subscription)
        elif new_status == 'suspended':
            send_subscription_suspended_email(new_subscription)
        elif event_type == 'reactivated':
            send_subscription_reactivated_email(new_subscription)


def handle_plan_change(old_subscription, new_subscription):
    """
    Handle subscription plan changes
    """

    def create_plan_change_event():
        SubscriptionEvent.objects.create(
            subscription=new_subscription,
            event_type='plan_changed',
            description=f'Plan changed from {old_subscription.plan.name} to {new_subscription.plan.name}',
            previous_plan=old_subscription.plan,
            new_plan=new_subscription.plan,
            metadata={
                'old_plan_id': old_subscription.plan.id,
                'new_plan_id': new_subscription.plan.id,
                'old_plan_name': old_subscription.plan.name,
                'new_plan_name': new_subscription.plan.name
            }
        )

    from django.db import transaction
    transaction.on_commit(create_plan_change_event)

    # Send plan change notification
    send_plan_change_email(new_subscription, old_subscription.plan, new_subscription.plan)


@receiver(post_save, sender=SubscriptionInvoice)
def handle_invoice_creation(sender, instance, created, **kwargs):
    """
    Handle invoice creation and status changes
    """
    if created:
        # Send invoice email
        send_invoice_email(instance)

        # Create subscription event
        SubscriptionEvent.objects.create(
            subscription=instance.subscription,
            event_type='invoice_generated',
            description=f'Invoice {instance.invoice_number} generated',
            invoice=instance,
            metadata={
                'invoice_number': instance.invoice_number,
                'amount': str(instance.total_amount),
                'due_date': instance.due_date.isoformat()
            }
        )


@receiver(post_save, sender=UsageRecord)
def handle_usage_record_creation(sender, instance, created, **kwargs):
    """
    Handle usage record creation and check limits
    """
    if created:
        subscription = instance.subscription

        # Update subscription usage counters based on usage type
        if instance.usage_type == 'api_call':
            subscription.api_calls_used += instance.quantity
            subscription.save(update_fields=['api_calls_used'])

            # Check if approaching or exceeding API limits
            usage_percentage = (subscription.api_calls_used / subscription.plan.max_api_calls_per_month) * 100

            if usage_percentage >= 100:
                # Usage limit exceeded
                create_usage_limit_event(subscription, 'api_calls', usage_percentage)
                send_usage_limit_exceeded_email(subscription, 'API calls')
            elif usage_percentage >= 80:
                # Approaching limit warning
                create_usage_warning_event(subscription, 'api_calls', usage_percentage)
                send_usage_warning_email(subscription, 'API calls', usage_percentage)


def create_usage_limit_event(subscription, usage_type, percentage):
    """
    Create usage limit exceeded event
    """
    SubscriptionEvent.objects.create(
        subscription=subscription,
        event_type='usage_limit_exceeded',
        description=f'{usage_type.title()} usage limit exceeded ({percentage:.1f}%)',
        metadata={
            'usage_type': usage_type,
            'percentage': percentage,
            'limit_exceeded': True
        }
    )


def create_usage_warning_event(subscription, usage_type, percentage):
    """
    Create usage warning event
    """
    SubscriptionEvent.objects.create(
        subscription=subscription,
        event_type='usage_warning',
        description=f'{usage_type.title()} usage at {percentage:.1f}% of limit',
        metadata={
            'usage_type': usage_type,
            'percentage': percentage,
            'warning_threshold': 80
        }
    )


def create_subscription_notification(subscription, title, message):
    """
    Create notification for subscription-related events
    """
    from apps.users.models import UserNotification
    from apps.teams.models import Role

    # Notify organization owners and admins
    members_to_notify = subscription.organization.members.filter(
        is_active=True,
        role__name__in=[Role.OWNER, Role.ADMIN]
    )

    for member in members_to_notify:
        UserNotification.create_notification(
            user=member.user,
            title=title,
            message=message,
            notification_type='billing',
            organization=subscription.organization
        )


# Email notification functions
def send_subscription_welcome_email(subscription):
    """
    Send welcome email when subscription is created
    """
    try:
        context = {
            'subscription': subscription,
            'organization': subscription.organization,
            'plan': subscription.plan,
            'owner': subscription.organization.owner,
            'dashboard_url': f"{settings.FRONTEND_ADDRESS}/organizations/{subscription.organization.id}/billing"
        }

        subject = f"Welcome to {subscription.plan.name} - {subscription.organization.name}"
        html_message = render_to_string('subscriptions/emails/subscription_welcome.html', context)
        text_message = render_to_string('subscriptions/emails/subscription_welcome.txt', context)

        send_mail(
            subject=subject,
            message=text_message,
            html_message=html_message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[subscription.organization.owner.email],
            fail_silently=True
        )

    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"Failed to send subscription welcome email: {str(e)}")


def send_subscription_cancelled_email(subscription):
    """
    Send email when subscription is cancelled
    """
    try:
        context = {
            'subscription': subscription,
            'organization': subscription.organization,
            'plan': subscription.plan,
            'owner': subscription.organization.owner,
            'end_date': subscription.end_date
        }

        subject = f"Subscription Cancelled - {subscription.organization.name}"
        html_message = render_to_string('subscriptions/emails/subscription_cancelled.html', context)
        text_message = render_to_string('subscriptions/emails/subscription_cancelled.txt', context)

        send_mail(
            subject=subject,
            message=text_message,
            html_message=html_message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[subscription.organization.owner.email],
            fail_silently=True
        )

    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"Failed to send subscription cancelled email: {str(e)}")


def send_plan_change_email(subscription, old_plan, new_plan):
    """
    Send email when subscription plan is changed
    """
    try:
        context = {
            'subscription': subscription,
            'organization': subscription.organization,
            'old_plan': old_plan,
            'new_plan': new_plan,
            'owner': subscription.organization.owner
        }

        subject = f"Plan Changed - {subscription.organization.name}"
        html_message = render_to_string('subscriptions/emails/plan_changed.html', context)
        text_message = render_to_string('subscriptions/emails/plan_changed.txt', context)

        send_mail(
            subject=subject,
            message=text_message,
            html_message=html_message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[subscription.organization.owner.email],
            fail_silently=True
        )

    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"Failed to send plan change email: {str(e)}")


def send_invoice_email(invoice):
    """
    Send email when invoice is generated
    """
    try:
        context = {
            'invoice': invoice,
            'subscription': invoice.subscription,
            'organization': invoice.subscription.organization,
            'owner': invoice.subscription.organization.owner,
            'invoice_url': f"{settings.FRONTEND_ADDRESS}/billing/invoices/{invoice.id}"
        }

        subject = f"Invoice {invoice.invoice_number} - {invoice.subscription.organization.name}"
        html_message = render_to_string('subscriptions/emails/invoice.html', context)
        text_message = render_to_string('subscriptions/emails/invoice.txt', context)

        send_mail(
            subject=subject,
            message=text_message,
            html_message=html_message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[invoice.subscription.organization.owner.email],
            fail_silently=True
        )

    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"Failed to send invoice email: {str(e)}")


def send_usage_limit_exceeded_email(subscription, usage_type):
    """
    Send email when usage limit is exceeded
    """
    try:
        context = {
            'subscription': subscription,
            'organization': subscription.organization,
            'usage_type': usage_type,
            'plan': subscription.plan,
            'owner': subscription.organization.owner,
            'billing_url': f"{settings.FRONTEND_ADDRESS}/organizations/{subscription.organization.id}/billing"
        }

        subject = f"Usage Limit Exceeded - {subscription.organization.name}"
        html_message = render_to_string('subscriptions/emails/usage_limit_exceeded.html', context)
        text_message = render_to_string('subscriptions/emails/usage_limit_exceeded.txt', context)

        send_mail(
            subject=subject,
            message=text_message,
            html_message=html_message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[subscription.organization.owner.email],
            fail_silently=True
        )

    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"Failed to send usage limit exceeded email: {str(e)}")


def send_usage_warning_email(subscription, usage_type, percentage):
    """
    Send warning email when approaching usage limit
    """
    try:
        context = {
            'subscription': subscription,
            'organization': subscription.organization,
            'usage_type': usage_type,
            'percentage': percentage,
            'plan': subscription.plan,
            'owner': subscription.organization.owner,
            'billing_url': f"{settings.FRONTEND_ADDRESS}/organizations/{subscription.organization.id}/billing"
        }

        subject = f"Usage Warning - {subscription.organization.name}"
        html_message = render_to_string('subscriptions/emails/usage_warning.html', context)
        text_message = render_to_string('subscriptions/emails/usage_warning.txt', context)

        send_mail(
            subject=subject,
            message=text_message,
            html_message=html_message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[subscription.organization.owner.email],
            fail_silently=True
        )

    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"Failed to send usage warning email: {str(e)}")


def send_subscription_suspended_email(subscription):
    """
    Send email when subscription is suspended
    """
    try:
        context = {
            'subscription': subscription,
            'organization': subscription.organization,
            'plan': subscription.plan,
            'owner': subscription.organization.owner,
            'billing_url': f"{settings.FRONTEND_ADDRESS}/organizations/{subscription.organization.id}/billing"
        }

        subject = f"Subscription Suspended - {subscription.organization.name}"
        html_message = render_to_string('subscriptions/emails/subscription_suspended.html', context)
        text_message = render_to_string('subscriptions/emails/subscription_suspended.txt', context)

        send_mail(
            subject=subject,
            message=text_message,
            html_message=html_message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[subscription.organization.owner.email],
            fail_silently=True
        )

    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"Failed to send subscription suspended email: {str(e)}")


def send_subscription_reactivated_email(subscription):
    """
    Send email when subscription is reactivated
    """
    try:
        context = {
            'subscription': subscription,
            'organization': subscription.organization,
            'plan': subscription.plan,
            'owner': subscription.organization.owner,
            'dashboard_url': f"{settings.FRONTEND_ADDRESS}/organizations/{subscription.organization.id}/dashboard"
        }

        subject = f"Subscription Reactivated - {subscription.organization.name}"
        html_message = render_to_string('subscriptions/emails/subscription_reactivated.html', context)
        text_message = render_to_string('subscriptions/emails/subscription_reactivated.txt', context)

        send_mail(
            subject=subject,
            message=text_message,
            html_message=html_message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[subscription.organization.owner.email],
            fail_silently=True
        )

    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"Failed to send subscription reactivated email: {str(e)}")


# Cleanup old records periodically
@receiver(post_save, sender=UsageRecord)
def cleanup_old_usage_records(sender, **kwargs):
    """
    Clean up old usage records to prevent database bloat
    """
    import random

    # Only run cleanup 1% of the time
    if random.randint(1, 100) == 1:
        cutoff_date = timezone.now() - timedelta(days=365)  # Keep 1 year of data

        old_records = UsageRecord.objects.filter(created_at__lt=cutoff_date)
        count = old_records.count()

        if count > 1000:  # Only delete if there are many old records
            old_records.delete()

            import logging
            logger = logging.getLogger(__name__)
            logger.info(f"Cleaned up {count} old usage records")