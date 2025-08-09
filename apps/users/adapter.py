from allauth.headless.adapter import DefaultHeadlessAdapter
from allauth.account.models import EmailAddress
from django.contrib.auth import get_user_model
from django.utils import timezone
from django.conf import settings

User = get_user_model()


class CustomHeadlessAdapter(DefaultHeadlessAdapter):
    """
    Custom headless adapter for API-based authentication flows
    """

    def get_login_redirect_url(self, request):
        """
        Get login redirect URL for headless mode
        """
        # For headless mode, return API endpoint or frontend URL
        frontend_url = getattr(settings, 'FRONTEND_ADDRESS', 'http://localhost:3000')

        # Check if user needs onboarding
        if request.user and not request.user.is_onboarded:
            return f"{frontend_url}/onboarding"

        # Check for pending invitations
        if request.user:
            from apps.teams.models import Invitation
            pending_invitations = Invitation.objects.filter(
                email=request.user.email,
                status=Invitation.PENDING
            ).exclude(expires_at__lt=timezone.now())

            if pending_invitations.exists():
                return f"{frontend_url}/invitations"

        return f"{frontend_url}/dashboard"

    def get_signup_redirect_url(self, request):
        """
        Get signup redirect URL for headless mode
        """
        frontend_url = getattr(settings, 'FRONTEND_ADDRESS', 'http://localhost:3000')

        # Always redirect to onboarding after signup
        return f"{frontend_url}/onboarding"

    def get_logout_redirect_url(self, request):
        """
        Get logout redirect URL for headless mode
        """
        frontend_url = getattr(settings, 'FRONTEND_ADDRESS', 'http://localhost:3000')
        return f"{frontend_url}/login"

    def get_email_confirmation_redirect_url(self, request):
        """
        Get email confirmation redirect URL for headless mode
        """
        frontend_url = getattr(settings, 'FRONTEND_ADDRESS', 'http://localhost:3000')
        return f"{frontend_url}/email-confirmed"

    def serialize_user(self, user):
        """
        Serialize user data for API responses
        """
        return {
            'id': user.id,
            'username': user.username,
            'email': user.email,
            'first_name': user.first_name,
            'last_name': user.last_name,
            'display_name': user.get_display_name(),
            'avatar_url': user.avatar_url,
            'is_verified': user.is_verified,
            'is_onboarded': user.is_onboarded,
            'timezone': user.timezone,
            'language': user.language,
            'date_joined': user.date_joined.isoformat(),
            'last_login': user.last_login.isoformat() if user.last_login else None,
        }

    def pre_authenticate(self, request, **credentials):
        """
        Handle pre-authentication logic for headless mode
        """
        # Log authentication attempt
        email = credentials.get('email')
        if email:
            from apps.users.utils import log_user_activity
            try:
                user = User.objects.get(email=email)
                log_user_activity(
                    user=user,
                    action='login_attempt',
                    description='Login attempt via API',
                    ip_address=self.get_client_ip(request)
                )
            except User.DoesNotExist:
                pass

    def pre_login(self, request, user, **kwargs):
        """
        Handle pre-login logic for headless mode
        """
        # Update last login IP
        client_ip = self.get_client_ip(request)
        if client_ip:
            user.last_login_ip = client_ip
            user.save(update_fields=['last_login_ip'])

        # Check for security alerts
        self.check_security_alerts(request, user)

    def post_login(self, request, user, **kwargs):
        """
        Handle post-login logic for headless mode
        """
        # Update user activity
        from apps.users.utils import log_user_activity
        log_user_activity(
            user=user,
            action='login',
            description='Login via API',
            ip_address=self.get_client_ip(request),
            user_agent=request.META.get('HTTP_USER_AGENT', '')
        )

        # Process pending invitations
        self.process_pending_invitations(user)

    def post_logout(self, request, user, **kwargs):
        """
        Handle post-logout logic for headless mode
        """
        if user:
            from apps.users.utils import log_user_activity
            log_user_activity(
                user=user,
                action='logout',
                description='Logout via API',
                ip_address=self.get_client_ip(request)
            )

    def confirm_email(self, request, email_address):
        """
        Handle email confirmation in headless mode
        """
        user = email_address.user

        # Mark user as verified
        if not user.is_verified:
            user.is_verified = True
            user.save(update_fields=['is_verified'])

        # Log email verification
        from apps.users.utils import log_user_activity
        log_user_activity(
            user=user,
            action='email_verified',
            description='Email verified via API',
            ip_address=self.get_client_ip(request)
        )

        # Create notification
        from apps.users.utils import create_user_notification
        create_user_notification(
            user=user,
            title="Email Verified",
            message="Your email address has been successfully verified.",
            notification_type='success'
        )

        # Process pending invitations
        self.process_pending_invitations(user)

    def save_user(self, request, user, form, commit=True):
        """
        Save user with additional processing for headless mode
        """
        user = super().save_user(request, user, form, commit=commit)

        if commit:
            # Create user preferences
            from apps.users.models import UserPreference
            UserPreference.objects.get_or_create(user=user)

            # Log user creation
            from apps.users.utils import log_user_activity
            log_user_activity(
                user=user,
                action='account_created',
                description='Account created via API',
                ip_address=self.get_client_ip(request)
            )

        return user

    def process_pending_invitations(self, user):
        """
        Process pending invitations for the user
        """
        from apps.teams.models import Invitation

        pending_invitations = Invitation.objects.filter(
            email=user.email,
            status=Invitation.PENDING
        ).exclude(expires_at__lt=timezone.now())

        for invitation in pending_invitations:
            try:
                if not invitation.is_expired:
                    invitation.accept(user)

                    # Create notification
                    from apps.users.utils import create_user_notification
                    create_user_notification(
                        user=user,
                        title=f"Joined {invitation.organization.name}",
                        message=f"You've successfully joined {invitation.organization.name} as {invitation.role.get_name_display()}",
                        notification_type='success',
                        organization=invitation.organization
                    )
                else:
                    invitation.status = Invitation.EXPIRED
                    invitation.save()

            except Exception as e:
                import logging
                logger = logging.getLogger(__name__)
                logger.error(f"Failed to process invitation {invitation.id}: {str(e)}")

    def check_security_alerts(self, request, user):
        """
        Check for security alerts during login
        """
        client_ip = self.get_client_ip(request)
        user_agent = request.META.get('HTTP_USER_AGENT', '')

        # Check for new device login
        from apps.users.models import UserSession
        existing_sessions = UserSession.objects.filter(
            user=user,
            is_active=True
        )

        # Simple check for new device (based on user agent)
        is_new_device = not existing_sessions.filter(
            user_agent__icontains=self.extract_browser_info(user_agent)
        ).exists()

        if is_new_device and existing_sessions.exists():
            # Send security alert
            from apps.users.utils import send_security_alert
            send_security_alert(
                user=user,
                alert_type='login_from_new_device',
                details={
                    'ip_address': client_ip,
                    'user_agent': user_agent,
                    'login_time': timezone.now(),
                    'device_info': self.extract_browser_info(user_agent)
                }
            )

    def extract_browser_info(self, user_agent):
        """
        Extract browser information from user agent
        """
        user_agent = user_agent.lower()

        if 'chrome' in user_agent:
            return 'Chrome'
        elif 'firefox' in user_agent:
            return 'Firefox'
        elif 'safari' in user_agent:
            return 'Safari'
        elif 'edge' in user_agent:
            return 'Edge'
        else:
            return 'Unknown'

    def get_client_ip(self, request):
        """
        Get client IP address from request
        """
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded_for:
            return x_forwarded_for.split(',')[0].strip()
        return request.META.get('REMOTE_ADDR', '0.0.0.0')

    def respond_user_session_changed(self, request, user):
        """
        Respond to user session changes in headless mode
        """
        return {
            'user': self.serialize_user(user),
            'session_info': {
                'last_login': user.last_login.isoformat() if user.last_login else None,
                'last_login_ip': user.last_login_ip,
                'is_onboarded': user.is_onboarded,
                'needs_email_verification': not user.is_verified
            }
        }

    def get_email_verification_url(self, request, emailconfirmation):
        """
        Get email verification URL for headless mode
        """
        frontend_url = getattr(settings, 'FRONTEND_ADDRESS', 'http://localhost:3000')
        return f"{frontend_url}/verify-email/{emailconfirmation.key}"

    def get_password_reset_url(self, request, user, temp_key):
        """
        Get password reset URL for headless mode
        """
        frontend_url = getattr(settings, 'FRONTEND_ADDRESS', 'http://localhost:3000')
        return f"{frontend_url}/reset-password/{temp_key}"