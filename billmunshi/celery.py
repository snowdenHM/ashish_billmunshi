import os
from celery import Celery
from django.conf import settings

# Set the default Django settings module for the 'celery' program.
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'billmunshi.settings')

app = Celery('billmunshi')

# Using a string here means the worker doesn't have to serialize
# the configuration object to child processes.
app.config_from_object('django.conf:settings', namespace='CELERY')

# Load task modules from all registered Django apps.
app.autodiscover_tasks()

# Celery beat schedule
app.conf.beat_schedule = {
    'cleanup-expired-sessions': {
        'task': 'apps.users.tasks.cleanup_expired_sessions',
        'schedule': 300.0,  # Every 5 minutes
    },
    'process-usage-records': {
        'task': 'apps.subscriptions.tasks.process_daily_usage',
        'schedule': 3600.0,  # Every hour
    },
    'check-subscription-renewals': {
        'task': 'apps.subscriptions.tasks.check_subscription_renewals',
        'schedule': 1800.0,  # Every 30 minutes
    },
    'send-usage-alerts': {
        'task': 'apps.subscriptions.tasks.send_usage_alerts',
        'schedule': 3600.0,  # Every hour
    },
    'cleanup-old-activities': {
        'task': 'apps.users.tasks.cleanup_old_activities',
        'schedule': 86400.0,  # Daily
    },
    'generate-monthly-reports': {
        'task': 'apps.subscriptions.tasks.generate_monthly_reports',
        'schedule': 86400.0,  # Daily
    },
    'cleanup-old-usage-records': {
        'task': 'apps.subscriptions.tasks.cleanup_old_usage_records',
        'schedule': 86400.0,  # Daily
    },
}

app.conf.timezone = 'Asia/Kolkata'

@app.task(bind=True)
def debug_task(self):
    print(f'Request: {self.request!r}')