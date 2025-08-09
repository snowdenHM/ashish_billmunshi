from django.db.models.signals import post_save, post_delete
from django.contrib.auth.signals import user_logged_in, user_logged_out
from django.dispatch import receiver
from django.contrib.auth import get_user_model
from django.contrib.sessions.models import Session
from django.utils import timezone
from allauth.account.signals import email_confirmed

from .models import (
    UserPreference,
    UserActivity,
    UserSession,
    UserNotification
)

User = get_user_model()


@receiver(post_save, sender=User)
def create_user_preferences(sender, instance, created, **kwargs):
    """
    Create user preferences when a new user is created
    """
    if created:
        UserPreference.objects.create(user=instance)

        # Create welcome notification
        UserNotification.create_notification(
            user=instance,
            title="Welcome to Billmunshi!",
            message="Welcome to Billmunshi! Complete your profile to get started.",
            notification_type='info',
            action_url="/users/profile/",
            action_text="Complete Profile"
        )


@receiver(email_confirmed)
def mark_user_as_verified(sender, request, email_address, **kwargs):
    """
    Mark user as verified when email is confirmed
    """
    user = email_address.user
    if not user.is_verified:
        user.is_verified = True
        user.save(update_fields=['is_verified'])

        # Log email verification activity
        UserActivity.objects.create(
            user=user,
            action='email_verified',
            description='Email address verified successfully',
            ip_address=get_client_ip(request) if request else None
        )

        # Create verification success notification
        UserNotification.create_notification(
            user=user,
            title="Email Verified!",
            message="Your email address has been successfully verified.",
            notification_type='success'
        )


@receiver(user_logged_in)
def user_login_handler(sender, request, user, **kwargs):
    """
    Handle user login activities
    """
    ip_address = get_client_ip(request)
    user_agent = request.META.get('HTTP_USER_AGENT', '')

    # Update user's last activity
    user.update_last_activity(ip_address)

    # Log login activity
    UserActivity.objects.create(
        user=user,
        action='login',
        description='User logged in successfully',
        ip_address=ip_address,
        user_agent=user_agent
    )

    # Create or update user session
    if hasattr(request, 'session'):
        session_key = request.session.session_key
        if session_key:
            # Calculate session expiry
            expires_at = timezone.now() + timezone.timedelta(
                seconds=request.session.get_expiry_age()
            )

            UserSession.objects.update_or_create(
                session_key=session_key,
                defaults={
                    'user': user,
                    'ip_address': ip_address,
                    'user_agent': user_agent,
                    'is_active': True,
                    'expires_at': expires_at,
                    'last_activity': timezone.now()
                }
            )


@receiver(user_logged_out)
def user_logout_handler(sender, request, user, **kwargs):
    """
    Handle user logout activities
    """
    if user:  # user might be None for anonymous sessions
        ip_address = get_client_ip(request)

        # Log logout activity
        UserActivity.objects.create(
            user=user,
            action='logout',
            description='User logged out',
            ip_address=ip_address,
            user_agent=request.META.get('HTTP_USER_AGENT', '')
        )

        # Deactivate session
        if hasattr(request, 'session') and request.session.session_key:
            try:
                user_session = UserSession.objects.get(
                    session_key=request.session.session_key,
                    user=user
                )
                user_session.terminate()
            except UserSession.DoesNotExist:
                pass


@receiver(post_delete, sender=Session)
def cleanup_user_session(sender, instance, **kwargs):
    """
    Clean up UserSession when Django session is deleted
    """
    try:
        user_session = UserSession.objects.get(session_key=instance.session_key)
        user_session.terminate()
    except UserSession.DoesNotExist:
        pass


@receiver(post_save, sender=UserActivity)
def check_suspicious_activity(sender, instance, created, **kwargs):
    """
    Check for suspicious activities and create notifications
    """
    if not created:
        return

    user = instance.user
    suspicious_actions = ['password_change', 'email_change', 'multiple_failed_logins']

    if instance.action in suspicious_actions:
        # Check for multiple suspicious activities in short time
        recent_suspicious = UserActivity.objects.filter(
            user=user,
            action__in=suspicious_actions,
            created_at__gte=timezone.now() - timezone.timedelta(hours=1)
        ).count()

        if recent_suspicious >= 3:
            UserNotification.create_notification(
                user=user,
                title="Suspicious Activity Detected",
                message="We've detected unusual activity on your account. Please review your recent activities.",
                notification_type='security',
                action_url="/users/activities/",
                action_text="Review Activities"
            )


def get_client_ip(request):
    """
    Get client IP address from request
    """
    if not request:
        return None

    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        ip = x_forwarded_for.split(',')[0]
    else:
        ip = request.META.get('REMOTE_ADDR')
    return ip


# Organization-related signals for user notifications
@receiver(post_save, sender='teams.OrganizationMember')
def notify_user_organization_join(sender, instance, created, **kwargs):
    """
    Notify user when they join an organization
    """
    if created and instance.is_active:
        UserNotification.create_notification(
            user=instance.user,
            title=f"Joined {instance.organization.name}",
            message=f"You are now a {instance.role.get_name_display()} in {instance.organization.name}.",
            notification_type='organization',
            organization=instance.organization,
            action_url=f"/organizations/{instance.organization.id}/",
            action_text="View Organization"
        )


@receiver(post_save, sender='teams.Invitation')
def notify_user_invitation_received(sender, instance, created, **kwargs):
    """
    Notify user when they receive an invitation
    """
    if created and instance.status == 'pending':
        # Try to find existing user
        try:
            user = User.objects.get(email=instance.email)
            UserNotification.create_notification(
                user=user,
                title=f"Invitation to {instance.organization.name}",
                message=f"{instance.invited_by.get_display_name()} invited you to join {instance.organization.name} as {instance.role.get_name_display()}.",
                notification_type='invitation',
                organization=instance.organization,
                action_url=f"/invitations/{instance.token}/",
                action_text="View Invitation"
            )
        except User.DoesNotExist:
            # User doesn't exist yet, notification will be created when they sign up
            pass


@receiver(post_save, sender='teams.OrganizationAPIKey')
def notify_api_key_created(sender, instance, created, **kwargs):
    """
    Notify relevant users when API key is created
    """
    if created:
        # Notify the creator
        UserNotification.create_notification(
            user=instance.created_by,
            title="API Key Created",
            message=f"New API key '{instance.name}' created for {instance.organization.name}.",
            notification_type='api',
            organization=instance.organization,
            action_url=f"/organizations/{instance.organization.id}/api-keys/",
            action_text="Manage API Keys"
        )

        # Log activity
        UserActivity.objects.create(
            user=instance.created_by,
            action='api_key_create',
            description=f"Created API key '{instance.name}'",
            organization=instance.organization,
            metadata={'api_key_name': instance.name}
        )


@receiver(post_delete, sender='teams.OrganizationAPIKey')
def notify_api_key_deleted(sender, instance, **kwargs):
    """
    Notify when API key is deleted
    """
    # Create notification for organization members with API key management permissions
    from apps.teams.models import Role

    members_to_notify = instance.organization.members.filter(
        is_active=True,
        role__can_manage_api_keys=True
    ).exclude(user=instance.created_by)

    for member in members_to_notify:
        UserNotification.create_notification(
            user=member.user,
            title="API Key Deleted",
            message=f"API key '{instance.name}' was deleted from {instance.organization.name}.",
            notification_type='api',
            organization=instance.organization
        )


# Cleanup old activities and sessions periodically
# This would typically be done with a Celery task, but for demonstration:
@receiver(post_save, sender=UserActivity)
def cleanup_old_activities(sender, **kwargs):
    """
    Clean up old activities (keep last 1000 per user)
    """
    # This is a simple implementation - in production, use Celery tasks
    import random

    # Only run cleanup 1% of the time to avoid performance issues
    if random.randint(1, 100) == 1:
        from django.db import connection

        # Keep only the latest 1000 activities per user
        with connection.cursor() as cursor:
            cursor.execute("""
                           DELETE
                           FROM user_activities
                           WHERE id NOT IN (SELECT id
                                            FROM (SELECT id
                                                  FROM user_activities
                                                  ORDER BY user_id, created_at DESC LIMIT 1000) AS subquery)
                           """)


@receiver(post_save, sender=UserSession)
def cleanup_expired_sessions(sender, **kwargs):
    """
    Clean up expired sessions
    """
    import random

    # Only run cleanup 5% of the time
    if random.randint(1, 20) == 1:
        expired_sessions = UserSession.objects.filter(
            expires_at__lt=timezone.now()
        )
        expired_sessions.update(is_active=False)
