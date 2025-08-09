from django.http import JsonResponse
from django.views import View
from django.db import connection
from django.core.cache import cache
import redis
from celery import current_app
import logging

logger = logging.getLogger(__name__)


class HealthCheckView(View):
    """Health check endpoint for monitoring"""

    def get(self, request):
        checks = {
            'database': self.check_database(),
            'cache': self.check_cache(),
            'celery': self.check_celery(),
        }

        all_healthy = all(checks.values())
        status_code = 200 if all_healthy else 503

        return JsonResponse({
            'status': 'healthy' if all_healthy else 'unhealthy',
            'checks': checks,
            'timestamp': timezone.now().isoformat()
        }, status=status_code)

    def check_database(self):
        """Check database connectivity"""
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
            return True
        except Exception as e:
            logger.error(f"Database health check failed: {e}")
            return False

    def check_cache(self):
        """Check cache connectivity"""
        try:
            cache.set('health_check', 'ok', 30)
            return cache.get('health_check') == 'ok'
        except Exception as e:
            logger.error(f"Cache health check failed: {e}")
            return False

    def check_celery(self):
        """Check Celery connectivity"""
        try:
            inspect = current_app.control.inspect()
            stats = inspect.stats()
            return bool(stats)
        except Exception as e:
            logger.error(f"Celery health check failed: {e}")
            return False