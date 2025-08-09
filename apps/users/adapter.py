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

    # -----------------------------
    # Redirects
    # -----------------------------
    def _frontend(self) -> str:
        return getattr(settings, "FRONTEND_ADDRESS", "http://localhost:3000")

    def get_login_redirect_url(self, request):
        frontend = self._frontend()

        # Onboarding first-time users
        if getattr(request, "user", None) and not getattr(request.user, "is_onboarded", False):
            return f"{frontend}/onboarding"

        # Pending invitations
        if getattr(request, "user", None):
            from apps.teams.models import Invitation
            pending = (
                Invitation.objects.filter(
                    email=request.user.email,
                    status=Invitation.PENDING
                )
                .exclude(expires_at__lt=timezone.now())
            )
            if pending.exists():
                return f"{frontend}/invitations"

        return f"{frontend}/dashboard"

    def get_signup_redirect_url(self, request):
        return f"{self._frontend()}/onboarding"

    def get_logout_redirect_url(self, request):
        return f"{self._frontend()}/login"

    # NEW: allauth >= 0.64 expects this instead of get_email_confirmation_redirect_url
    def get_email_verification_redirect_url(self, email_address: EmailAddress):
        """
        Where to send the user AFTER the email has been verified.
        """
        # Allow overriding via settings.HEADLESS_FRONTEND_URLS
        url_map = getattr(settings, "HEADLESS_FRONTEND_URLS", {}) or {}
        # Prefer a specific post-verify route if you’ve defined one
        path = url_map.get("email_verified_redirect", "/email-confirmed")
        return f"{self._frontend()}{path}"

    # Back-compat shim for older allauth code paths (safe to keep; can remove later)
    def get_email_confirmation_redirect_url(self, request):
        return self.get_email_verification_redirect_url(
            EmailAddress(user=getattr(request, "user", None), email=getattr(getattr(request, "user", None), "email", ""))
        )

    # -----------------------------
    # URLs placed inside emails
    # -----------------------------
    def get_email_verification_url(self, request, emailconfirmation):
        """
        The actual URL sent in the verification email (contains the confirmation key).
        """
        return f"{self._frontend()}/verify-email/{emailconfirmation.key}"

    def get_password_reset_url(self, request, user, temp_key):
        return f"{self._frontend()}/reset-password/{temp_key}"

    # -----------------------------
    # Serialization & session hooks
    # -----------------------------
    def serialize_user(self, user):
        """
        Serialize user data for API responses. Safe with username-less models.
        """
        # Guard optional attributes so this works even if you dropped username/display_name/etc.
        get = getattr
        return {
            "id": user.id,
            "username": getattr(user, "username", None),
            "email": user.email,
            "first_name": get(user, "first_name", "") or "",
            "last_name": get(user, "last_name", "") or "",
            "display_name": getattr(user, "get_display_name", lambda: None)(),
            "avatar_url": getattr(user, "avatar_url", None),
            "is_verified": getattr(user, "is_verified", False),
            "is_onboarded": getattr(user, "is_onboarded", False),
            "timezone": getattr(user, "timezone", None),
            "language": getattr(user, "language", None),
            "date_joined": user.date_joined.isoformat(),
            "last_login": user.last_login.isoformat() if user.last_login else None,
        }

    def pre_authenticate(self, request, **credentials):
        email = credentials.get("email")
        if email:
            from apps.users.utils import log_user_activity
            try:
                user = User.objects.get(email=email)
                log_user_activity(
                    user=user,
                    action="login_attempt",
                    description="Login attempt via API",
                    ip_address=self.get_client_ip(request),
                )
            except User.DoesNotExist:
                pass

    def pre_login(self, request, user, **kwargs):
        client_ip = self.get_client_ip(request)
        if client_ip:
            # last_login is handled by auth; keep a separate IP if you track it
            if hasattr(user, "last_login_ip"):
                user.last_login_ip = client_ip
                user.save(update_fields=["last_login_ip"])
        self.check_security_alerts(request, user)

    def post_login(self, request, user, **kwargs):
        from apps.users.utils import log_user_activity
        log_user_activity(
            user=user,
            action="login",
            description="Login via API",
            ip_address=self.get_client_ip(request),
            user_agent=request.META.get("HTTP_USER_AGENT", ""),
        )
        self.process_pending_invitations(user)

    def post_logout(self, request, user, **kwargs):
        if user:
            from apps.users.utils import log_user_activity
            log_user_activity(
                user=user,
                action="logout",
                description="Logout via API",
                ip_address=self.get_client_ip(request),
            )

    # Let allauth perform its internal confirm, then add your side effects
    def confirm_email(self, request, email_address):
        # Call parent to keep allauth’s bookkeeping intact (idempotent)
        try:
            super().confirm_email(request, email_address)
        except AttributeError:
            # Older allauth versions may not implement parent; ignore gracefully
            pass

        user = email_address.user

        # Optional: mirror verification into your User model
        if hasattr(user, "is_verified") and not user.is_verified:
            user.is_verified = True
            user.save(update_fields=["is_verified"])

        from apps.users.utils import log_user_activity, create_user_notification
        log_user_activity(
            user=user,
            action="email_verified",
            description="Email verified via API",
            ip_address=self.get_client_ip(request),
        )
        create_user_notification(
            user=user,
            title="Email Verified",
            message="Your email address has been successfully verified.",
            notification_type="success",
        )

        self.process_pending_invitations(user)

    def save_user(self, request, user, form, commit=True):
        user = super().save_user(request, user, form, commit=commit)
        if commit:
            from apps.users.models import UserPreference
            from apps.users.utils import log_user_activity
            UserPreference.objects.get_or_create(user=user)
            log_user_activity(
                user=user,
                action="account_created",
                description="Account created via API",
                ip_address=self.get_client_ip(request),
            )
        return user

    # -----------------------------
    # Invitations & security checks
    # -----------------------------
    def process_pending_invitations(self, user):
        from apps.teams.models import Invitation

        pending = (
            Invitation.objects.filter(
                email=user.email,
                status=Invitation.PENDING,
            ).exclude(expires_at__lt=timezone.now())
        )

        for invitation in pending:
            try:
                if not getattr(invitation, "is_expired", False):
                    invitation.accept(user)
                    from apps.users.utils import create_user_notification
                    create_user_notification(
                        user=user,
                        title=f"Joined {invitation.organization.name}",
                        message=(
                            f"You've successfully joined {invitation.organization.name} "
                            f"as {invitation.role.get_name_display()}"
                        ),
                        notification_type="success",
                        organization=invitation.organization,
                    )
                else:
                    invitation.status = Invitation.EXPIRED
                    invitation.save(update_fields=["status"])
            except Exception as e:
                import logging
                logging.getLogger(__name__).error(
                    f"Failed to process invitation {invitation.id}: {e}"
                )

    def check_security_alerts(self, request, user):
        client_ip = self.get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        from apps.users.models import UserSession
        existing = UserSession.objects.filter(user=user, is_active=True)

        is_new_device = not existing.filter(
            user_agent__icontains=self.extract_browser_info(user_agent)
        ).exists()

        if is_new_device and existing.exists():
            from apps.users.utils import send_security_alert
            send_security_alert(
                user=user,
                alert_type="login_from_new_device",
                details={
                    "ip_address": client_ip,
                    "user_agent": user_agent,
                    "login_time": timezone.now(),
                    "device_info": self.extract_browser_info(user_agent),
                },
            )

    # -----------------------------
    # Helpers
    # -----------------------------
    def extract_browser_info(self, user_agent):
        ua = (user_agent or "").lower()
        if "chrome" in ua and "edge" not in ua:
            return "Chrome"
        if "firefox" in ua:
            return "Firefox"
        if "safari" in ua and "chrome" not in ua:
            return "Safari"
        if "edge" in ua:
            return "Edge"
        return "Unknown"

    def get_client_ip(self, request):
        xff = request.META.get("HTTP_X_FORWARDED_FOR")
        if xff:
            return xff.split(",")[0].strip()
        return request.META.get("REMOTE_ADDR", "0.0.0.0")

    def respond_user_session_changed(self, request, user):
        return {
            "user": self.serialize_user(user),
            "session_info": {
                "last_login": user.last_login.isoformat() if user.last_login else None,
                "last_login_ip": getattr(user, "last_login_ip", None),
                "is_onboarded": getattr(user, "is_onboarded", False),
                "needs_email_verification": not getattr(user, "is_verified", False),
            },
        }
