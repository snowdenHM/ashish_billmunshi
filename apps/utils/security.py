import hashlib
import hmac
import secrets
import time
from typing import Optional, Dict, Any
from django.conf import settings
from django.core.cache import cache
from django.utils import timezone


class SecurityManager:
    """Security utilities for the application"""

    @staticmethod
    def generate_secure_token(length: int = 32) -> str:
        """Generate a cryptographically secure token"""
        return secrets.token_urlsafe(length)

    @staticmethod
    def hash_sensitive_data(data: str, salt: Optional[str] = None) -> str:
        """Hash sensitive data with optional salt"""
        if salt is None:
            salt = secrets.token_hex(16)

        combined = f"{salt}{data}".encode('utf-8')
        hashed = hashlib.sha256(combined).hexdigest()
        return f"{salt}:{hashed}"

    @staticmethod
    def verify_hashed_data(data: str, hashed: str) -> bool:
        """Verify hashed data"""
        try:
            salt, expected_hash = hashed.split(':', 1)
            combined = f"{salt}{data}".encode('utf-8')
            actual_hash = hashlib.sha256(combined).hexdigest()
            return hmac.compare_digest(expected_hash, actual_hash)
        except ValueError:
            return False

    @staticmethod
    def verify_webhook_signature(payload: str, signature: str, secret: str) -> bool:
        """Verify webhook signature"""
        expected_signature = hmac.new(
            secret.encode('utf-8'),
            payload.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()

        return hmac.compare_digest(f"sha256={expected_signature}", signature)

    @staticmethod
    def rate_limit_check(key: str, limit: int, window: int = 3600) -> Dict[str, Any]:
        """Check rate limit for a given key"""
        cache_key = f"rate_limit:{key}"
        now = int(time.time())
        window_start = now - window

        # Get current requests in window
        requests = cache.get(cache_key, [])

        # Filter requests within current window
        current_requests = [req_time for req_time in requests if req_time > window_start]

        # Check if limit exceeded
        if len(current_requests) >= limit:
            return {
                'allowed': False,
                'limit': limit,
                'remaining': 0,
                'reset_time': min(current_requests) + window
            }

        # Add current request
        current_requests.append(now)
        cache.set(cache_key, current_requests, window)

        return {
            'allowed': True,
            'limit': limit,
            'remaining': limit - len(current_requests),
            'reset_time': now + window
        }