import time
import json
from django.utils.deprecation import MiddlewareMixin
from django.conf import settings
from django.http import JsonResponse
from django.core.cache import cache
from django.utils import timezone
from datetime import timedelta
import hashlib


class EnhancedRateLimitMiddleware(MiddlewareMixin):
    """
    Enhanced rate limiting middleware with multiple strategies
    """

    def __init__(self, get_response):
        self.get_response = get_response
        super().__init__(get_response)

    def process_request(self, request):
        """
        Apply rate limiting before processing request
        """
        # Skip rate limiting for certain paths
        skip_paths = [
            '/admin/',
            '/api/schema/',
            '/health/',
            '/static/',
            '/media/',
        ]

        if any(request.path.startswith(path) for path in skip_paths):
            return None

        # Apply different rate limiting strategies
        if hasattr(request, 'api_key'):
            return self.check_api_key_rate_limit(request)
        elif request.user.is_authenticated:
            return self.check_user_rate_limit(request)
        else:
            return self.check_anonymous_rate_limit(request)

    def check_api_key_rate_limit(self, request):
        """
        Check rate limits for API key requests
        """
        api_key = request.api_key

        # Check hourly limit
        hourly_key = f"api_key_hourly:{api_key.id}:{timezone.now().strftime('%Y%m%d%H')}"
        hourly_count = cache.get(hourly_key, 0)

        if hourly_count >= api_key.rate_limit_per_hour:
            return self.rate_limit_response(
                limit=api_key.rate_limit_per_hour,
                window='hour',
                retry_after=3600 - (int(time.time()) % 3600)
            )

        # Check daily limit
        daily_key = f"api_key_daily:{api_key.id}:{timezone.now().strftime('%Y%m%d')}"
        daily_count = cache.get(daily_key, 0)

        if daily_count >= api_key.rate_limit_per_day:
            return self.rate_limit_response(
                limit=api_key.rate_limit_per_day,
                window='day',
                retry_after=86400 - (int(time.time()) % 86400)
            )

        # Increment counters
        cache.set(hourly_key, hourly_count + 1, 3600)
        cache.set(daily_key, daily_count + 1, 86400)

        # Add rate limit headers
        request._rate_limit_headers = {
            'X-RateLimit-Limit-Hour': str(api_key.rate_limit_per_hour),
            'X-RateLimit-Remaining-Hour': str(max(0, api_key.rate_limit_per_hour - hourly_count - 1)),
            'X-RateLimit-Limit-Day': str(api_key.rate_limit_per_day),
            'X-RateLimit-Remaining-Day': str(max(0, api_key.rate_limit_per_day - daily_count - 1)),
        }

        return None

    def check_user_rate_limit(self, request):
        """
        Check rate limits for authenticated users
        """
        user = request.user

        # Different limits based on user's subscription
        if hasattr(user, 'organization_memberships'):
            # Get user's highest plan limits
            max_hourly_limit = 100  # Default
            max_daily_limit = 1000  # Default

            for membership in user.organization_memberships.filter(is_active=True):
                org = membership.organization
                if hasattr(org, 'subscription') and org.subscription.is_active:
                    plan = org.subscription.plan
                    # Use plan's API limits as user limits
                    if plan.api_rate_limit_boost:
                        max_hourly_limit = max(max_hourly_limit, 500)
                        max_daily_limit = max(max_daily_limit, 5000)
                    else:
                        max_hourly_limit = max(max_hourly_limit, 200)
                        max_daily_limit = max(max_daily_limit, 2000)

        # Check hourly limit for user
        hourly_key = f"user_hourly:{user.id}:{timezone.now().strftime('%Y%m%d%H')}"
        hourly_count = cache.get(hourly_key, 0)

        if hourly_count >= max_hourly_limit:
            return self.rate_limit_response(
                limit=max_hourly_limit,
                window='hour',
                retry_after=3600 - (int(time.time()) % 3600)
            )

        # Check daily limit for user
        daily_key = f"user_daily:{user.id}:{timezone.now().strftime('%Y%m%d')}"
        daily_count = cache.get(daily_key, 0)

        if daily_count >= max_daily_limit:
            return self.rate_limit_response(
                limit=max_daily_limit,
                window='day',
                retry_after=86400 - (int(time.time()) % 86400)
            )

        # Increment counters
        cache.set(hourly_key, hourly_count + 1, 3600)
        cache.set(daily_key, daily_count + 1, 86400)

        # Add rate limit headers
        request._rate_limit_headers = {
            'X-RateLimit-Limit-Hour': str(max_hourly_limit),
            'X-RateLimit-Remaining-Hour': str(max(0, max_hourly_limit - hourly_count - 1)),
            'X-RateLimit-Limit-Day': str(max_daily_limit),
            'X-RateLimit-Remaining-Day': str(max(0, max_daily_limit - daily_count - 1)),
        }

        return None

    def check_anonymous_rate_limit(self, request):
        """
        Check rate limits for anonymous users (by IP)
        """
        client_ip = self.get_client_ip(request)
        ip_hash = hashlib.md5(client_ip.encode()).hexdigest()

        # Stricter limits for anonymous users
        hourly_limit = 50
        daily_limit = 200

        # Check hourly limit
        hourly_key = f"ip_hourly:{ip_hash}:{timezone.now().strftime('%Y%m%d%H')}"
        hourly_count = cache.get(hourly_key, 0)

        if hourly_count >= hourly_limit:
            return self.rate_limit_response(
                limit=hourly_limit,
                window='hour',
                retry_after=3600 - (int(time.time()) % 3600)
            )

        # Check daily limit
        daily_key = f"ip_daily:{ip_hash}:{timezone.now().strftime('%Y%m%d')}"
        daily_count = cache.get(daily_key, 0)

        if daily_count >= daily_limit:
            return self.rate_limit_response(
                limit=daily_limit,
                window='day',
                retry_after=86400 - (int(time.time()) % 86400)
            )

        # Increment counters
        cache.set(hourly_key, hourly_count + 1, 3600)
        cache.set(daily_key, daily_count + 1, 86400)

        # Add rate limit headers
        request._rate_limit_headers = {
            'X-RateLimit-Limit-Hour': str(hourly_limit),
            'X-RateLimit-Remaining-Hour': str(max(0, hourly_limit - hourly_count - 1)),
            'X-RateLimit-Limit-Day': str(daily_limit),
            'X-RateLimit-Remaining-Day': str(max(0, daily_limit - daily_count - 1)),
        }

        return None

    def process_response(self, request, response):
        """
        Add rate limit headers to response
        """
        if hasattr(request, '_rate_limit_headers'):
            for header, value in request._rate_limit_headers.items():
                response[header] = value

        return response

    def rate_limit_response(self, limit, window, retry_after):
        """
        Return rate limit exceeded response
        """
        return JsonResponse({
            'error': 'Rate limit exceeded',
            'message': f'Rate limit of {limit} requests per {window} exceeded',
            'limit': limit,
            'window': window,
            'retry_after': retry_after
        }, status=429)

    def get_client_ip(self, request):
        """
        Get client IP address
        """
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded_for:
            return x_forwarded_for.split(',')[0].strip()
        return request.META.get('REMOTE_ADDR', '0.0.0.0')


class BurstRateLimitMiddleware(MiddlewareMixin):
    """
    Additional middleware for burst protection (short-term high-frequency requests)
    """

    def process_request(self, request):
        """
        Check for burst patterns
        """
        # Skip for non-API requests
        if not request.path.startswith('/api/'):
            return None

        # Get identifier (API key, user, or IP)
        if hasattr(request, 'api_key'):
            identifier = f"api_key:{request.api_key.id}"
            burst_limit = 20  # 20 requests per minute for API keys
        elif request.user.is_authenticated:
            identifier = f"user:{request.user.id}"
            burst_limit = 10  # 10 requests per minute for users
        else:
            client_ip = self.get_client_ip(request)
            identifier = f"ip:{hashlib.md5(client_ip.encode()).hexdigest()}"
            burst_limit = 5  # 5 requests per minute for anonymous

        # Check burst limit (1-minute window)
        minute_key = f"burst:{identifier}:{timezone.now().strftime('%Y%m%d%H%M')}"
        minute_count = cache.get(minute_key, 0)

        if minute_count >= burst_limit:
            return JsonResponse({
                'error': 'Burst rate limit exceeded',
                'message': f'Too many requests in a short time. Limit: {burst_limit} per minute',
                'retry_after': 60
            }, status=429)

        # Increment counter
        cache.set(minute_key, minute_count + 1, 60)

        return None

    def get_client_ip(self, request):
        """
        Get client IP address
        """
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded_for:
            return x_forwarded_for.split(',')[0].strip()
        return request.META.get('REMOTE_ADDR', '0.0.0.0')


class AdaptiveRateLimitMiddleware(MiddlewareMixin):
    """
    Adaptive rate limiting that adjusts based on system load
    """

    def process_request(self, request):
        """
        Apply adaptive rate limiting
        """
        if not request.path.startswith('/api/'):
            return None

        # Get system load factor (simplified)
        load_factor = self.get_system_load()

        # Adjust rate limits based on load
        if load_factor > 0.8:  # High load
            rate_multiplier = 0.5
        elif load_factor > 0.6:  # Medium load
            rate_multiplier = 0.7
        else:  # Normal load
            rate_multiplier = 1.0

        # Store load factor for other middleware to use
        request._system_load = load_factor
        request._rate_multiplier = rate_multiplier

        return None

    def get_system_load(self):
        """
        Get current system load (simplified implementation)
        """
        # In production, this could check:
        # - CPU usage
        # - Memory usage
        # - Database connection pool
        # - Redis latency
        # - Active request count

        # For now, return a mock value
        # You could integrate with monitoring tools like New Relic, DataDog, etc.
        return cache.get('system_load', 0.3)


class GeographicRateLimitMiddleware(MiddlewareMixin):
    """
    Rate limiting based on geographic location
    """

    def __init__(self, get_response):
        self.get_response = get_response
        # In production, you might want to integrate with a GeoIP service
        self.high_risk_countries = ['CN', 'RU', 'IR', 'KP']  # Example
        super().__init__(get_response)

    def process_request(self, request):
        """
        Apply geographic-based rate limiting
        """
        if not request.path.startswith('/api/'):
            return None

        client_ip = self.get_client_ip(request)
        country_code = self.get_country_code(client_ip)

        if country_code in self.high_risk_countries:
            # Apply stricter rate limits for high-risk locations
            daily_key = f"geo_limit:{country_code}:{timezone.now().strftime('%Y%m%d')}"
            daily_count = cache.get(daily_key, 0)

            # Reduced limit for high-risk countries
            daily_limit = 100

            if daily_count >= daily_limit:
                return JsonResponse({
                    'error': 'Geographic rate limit exceeded',
                    'message': 'Daily request limit exceeded for your region',
                    'retry_after': 86400 - (int(time.time()) % 86400)
                }, status=429)

            cache.set(daily_key, daily_count + 1, 86400)

        return None

    def get_client_ip(self, request):
        """
        Get client IP address
        """
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded_for:
            return x_forwarded_for.split(',')[0].strip()
        return request.META.get('REMOTE_ADDR', '0.0.0.0')

    def get_country_code(self, ip_address):
        """
        Get country code from IP address
        """
        # In production, integrate with a GeoIP service like:
        # - MaxMind GeoIP2
        # - IPStack
        # - ip-api.com

        # For now, return a mock country code
        return cache.get(f'geoip:{ip_address}', 'US')


class RateLimitByEndpointMiddleware(MiddlewareMixin):
    """
    Rate limiting with different limits per endpoint
    """

    def __init__(self, get_response):
        self.get_response = get_response

        # Define endpoint-specific rate limits
        self.endpoint_limits = {
            '/api/teams/organizations/': {'hourly': 100, 'daily': 500},
            '/api/subscriptions/': {'hourly': 50, 'daily': 200},
            '/api/users/profile/': {'hourly': 200, 'daily': 1000},
            '/api/teams/api-keys/': {'hourly': 20, 'daily': 100},
        }

        super().__init__(get_response)

    def process_request(self, request):
        """
        Apply endpoint-specific rate limiting
        """
        # Find matching endpoint pattern
        endpoint_limits = None
        for pattern, limits in self.endpoint_limits.items():
            if request.path.startswith(pattern):
                endpoint_limits = limits
                break

        if not endpoint_limits:
            return None

        # Get identifier
        if hasattr(request, 'api_key'):
            identifier = f"api_key:{request.api_key.id}"
        elif request.user.is_authenticated:
            identifier = f"user:{request.user.id}"
        else:
            client_ip = self.get_client_ip(request)
            identifier = f"ip:{hashlib.md5(client_ip.encode()).hexdigest()}"

        # Check limits for this specific endpoint
        endpoint_hash = hashlib.md5(request.path.encode()).hexdigest()[:8]

        # Hourly check
        hourly_key = f"endpoint_hourly:{identifier}:{endpoint_hash}:{timezone.now().strftime('%Y%m%d%H')}"
        hourly_count = cache.get(hourly_key, 0)

        if hourly_count >= endpoint_limits['hourly']:
            return JsonResponse({
                'error': 'Endpoint rate limit exceeded',
                'message': f'Hourly limit of {endpoint_limits["hourly"]} exceeded for this endpoint',
                'retry_after': 3600 - (int(time.time()) % 3600)
            }, status=429)

        # Daily check
        daily_key = f"endpoint_daily:{identifier}:{endpoint_hash}:{timezone.now().strftime('%Y%m%d')}"
        daily_count = cache.get(daily_key, 0)

        if daily_count >= endpoint_limits['daily']:
            return JsonResponse({
                'error': 'Endpoint rate limit exceeded',
                'message': f'Daily limit of {endpoint_limits["daily"]} exceeded for this endpoint',
                'retry_after': 86400 - (int(time.time()) % 86400)
            }, status=429)

        # Increment counters
        cache.set(hourly_key, hourly_count + 1, 3600)
        cache.set(daily_key, daily_count + 1, 86400)

        return None

    def get_client_ip(self, request):
        """
        Get client IP address
        """
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded_for:
            return x_forwarded_for.split(',')[0].strip()
        return request.META.get('REMOTE_ADDR', '0.0.0.0')
