from django.db.models.signals import post_save
from django.dispatch import receiver
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.conf import settings
from django.utils import timezone

from .models import Organization, Invitation, OrganizationMember, Role


@receiver(post_save, sender=Organization)
def create_default_roles(sender, instance, created, **kwargs):
    """
    Create default roles when the first organization is created
    """
    if created and not Role.objects.exists():
        # Create default roles
        Role.objects.bulk_create([
            Role(
                name=Role.OWNER,
                description='Organization Owner with full access',
                can_manage_organization=True,
                can_manage_members=True,
                can_manage_api_keys=True,
                can_view_analytics=True,
                can_manage_billing=True,
            ),
            Role(
                name=Role.ADMIN,
                description='Organization Admin with management access',
                can_manage_organization=True,
                can_manage_members=True,
                can_manage_api_keys=True,
                can_view_analytics=True,
                can_manage_billing=False,
            ),
            Role(
                name=Role.MEMBER,
                description='Organization Member with standard access',
                can_manage_organization=False,
                can_manage_members=False,
                can_manage_api_keys=False,
                can_view_analytics=True,
                can_manage_billing=False,
            ),
            Role(
                name=Role.VIEWER,
                description='Organization Viewer with read-only access',
                can_manage_organization=False,
                can_manage_members=False,
                can_manage_api_keys=False,
                can_view_analytics=True,
                can_manage_billing=False,
            ),
        ])


@receiver(post_save, sender=Invitation)
def send_invitation_email(sender, instance, created, **kwargs):
    """
    Send invitation email when a new invitation is created
    """
    if created and instance.status == Invitation.PENDING:
        try:
            # Prepare email context
            context = {
                'invitation': instance,
                'organization': instance.organization,
                'invited_by': instance.invited_by,
                'accept_url': f"{settings.FRONTEND_ADDRESS}/invitations/{instance.token}/accept",
                'decline_url': f"{settings.FRONTEND_ADDRESS}/invitations/{instance.token}/decline",
                'expires_at': instance.expires_at,
            }

            # Render email templates
            subject = f"Invitation to join {instance.organization.name}"
            html_message = render_to_string('teams/emails/invitation.html', context)
            text_message = render_to_string('teams/emails/invitation.txt', context)

            # Send email
            send_mail(
                subject=subject,
                message=text_message,
                html_message=html_message,
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[instance.email],
                fail_silently=False,
            )

        except Exception as e:
            # Log the error but don't fail the invitation creation
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"Failed to send invitation email: {str(e)}")


@receiver(post_save, sender=OrganizationMember)
def send_welcome_email(sender, instance, created, **kwargs):
    """
    Send welcome email when a new member joins the organization
    """
    if created and instance.is_active:
        try:
            # Prepare email context
            context = {
                'member': instance,
                'organization': instance.organization,
                'user': instance.user,
                'role': instance.role,
                'dashboard_url': f"{settings.FRONTEND_ADDRESS}/organizations/{instance.organization.id}/dashboard",
            }

            # Render email templates
            subject = f"Welcome to {instance.organization.name}!"
            html_message = render_to_string('teams/emails/welcome.html', context)
            text_message = render_to_string('teams/emails/welcome.txt', context)

            # Send email
            send_mail(
                subject=subject,
                message=text_message,
                html_message=html_message,
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[instance.user.email],
                fail_silently=False,
            )

        except Exception as e:
            # Log the error but don't fail the member creation
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"Failed to send welcome email: {str(e)}")


@receiver(post_save, sender=Invitation)
def cleanup_expired_invitations(sender, instance, **kwargs):
    """
    Mark expired invitations as expired
    """
    if instance.status == Invitation.PENDING and instance.is_expired:
        instance.status = Invitation.EXPIRED
        instance.save(update_fields=['status'])


# Optional: Clean up expired invitations periodically
# This would typically be done with a Celery task, but for simplicity:
@receiver(post_save, sender=Invitation)
def mark_expired_invitations(sender, **kwargs):
    """
    Mark all expired pending invitations as expired
    """
    expired_invitations = Invitation.objects.filter(
        status=Invitation.PENDING,
        expires_at__lt=timezone.now()
    )
    expired_invitations.update(status=Invitation.EXPIRED)
