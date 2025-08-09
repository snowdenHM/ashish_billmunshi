from celery import shared_task
from django.utils import timezone
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.conf import settings
from datetime import timedelta
import logging

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3)
def send_email_async(self, subject, message, from_email, recipient_list, html_message=None):
    """
    Send email asynchronously
    """
    try:
        send_mail(
            subject=subject,
            message=message,
            from_email=from_email,
            recipient_list=recipient_list,
            html_message=html_message,
            fail_silently=False
        )
        logger.info(f"Email sent successfully to {recipient_list}")
        return f"Email sent to {len(recipient_list)} recipients"
    except Exception as exc:
        logger.error(f"Failed to send email: {str(exc)}")
        raise self.retry(exc=exc, countdown=60 * (2 ** self.request.retries))


@shared_task(bind=True, max_retries=3)
def send_user_notification_email(self, user_id, template_name, context=None, subject=None):
    """
    Send notification email to user
    """
    try:
        from django.contrib.auth import get_user_model
        User = get_user_model()

        user = User.objects.get(id=user_id)
        context = context or {}
        context.update({
            'user': user,
            'site_name': getattr(settings, 'PROJECT_METADATA', {}).get('NAME', 'Billmunshi'),
            'site_url': getattr(settings, 'FRONTEND_ADDRESS', 'http://localhost:3000')
        })

        # Render email templates
        html_message = render_to_string(f'users/emails/{template_name}.html', context)
        text_message = render_to_string(f'users/emails/{template_name}.txt', context)

        if not subject:
            subject = f"Notification from {context['site_name']}"

        send_mail(
            subject=subject,
            message=text_message,
            html_message=html_message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[user.email],
            fail_silently=False
        )

        logger.info(f"Notification email sent to user {user.email}")
        return f"Notification email sent to {user.email}"

    except Exception as exc:
        logger.error(f"Failed to send notification email: {str(exc)}")
        raise self.retry(exc=exc, countdown=60 * (2 ** self.request.retries))


@shared_task
def cleanup_expired_sessions():
    """
    Clean up expired user sessions and Django sessions
    """
    from .models import UserSession
    from django.contrib.sessions.models import Session

    try:
        # Mark expired UserSessions as inactive
        expired_count = UserSession.objects.filter(
            expires_at__lt=timezone.now(),
            is_active=True
        ).update(is_active=False)

        # Delete expired Django sessions
        django_expired_count, _ = Session.objects.filter(
            expire_date__lt=timezone.now()
        ).delete()

        logger.info(f"Cleaned up {expired_count} user sessions and {django_expired_count} Django sessions")
        return f"Cleaned up {expired_count} user sessions and {django_expired_count} Django sessions"

    except Exception as exc:
        logger.error(f"Failed to cleanup sessions: {str(exc)}")
        raise exc


@shared_task
def cleanup_old_activities(days=90):
    """
    Clean up old user activities (keep last N days)
    """
    from .models import UserActivity

    try:
        cutoff_date = timezone.now() - timedelta(days=days)

        # Delete old activities but keep the latest 100 per user
        from django.db import connection

        with connection.cursor() as cursor:
            cursor.execute("""
                           DELETE
                           FROM user_activities
                           WHERE created_at < %s
                             AND id NOT IN (SELECT id
                                            FROM (SELECT id
                                                  FROM user_activities
                                                  WHERE user_id = user_activities.user_id
                                                  ORDER BY created_at DESC LIMIT 100) AS recent_activities)
                           """, [cutoff_date])

            deleted_count = cursor.rowcount

        logger.info(f"Cleaned up {deleted_count} old activities")
        return f"Cleaned up {deleted_count} old activities"

    except Exception as exc:
        logger.error(f"Failed to cleanup old activities: {str(exc)}")
        raise exc


@shared_task
def cleanup_old_notifications(days=180):
    """
    Clean up old read notifications
    """
    from .models import UserNotification

    try:
        cutoff_date = timezone.now() - timedelta(days=days)

        # Delete old read notifications
        deleted_count, _ = UserNotification.objects.filter(
            created_at__lt=cutoff_date,
            is_read=True
        ).delete()

        logger.info(f"Cleaned up {deleted_count} old notifications")
        return f"Cleaned up {deleted_count} old notifications"

    except Exception as exc:
        logger.error(f"Failed to cleanup old notifications: {str(exc)}")
        raise exc


@shared_task(bind=True, max_retries=3)
def send_security_alert_email(self, user_id, alert_type, details):
    """
    Send security alert email to user
    """
    try:
        from django.contrib.auth import get_user_model
        User = get_user_model()

        user = User.objects.get(id=user_id)

        context = {
            'user': user,
            'site_name': getattr(settings, 'PROJECT_METADATA', {}).get('NAME', 'Billmunshi'),
            'site_url': getattr(settings, 'FRONTEND_ADDRESS', 'http://localhost:3000'),
            **details
        }

        # Security alert templates
        templates = {
            'login_from_new_device': {
                'subject': 'New device login detected',
                'template': 'security_alert_new_device'
            },
            'password_changed': {
                'subject': 'Password changed successfully',
                'template': 'security_alert_password_change'
            },
            'suspicious_activity': {
                'subject': 'Suspicious activity detected',
                'template': 'security_alert_suspicious'
            }
        }

        template_config = templates.get(alert_type)
        if not template_config:
            raise ValueError(f"Unknown alert type: {alert_type}")

        html_message = render_to_string(f'users/emails/{template_config["template"]}.html', context)
        text_message = render_to_string(f'users/emails/{template_config["template"]}.txt', context)

        send_mail(
            subject=template_config['subject'],
            message=text_message,
            html_message=html_message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[user.email],
            fail_silently=False
        )

        logger.info(f"Security alert email sent to user {user.email}")
        return f"Security alert sent to {user.email}"

    except Exception as exc:
        logger.error(f"Failed to send security alert: {str(exc)}")
        raise self.retry(exc=exc, countdown=60 * (2 ** self.request.retries))


@shared_task
def process_user_data_export(user_id):
    """
    Process user data export request
    """
    try:
        from django.contrib.auth import get_user_model
        from .utils import export_user_data
        import json
        import os
        from django.core.files.base import ContentFile

        User = get_user_model()
        user = User.objects.get(id=user_id)

        # Export user data
        user_data = export_user_data(user)

        # Convert to JSON
        json_data = json.dumps(user_data, indent=2, default=str)

        # Save to file (in production, save to cloud storage)
        filename = f"user_data_export_{user.id}_{timezone.now().strftime('%Y%m%d_%H%M%S')}.json"
        file_path = os.path.join(settings.MEDIA_ROOT, 'exports', filename)

        # Ensure directory exists
        os.makedirs(os.path.dirname(file_path), exist_ok=True)

        with open(file_path, 'w') as f:
            f.write(json_data)

        # Send email with download link
        context = {
            'user': user,
            'download_url': f"{settings.FRONTEND_ADDRESS}/media/exports/{filename}",
            'expires_at': timezone.now() + timedelta(days=7)
        }

        send_user_notification_email.delay(
            user.id,
            'data_export_ready',
            context,
            'Your data export is ready'
        )

        logger.info(f"User data export completed for user {user.email}")
        return f"Data export completed for {user.email}"

    except Exception as exc:
        logger.error(f"Failed to process data export: {str(exc)}")
        raise exc


@shared_task
def send_digest_emails():
    """
    Send daily/weekly digest emails to users
    """
    from django.contrib.auth import get_user_model
    from .models import UserPreference, UserNotification

    try:
        User = get_user_model()

        # Get users who want daily digests
        daily_users = User.objects.filter(
            preferences__notification_frequency='daily',
            email_notifications=True,
            is_active=True
        ).select_related('preferences')

        for user in daily_users:
            # Get unread notifications from last 24 hours
            yesterday = timezone.now() - timedelta(days=1)
            notifications = user.notifications.filter(
                created_at__gte=yesterday,
                is_read=False
            )[:10]  # Limit to 10 notifications

            if notifications.exists():
                context = {
                    'user': user,
                    'notifications': notifications,
                    'notification_count': notifications.count(),
                    'dashboard_url': f"{settings.FRONTEND_ADDRESS}/dashboard"
                }

                send_user_notification_email.delay(
                    user.id,
                    'daily_digest',
                    context,
                    'Daily Activity Digest'
                )

        # Similar logic for weekly digests
        if timezone.now().weekday() == 0:  # Monday
            weekly_users = User.objects.filter(
                preferences__notification_frequency='weekly',
                email_notifications=True,
                is_active=True
            ).select_related('preferences')

            for user in weekly_users:
                last_week = timezone.now() - timedelta(days=7)
                notifications = user.notifications.filter(
                    created_at__gte=last_week
                )[:20]

                if notifications.exists():
                    context = {
                        'user': user,
                        'notifications': notifications,
                        'notification_count': notifications.count(),
                        'dashboard_url': f"{settings.FRONTEND_ADDRESS}/dashboard"
                    }

                    send_user_notification_email.delay(
                        user.id,
                        'weekly_digest',
                        context,
                        'Weekly Activity Summary'
                    )

        logger.info("Digest emails processed successfully")
        return "Digest emails sent"

    except Exception as exc:
        logger.error(f"Failed to send digest emails: {str(exc)}")
        raise exc


@shared_task
def update_user_statistics():
    """
    Update user statistics and activity summaries
    """
    try:
        from django.contrib.auth import get_user_model
        from .models import UserActivity
        from django.db.models import Count

        User = get_user_model()

        # Update activity counts for users
        users_with_activity = User.objects.filter(
            activities__created_at__gte=timezone.now() - timedelta(days=30)
        ).annotate(
            recent_activity_count=Count('activities')
        )

        # You can store these statistics in cache or a separate model
        # For now, just log the information
        total_users = users_with_activity.count()

        logger.info(f"Updated statistics for {total_users} users")
        return f"Updated statistics for {total_users} users"

    except Exception as exc:
        logger.error(f"Failed to update user statistics: {str(exc)}")
        raise exc


@shared_task(bind=True, max_retries=3)
def process_bulk_user_action(self, action_type, user_ids, action_data):
    """
    Process bulk actions on users
    """
    try:
        from django.contrib.auth import get_user_model
        User = get_user_model()

        users = User.objects.filter(id__in=user_ids)
        processed_count = 0

        if action_type == 'send_notification':
            from .models import UserNotification

            for user in users:
                UserNotification.create_notification(
                    user=user,
                    title=action_data['title'],
                    message=action_data['message'],
                    notification_type=action_data.get('type', 'info')
                )
                processed_count += 1

        elif action_type == 'send_email':
            for user in users:
                send_user_notification_email.delay(
                    user.id,
                    action_data['template'],
                    action_data.get('context', {}),
                    action_data['subject']
                )
                processed_count += 1

        elif action_type == 'update_preferences':
            from .models import UserPreference

            for user in users:
                preferences, _ = UserPreference.objects.get_or_create(user=user)
                for key, value in action_data.items():
                    if hasattr(preferences, key):
                        setattr(preferences, key, value)
                preferences.save()
                processed_count += 1

        logger.info(f"Processed bulk action {action_type} for {processed_count} users")
        return f"Processed {action_type} for {processed_count} users"

    except Exception as exc:
        logger.error(f"Failed to process bulk action: {str(exc)}")
        raise self.retry(exc=exc, countdown=60 * (2 ** self.request.retries))