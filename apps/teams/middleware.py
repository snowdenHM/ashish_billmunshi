import time
from django.utils.deprecation import MiddlewareMixin
from django.http import JsonResponse
from django.utils import timezone
from .models import OrganizationAPIKey, APIKeyUsageLog


class APIKeyRateLimitMiddleware(MiddlewareMixin):
    """
    Middleware to handle API key rate limiting and usage logging
    """

    def process_request(self, request):
        """
        Check rate limits for API key requests
        """
        # Skip if not an API request with API key
        if not hasattr(request, 'api_key'):
            return None

        api_key = request.api_key

        # Check hourly rate limit
        if not self.check_rate_limit(api_key, 'hour'):
            return JsonResponse({
                'error': 'Hourly rate limit exceeded',
                'limit': api_key.rate_limit_per_hour,
                'window': 'hour'
            }, status=429)

        # Check daily rate limit
        if not self.check_rate_limit(api_key, 'day'):
            return JsonResponse({
                'error': 'Daily rate limit exceeded',
                'limit': api_key.rate_limit_per_day,
                'window': 'day'
            }, status=429)

        # Store start time for response time calculation
        request._start_time = time.time()

        return None

    def process_response(self, request, response):
        """
        Log API usage after request completion
        """
        # Skip if not an API request with API key
        if not hasattr(request, 'api_key'):
            return response

        try:
            api_key = request.api_key

            # Calculate response time
            response_time_ms = 0
            if hasattr(request, '_start_time'):
                response_time_ms = int((time.time() - request._start_time) * 1000)

            # Update existing log entry or create new one
            self.update_usage_log(request, api_key, response.status_code, response_time_ms)

        except Exception:
            # Don't fail the response if logging fails
            pass

        return response

    def check_rate_limit(self, api_key, window):
        """
        Check if API key has exceeded rate limits
        """
        now = timezone.now()

        if window == 'hour':
            start_time = now - timezone.timedelta(hours=1)
            limit = api_key.rate_limit_per_hour
        else:  # day
            start_time = now - timezone.timedelta(days=1)
            limit = api_key.rate_limit_per_day

        # Count requests in the time window
        request_count = APIKeyUsageLog.objects.filter(
            api_key=api_key,
            created_at__gte=start_time
        ).count()

        return request_count < limit

    def update_usage_log(self, request, api_key, status_code, response_time_ms):
        """
        Update or create usage log entry
        """
        # Get client IP
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded_for:
            ip_address = x_forwarded_for.split(',')[0]
        else:
            ip_address = request.META.get('REMOTE_ADDR', '0.0.0.0')

        # Create usage log
        APIKeyUsageLog.objects.create(
            api_key=api_key,
            ip_address=ip_address,
            user_agent=request.META.get('HTTP_USER_AGENT', '')[:500],
            endpoint=request.path,
            method=request.method,
            status_code=status_code,
            response_time_ms=response_time_ms
        )


class OrganizationContextMiddleware(MiddlewareMixin):
    """
    Middleware to add organization context to requests
    """

    def process_request(self, request):
        """
        Add organization context to the request
        """
        # Initialize organization context
        request.organization = None
        request.user_role = None

        # If user is authenticated, we can set organization context in views
        # This middleware just ensures the attributes exist

        return None

    def process_view(self, request, view_func, view_args, view_kwargs):
        """
        Set organization context based on URL parameters
        """
        # Get organization ID from URL kwargs
        org_id = view_kwargs.get('organization_id') or view_kwargs.get('pk')

        if org_id and request.user.is_authenticated:
            try:
                from .models import Organization
                organization = Organization.objects.get(id=org_id)

                # Check if user is a member
                if organization.has_member(request.user):
                    request.organization = organization
                    request.user_role = organization.get_user_role(request.user)

            except Organization.DoesNotExist:
                pass

        return None


class APIKeyAuthenticationMiddleware(MiddlewareMixin):
    """
    Middleware to authenticate requests using organization API keys
    """

    def process_request(self, request):
        """
        Authenticate request using API key if provided
        """
        # Check for API key in Authorization header
        auth_header = request.META.get('HTTP_AUTHORIZATION', '')

        if auth_header.startswith('Bearer '):
            api_key = auth_header[7:]  # Remove 'Bearer ' prefix

            try:
                api_key_obj = OrganizationAPIKey.objects.select_related('organization').get(
                    key=api_key,
                    is_active=True
                )

                # Check if API key is expired
                if api_key_obj.is_expired:
                    return JsonResponse({
                        'error': 'API key has expired'
                    }, status=401)

                # Check IP restrictions
                client_ip = self.get_client_ip(request)
                if not api_key_obj.is_ip_allowed(client_ip):
                    return JsonResponse({
                        'error': 'IP address not allowed'
                    }, status=403)

                # Set API key and organization context
                request.api_key = api_key_obj
                request.organization = api_key_obj.organization

                # Increment usage count (async in production)
                api_key_obj.increment_usage()

            except OrganizationAPIKey.DoesNotExist:
                return JsonResponse({
                    'error': 'Invalid API key'
                }, status=401)

        return None

    def get_client_ip(self, request):
        """Get client IP address from request"""
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded_for:
            ip = x_forwarded_for.split(',')[0]
        else:
            ip = request.META.get('REMOTE_ADDR', '0.0.0.0')
        return ip