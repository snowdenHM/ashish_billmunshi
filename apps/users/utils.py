from django.contrib.auth import get_user_model
from django.utils import timezone
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.conf import settings
from typing import Optional, List, Dict, Any
import hashlib
import secrets
import string
from datetime import timedelta

from .models import UserActivity, UserNotification, UserSession

User = get_user_model()


def generate_secure_token(length: int = 32) -> str:
    """
    Generate a cryptographically secure random token
    """
    alphabet = string.ascii_letters + string.digits
    return ''.join(secrets.choice(alphabet) for _ in range(length))


def hash_user_data(data: str) -> str:
    """
    Hash sensitive user data for storage/comparison
    """
    return hashlib.sha256(data.encode('utf-8')).hexdigest()


def get_client_ip(request) -> str:
    """
    Extract client IP address from request
    """
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        ip = x_forwarded_for.split(',')[0].strip()
    else:
        ip = request.META.get('REMOTE_ADDR', '0.0.0.0')
    return ip


def log_user_activity(
        user: User,
        action: str,
        description: str = '',
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
        organization=None,
        metadata: Optional[Dict[str, Any]] = None
) -> UserActivity:
    """
    Helper function to log user activities
    """
    return UserActivity.objects.create(
        user=user,
        action=action,
        description=description,
        ip_address=ip_address,
        user_agent=user_agent,
        organization=organization,
        metadata=metadata or {}
    )


def create_user_notification(
        user: User,
        title: str,
        message: str,
        notification_type: str = 'info',
        organization=None,
        action_url: Optional[str] = None,
        action_text: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
) -> UserNotification:
    """
    Helper function to create user notifications
    """
    return UserNotification.create_notification(
        user=user,
        title=title,
        message=message,
        notification_type=notification_type,
        organization=organization,
        action_url=action_url,
        action_text=action_text,
        metadata=metadata or {}
    )


def send_user_email(
        user: User,
        subject: str,
        template_name: str,
        context: Optional[Dict[str, Any]] = None,
        from_email: Optional[str] = None
) -> bool:
    """
    Send email to user with template rendering
    """
    try:
        context = context or {}
        context.update({
            'user': user,
            'site_name': getattr(settings, 'SITE_NAME', 'Billmunshi'),
            'site_url': getattr(settings, 'FRONTEND_ADDRESS', 'http://localhost:3000')
        })

        # Render email templates
        html_message = render_to_string(f'users/emails/{template_name}.html', context)
        text_message = render_to_string(f'users/emails/{template_name}.txt', context)

        send_mail(
            subject=subject,
            message=text_message,
            html_message=html_message,
            from_email=from_email or settings.DEFAULT_FROM_EMAIL,
            recipient_list=[user.email],
            fail_silently=False
        )
        return True

    except Exception as e:
        # Log the error
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"Failed to send email to {user.email}: {str(e)}")
        return False


def cleanup_user_data(user: User, keep_days: int = 90) -> Dict[str, int]:
    """
    Clean up old user data (activities, notifications, sessions)
    """
    cutoff_date = timezone.now() - timedelta(days=keep_days)

    # Clean up old activities
    old_activities = user.activities.filter(created_at__lt=cutoff_date)
    activities_count = old_activities.count()
    old_activities.delete()

    # Clean up old read notifications
    old_notifications = user.notifications.filter(
        created_at__lt=cutoff_date,
        is_read=True
    )
    notifications_count = old_notifications.count()
    old_notifications.delete()

    # Clean up old inactive sessions
    old_sessions = user.sessions.filter(
        created_at__lt=cutoff_date,
        is_active=False
    )
    sessions_count = old_sessions.count()
    old_sessions.delete()

    return {
        'activities_deleted': activities_count,
        'notifications_deleted': notifications_count,
        'sessions_deleted': sessions_count
    }


def get_user_statistics(user: User) -> Dict[str, Any]:
    """
    Get comprehensive user statistics
    """
    today = timezone.now().date()
    week_ago = timezone.now() - timedelta(days=7)
    month_ago = timezone.now() - timedelta(days=30)

    return {
        # Basic stats
        'total_organizations': user.total_organizations,
        'owned_organizations': user.owned_organizations_count,
        'account_age_days': (today - user.date_joined.date()).days,

        # Activity stats
        'total_activities': user.activities.count(),
        'activities_this_week': user.activities.filter(created_at__gte=week_ago).count(),
        'activities_this_month': user.activities.filter(created_at__gte=month_ago).count(),

        # Notification stats
        'total_notifications': user.notifications.count(),
        'unread_notifications': user.notifications.filter(is_read=False).count(),
        'notifications_this_week': user.notifications.filter(created_at__gte=week_ago).count(),

        # Session stats
        'active_sessions': user.sessions.filter(
            is_active=True,
            expires_at__gt=timezone.now()
        ).count(),
        'total_sessions': user.sessions.count(),

        # Security stats
        'has_2fa': user.two_factor_enabled,
        'email_verified': user.has_verified_email,
        'last_login': user.last_login,
        'last_activity': user.last_activity_at,
    }


def check_user_permissions(user: User, organization=None) -> Dict[str, bool]:
    """
    Check user permissions in organization context
    """
    permissions = {
        'can_create_organization': True,  # All users can create
        'can_invite_users': False,
        'can_manage_api_keys': False,
        'can_view_analytics': False,
        'can_manage_billing': False,
    }

    if organization:
        user_role = organization.get_user_role(user)
        if user_role:
            permissions.update({
                'can_invite_users': user_role.can_manage_members,
                'can_manage_api_keys': user_role.can_manage_api_keys,
                'can_view_analytics': user_role.can_view_analytics,
                'can_manage_billing': user_role.can_manage_billing,
            })

    return permissions


def validate_user_action(user: User, action: str, **kwargs) -> tuple[bool, str]:
    """
    Validate if user can perform a specific action
    """
    if not user.is_active:
        return False, "User account is inactive"

    if not user.has_verified_email and action in ['create_organization', 'invite_user']:
        return False, "Email verification required"

    if not user.is_onboarded and action != 'complete_onboarding':
        return False, "Please complete your profile setup"

    # Organization-specific validations
    organization = kwargs.get('organization')
    if organization and action in ['invite_user', 'manage_api_keys', 'view_analytics']:
        user_role = organization.get_user_role(user)
        if not user_role:
            return False, "You are not a member of this organization"

        action_permissions = {
            'invite_user': user_role.can_manage_members,
            'manage_api_keys': user_role.can_manage_api_keys,
            'view_analytics': user_role.can_view_analytics,
            'manage_billing': user_role.can_manage_billing,
        }

        if not action_permissions.get(action, False):
            return False, f"Insufficient permissions to {action.replace('_', ' ')}"

    return True, "Action allowed"


def get_user_activity_summary(user: User, days: int = 30) -> Dict[str, Any]:
    """
    Get user activity summary for the last N days
    """
    start_date = timezone.now() - timedelta(days=days)
    activities = user.activities.filter(created_at__gte=start_date)

    # Group activities by action type
    activity_counts = {}
    for activity in activities:
        action = activity.action
        activity_counts[action] = activity_counts.get(action, 0) + 1

    # Get most active days
    from django.db.models import Count
    daily_activity = activities.extra(
        select={'day': 'date(created_at)'}
    ).values('day').annotate(
        count=Count('id')
    ).order_by('-count')[:7]

    return {
        'total_activities': activities.count(),
        'activity_by_type': activity_counts,
        'most_active_days': list(daily_activity),
        'period_days': days
    }


def send_security_alert(user: User, alert_type: str, details: Dict[str, Any]) -> bool:
    """
    Send security alert to user
    """
    alert_templates = {
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
        },
        'api_key_created': {
            'subject': 'New API key created',
            'template': 'security_alert_api_key'
        }
    }

    alert_config = alert_templates.get(alert_type)
    if not alert_config:
        return False

    # Send email
    email_sent = send_user_email(
        user=user,
        subject=alert_config['subject'],
        template_name=alert_config['template'],
        context=details
    )

    # Create in-app notification
    create_user_notification(
        user=user,
        title=alert_config['subject'],
        message=f"Security alert: {alert_type.replace('_', ' ')}",
        notification_type='security',
        metadata=details
    )

    return email_sent


def bulk_update_user_preferences(users: List[User], preferences: Dict[str, Any]) -> int:
    """
    Bulk update user preferences
    """
    from .models import UserPreference

    updated_count = 0
    for user in users:
        user_pref, created = UserPreference.objects.get_or_create(user=user)

        for key, value in preferences.items():
            if hasattr(user_pref, key):
                setattr(user_pref, key, value)

        user_pref.save()
        updated_count += 1

    return updated_count


def export_user_data(user: User) -> Dict[str, Any]:
    """
    Export all user data for GDPR compliance
    """
    # Basic user data
    user_data = {
        'id': user.id,
        'username': user.username,
        'email': user.email,
        'first_name': user.first_name,
        'last_name': user.last_name,
        'phone': user.phone,
        'bio': user.bio,
        'location': user.location,
        'website': user.website,
        'timezone': user.timezone,
        'language': user.language,
        'date_joined': user.date_joined.isoformat(),
        'last_login': user.last_login.isoformat() if user.last_login else None,
        'last_activity_at': user.last_activity_at.isoformat() if user.last_activity_at else None,
    }

    # Preferences
    try:
        preferences = user.preferences
        user_data['preferences'] = {
            'dashboard_layout': preferences.dashboard_layout,
            'notification_frequency': preferences.notification_frequency,
            'default_api_format': preferences.default_api_format,
            'profile_visibility': preferences.profile_visibility,
            'theme': preferences.theme,
        }
    except:
        user_data['preferences'] = {}

    # Activities
    user_data['activities'] = [
        {
            'action': activity.action,
            'description': activity.description,
            'created_at': activity.created_at.isoformat(),
            'ip_address': activity.ip_address,
            'metadata': activity.metadata
        }
        for activity in user.activities.all()[:1000]  # Limit to last 1000
    ]

    # Notifications
    user_data['notifications'] = [
        {
            'title': notif.title,
            'message': notif.message,
            'notification_type': notif.notification_type,
            'is_read': notif.is_read,
            'created_at': notif.created_at.isoformat(),
        }
        for notif in user.notifications.all()[:500]  # Limit to last 500
    ]

    # Organization memberships
    user_data['organizations'] = [
        {
            'organization_name': membership.organization.name,
            'role': membership.role.name,
            'joined_at': membership.joined_at.isoformat(),
            'is_active': membership.is_active,
        }
        for membership in user.organization_memberships.all()
    ]

    return user_data


def anonymize_user_data(user: User) -> bool:
    """
    Anonymize user data while preserving system integrity
    """
    try:
        # Generate anonymous identifiers
        anonymous_id = f"anon_{generate_secure_token(8)}"

        # Anonymize personal data
        user.username = anonymous_id
        user.email = f"{anonymous_id}@deleted.local"
        user.first_name = "Deleted"
        user.last_name = "User"
        user.phone = ""
        user.bio = ""
        user.location = ""
        user.website = ""
        user.is_active = False

        # Clear avatar
        if user.avatar:
            user.avatar.delete()

        user.save()

        # Anonymize activities (keep structure but remove sensitive data)
        user.activities.update(
            ip_address=None,
            user_agent="",
            metadata={}
        )

        # Delete notifications and sessions
        user.notifications.all().delete()
        user.sessions.all().delete()

        return True

    except Exception:
        return False


def get_user_security_score(user: User) -> Dict[str, Any]:
    """
    Calculate user security score based on various factors
    """
    score = 0
    max_score = 100
    recommendations = []

    # Email verification (20 points)
    if user.has_verified_email:
        score += 20
    else:
        recommendations.append("Verify your email address")

    # Two-factor authentication (25 points)
    if user.two_factor_enabled:
        score += 25
    else:
        recommendations.append("Enable two-factor authentication")

    # Profile completeness (15 points)
    profile_fields = [user.first_name, user.last_name, user.phone, user.bio]
    completed_fields = sum(1 for field in profile_fields if field)
    score += int((completed_fields / len(profile_fields)) * 15)

    if completed_fields < len(profile_fields):
        recommendations.append("Complete your profile information")

    # Recent password change (15 points)
    recent_password_change = user.activities.filter(
        action='password_change',
        created_at__gte=timezone.now() - timedelta(days=90)
    ).exists()

    if recent_password_change:
        score += 15
    else:
        recommendations.append("Consider changing your password regularly")

    # Active session management (10 points)
    active_sessions_count = user.sessions.filter(
        is_active=True,
        expires_at__gt=timezone.now()
    ).count()

    if active_sessions_count <= 3:  # Reasonable number of sessions
        score += 10
    else:
        recommendations.append("Review and terminate unused sessions")

    # Recent activity (15 points)
    if user.last_activity_at and user.last_activity_at >= timezone.now() - timedelta(days=30):
        score += 15

    return {
        'score': score,
        'max_score': max_score,
        'percentage': round((score / max_score) * 100, 1),
        'level': 'High' if score >= 80 else 'Medium' if score >= 50 else 'Low',
        'recommendations': recommendations
    }


class UserDataManager:
    """
    Class to manage user data operations
    """

    def __init__(self, user: User):
        self.user = user

    def get_dashboard_data(self) -> Dict[str, Any]:
        """Get data for user dashboard"""
        return {
            'user': self.user,
            'statistics': get_user_statistics(self.user),
            'recent_activities': self.user.activities.all()[:10],
            'unread_notifications': self.user.notifications.filter(is_read=False)[:5],
            'organizations': self.user.get_organizations(),
            'security_score': get_user_security_score(self.user),
        }

    def cleanup_old_data(self, days: int = 90) -> Dict[str, int]:
        """Clean up old user data"""
        return cleanup_user_data(self.user, days)

    def export_data(self) -> Dict[str, Any]:
        """Export user data"""
        return export_user_data(self.user)

    def anonymize(self) -> bool:
        """Anonymize user data"""
        return anonymize_user_data(self.user)