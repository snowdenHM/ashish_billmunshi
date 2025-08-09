from allauth.account.adapter import DefaultAccountAdapter
from allauth.account.models import EmailAddress
from django.contrib.auth import get_user_model
from django.utils import timezone
from .models import Invitation

User = get_user_model()


class AcceptInvitationAdapter(DefaultAccountAdapter):
    """
    Custom account adapter that handles invitation acceptance during signup
    """

    def save_user(self, request, user, form, commit=True):
        """
        Save user and handle any pending invitations
        """
        user = super().save_user(request, user, form, commit=commit)

        if commit:
            # Check for pending invitations for this email
            self.process_pending_invitations(user)

        return user

    def process_pending_invitations(self, user):
        """
        Process any pending invitations for the user's email
        """
        pending_invitations = Invitation.objects.filter(
            email=user.email,
            status=Invitation.PENDING
        ).exclude(
            expires_at__lt=timezone.now()
        )

        for invitation in pending_invitations:
            try:
                # Check if invitation is still valid
                if not invitation.is_expired:
                    # Accept the invitation automatically
                    invitation.accept(user)

                    # Create user activity
                    from apps.users.models import UserActivity
                    UserActivity.objects.create(
                        user=user,
                        action='invitation_accept',
                        description=f'Auto-accepted invitation to {invitation.organization.name}',
                        organization=invitation.organization,
                        metadata={
                            'invitation_id': invitation.id,
                            'organization_id': invitation.organization.id,
                            'role': invitation.role.name,
                            'auto_accepted': True
                        }
                    )
                else:
                    # Mark expired invitations
                    invitation.status = Invitation.EXPIRED
                    invitation.save()

            except Exception as e:
                # Log error but don't fail user creation
                import logging
                logger = logging.getLogger(__name__)
                logger.error(f"Failed to process invitation {invitation.id}: {str(e)}")

    def confirm_email(self, request, email_address):
        """
        Handle email confirmation and process any pending invitations
        """
        super().confirm_email(request, email_address)

        # Process pending invitations after email confirmation
        if email_address.verified:
            self.process_pending_invitations(email_address.user)

    def get_login_redirect_url(self, request):
        """
        Redirect user after login, potentially to invitation acceptance page
        """
        # Check if user has pending invitations to show
        if request.user.is_authenticated:
            pending_invitations = Invitation.objects.filter(
                email=request.user.email,
                status=Invitation.PENDING
            ).exclude(
                expires_at__lt=timezone.now()
            )

            if pending_invitations.exists():
                # Redirect to invitations page if there are pending invitations
                return '/invitations/pending/'

        return super().get_login_redirect_url(request)

    def get_signup_redirect_url(self, request):
        """
        Redirect user after signup
        """
        # Check for invitation token in session
        invitation_token = request.session.get('invitation_token')
        if invitation_token:
            try:
                invitation = Invitation.objects.get(
                    token=invitation_token,
                    status=Invitation.PENDING
                )
                if not invitation.is_expired:
                    # Clear the token from session
                    del request.session['invitation_token']
                    # Redirect to specific invitation acceptance page
                    return f'/invitations/{invitation_token}/accept/'
            except Invitation.DoesNotExist:
                pass

        # Check if user should complete onboarding
        if not request.user.is_onboarded:
            return '/onboarding/'

        return super().get_signup_redirect_url(request)

    def add_message(self, request, level, message_tag, message, extra_tags=""):
        """
        Add messages with invitation context
        """
        # Check if this is related to invitation processing
        invitation_token = request.session.get('invitation_token')
        if invitation_token and 'invitation' in message_tag:
            try:
                invitation = Invitation.objects.get(token=invitation_token)
                message = f"{message} You've been invited to join {invitation.organization.name}."
            except Invitation.DoesNotExist:
                pass

        super().add_message(request, level, message_tag, message, extra_tags)

    def is_open_for_signup(self, request):
        """
        Check if signup is open, considering invitation-only mode
        """
        # Always allow signup if there's a valid invitation
        invitation_token = request.GET.get('invitation') or request.session.get('invitation_token')
        if invitation_token:
            try:
                invitation = Invitation.objects.get(
                    token=invitation_token,
                    status=Invitation.PENDING
                )
                if not invitation.is_expired:
                    # Store invitation token in session for later use
                    request.session['invitation_token'] = invitation_token
                    return True
            except Invitation.DoesNotExist:
                pass

        # Default behavior
        return super().is_open_for_signup(request)

    def clean_email(self, email):
        """
        Clean and validate email address
        """
        email = super().clean_email(email)

        # Additional validation can be added here
        # For example, domain whitelisting/blacklisting

        return email

    def populate_username(self, request, user):
        """
        Populate username from email
        """
        # Use email as username base
        email = user.email
        username_base = email.split('@')[0]

        # Ensure username is unique
        username = username_base
        counter = 1
        while User.objects.filter(username=username).exists():
            username = f"{username_base}{counter}"
            counter += 1

        user.username = username
        return user

    def pre_authenticate(self, request, **credentials):
        """
        Handle pre-authentication logic
        """
        # Store invitation context if present
        invitation_token = request.GET.get('invitation')
        if invitation_token:
            request.session['invitation_token'] = invitation_token

        return super().pre_authenticate(request, **credentials)

    def authentication_failed(self, request, **credentials):
        """
        Handle authentication failure
        """
        # Clear invitation token on auth failure
        if 'invitation_token' in request.session:
            del request.session['invitation_token']

        super().authentication_failed(request, **credentials)

    def get_from_email(self):
        """
        Get the from email for account-related emails
        """
        from django.conf import settings
        return getattr(settings, 'DEFAULT_FROM_EMAIL', 'noreply@example.com')

    def render_mail(self, template_prefix, email, context, headers=None):
        """
        Render email with additional invitation context
        """
        # Add invitation context to email templates
        invitation_token = context.get('invitation_token')
        if invitation_token:
            try:
                invitation = Invitation.objects.get(token=invitation_token)
                context.update({
                    'invitation': invitation,
                    'organization': invitation.organization,
                    'invited_by': invitation.invited_by,
                })
            except Invitation.DoesNotExist:
                pass

        return super().render_mail(template_prefix, email, context, headers)

    def get_email_confirmation_url(self, request, emailconfirmation):
        """
        Get email confirmation URL with invitation context
        """
        url = super().get_email_confirmation_url(request, emailconfirmation)

        # Add invitation token to confirmation URL if present
        invitation_token = request.session.get('invitation_token')
        if invitation_token:
            separator = '&' if '?' in url else '?'
            url = f"{url}{separator}invitation={invitation_token}"

        return url