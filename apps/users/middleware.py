import time
from django.utils.deprecation import MiddlewareMixin
from django.utils import timezone
from django.contrib.auth import get_user_model
from django.contrib.sessions.models import Session

from .models import UserSession, UserActivity

User = get_user_model()


class UserActivityTrackingMiddleware(MiddlewareMixin):
    """
    Middleware to track user activity and update last seen timestamp
    """

    def process_request(self, request):
        """
        Track user activity on each request
        """
        if request.user.is_authenticated:
            # Update user's last activity
            ip_address = self.get_client_ip(request)
            request.user.update_last_activity(ip_address)

            # Update session last activity if session exists
            if hasattr(request, 'session') and request.session.session_key:
                try:
                    user_session = UserSession.objects.get(
                        session_key=request.session.session_key,
                        user=request.user
                    )
                    user_session.last_activity = timezone.now()
                    user_session.save(update_fields=['last_activity'])
                except UserSession.DoesNotExist:
                    # Create session if it doesn't exist
                    self.create_user_session(request)

        return None

    def create_user_session(self, request):
        """
        Create user session record
        """
        if not request.user.is_authenticated or not hasattr(request, 'session'):
            return

        session_key = request.session.session_key
        if not session_key:
            return

        # Calculate session expiry
        expires_at = timezone.now() + timezone.timedelta(
            seconds=request.session.get_expiry_age()
        )

        ip_address = self.get_client_ip(request)
        user_agent = request.META.get('HTTP_USER_AGENT', '')

        UserSession.objects.get_or_create(
            session_key=session_key,
            defaults={
                'user': request.user,
                'ip_address': ip_address,
                'user_agent': user_agent,
                'is_active': True,
                'expires_at': expires_at,
            }
        )

    def get_client_ip(self, request):
        """Get client IP address"""
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded_for:
            ip = x_forwarded_for.split(',')[0]
        else:
            ip = request.META.get('REMOTE_ADDR', '0.0.0.0')
        return ip


class SessionCleanupMiddleware(MiddlewareMixin):
    """
    Middleware to clean up expired sessions periodically
    """

    def process_request(self, request):
        """
        Occasionally clean up expired sessions
        """
        import random

        # Only run cleanup 1% of the time to avoid performance issues
        if random.randint(1, 100) == 1:
            self.cleanup_expired_sessions()

        return None

    def cleanup_expired_sessions(self):
        """
        Clean up expired user sessions and Django sessions
        """
        try:
            # Mark expired UserSessions as inactive
            expired_user_sessions = UserSession.objects.filter(
                expires_at__lt=timezone.now(),
                is_active=True
            )
            expired_user_sessions.update(is_active=False)

            # Delete expired Django sessions
            expired_django_sessions = Session.objects.filter(
                expire_date__lt=timezone.now()
            )
            expired_django_sessions.delete()

        except Exception:
            # Don't fail requests if cleanup fails
            pass


class UserPreferenceMiddleware(MiddlewareMixin):
    """
    Middleware to add user preferences to request context
    """

    def process_request(self, request):
        """
        Add user preferences to request
        """
        if request.user.is_authenticated:
            # Get or create user preferences
            from .models import UserPreference

            try:
                preferences = request.user.preferences
            except UserPreference.DoesNotExist:
                preferences = UserPreference.objects.create(user=request.user)

            request.user_preferences = preferences

        return None


class APIUsageTrackingMiddleware(MiddlewareMixin):
    """
    Middleware to track API usage for authenticated users
    """

    def process_request(self, request):
        """
        Track API requests for authenticated users
        """
        # Only track API requests (adjust path pattern as needed)
        if not request.path.startswith('/api/'):
            return None

        if request.user.is_authenticated:
            # Store request start time for response time calculation
            request._api_start_time = time.time()
            request._track_api_usage = True

        return None

    def process_response(self, request, response):
        """
        Log API usage after request completion
        """
        if not getattr(request, '_track_api_usage', False):
            return response

        if not request.user.is_authenticated:
            return response

        try:
            # Calculate response time
            response_time_ms = 0
            if hasattr(request, '_api_start_time'):
                response_time_ms = int((time.time() - request._api_start_time) * 1000)

            # Log significant API activities
            self.log_api_activity(request, response, response_time_ms)

        except Exception:
            # Don't fail the response if logging fails
            pass

        return response

    def log_api_activity(self, request, response, response_time_ms):
        """
        Log significant API activities
        """
        # Only log certain activities to avoid spam
        significant_endpoints = [
            '/api/teams/organizations/',
            '/api/teams/api-keys/',
            '/api/users/profile/',
        ]

        # Only log POST, PUT, DELETE requests or significant GET requests
        if (request.method in ['POST', 'PUT', 'DELETE'] or
                any(endpoint in request.path for endpoint in significant_endpoints)):
            action_map = {
                'POST': 'api_create',
                'PUT': 'api_update',
                'DELETE': 'api_delete',
                'GET': 'api_access'
            }

            action = action_map.get(request.method, 'api_request')

            # Get organization context if available
            organization = getattr(request, 'organization', None)

            UserActivity.objects.create(
                user=request.user,
                action=action,
                description=f"API {request.method} {request.path}",
                ip_address=self.get_client_ip(request),
                user_agent=request.META.get('HTTP_USER_AGENT', '')[:500],
                organization=organization,
                metadata={
                    'endpoint': request.path,
                    'method': request.method,
                    'status_code': response.status_code,
                    'response_time_ms': response_time_ms
                }
            )

    def get_client_ip(self, request):
        """Get client IP address"""
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded_for:
            ip = x_forwarded_for.split(',')[0]
        else:
            ip = request.META.get('REMOTE_ADDR', '0.0.0.0')
        return ip


class SecurityHeadersMiddleware(MiddlewareMixin):
    """
    Middleware to add security headers for user protection
    """

    def process_response(self, request, response):
        """
        Add security headers to response
        """
        # Only add headers for authenticated users on sensitive pages
        if request.user.is_authenticated:
            # Add Content Security Policy
            response['Content-Security-Policy'] = (
                "default-src 'self'; "
                "script-src 'self' 'unsafe-inline' 'unsafe-eval'; "
                "style-src 'self' 'unsafe-inline'; "
                "img-src 'self' data: https:; "
                "connect-src 'self';"
            )

            # Add other security headers
            response['X-Content-Type-Options'] = 'nosniff'
            response['X-Frame-Options'] = 'DENY'
            response['Referrer-Policy'] = 'strict-origin-when-cross-origin'

            # Add HSTS for HTTPS
            if request.is_secure():
                response['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'

        return response


class OnboardingRedirectMiddleware(MiddlewareMixin):
    """
    Middleware to redirect users to onboarding if not completed
    """

    def process_request(self, request):
        """
        Redirect to onboarding if user is authenticated but not onboarded
        """
        # Skip for certain paths
        skip_paths = [
            '/api/',
            '/admin/',
            '/users/complete-onboarding/',
            '/accounts/logout/',
            '/static/',
            '/media/',
        ]

        if any(request.path.startswith(path) for path in skip_paths):
            return None

        if (request.user.is_authenticated and
                not request.user.is_onboarded and
                not request.path.startswith('/onboarding/')):

            # For API requests, return JSON response
            if request.path.startswith('/api/'):
                from django.http import JsonResponse
                return JsonResponse({
                    'error': 'Onboarding required',
                    'message': 'Please complete your profile setup',
                    'onboarding_url': '/api/users/profile/complete-onboarding/'
                }, status=400)

            # For web requests, redirect to onboarding
            # (You would implement this based on your frontend routing)

        return None