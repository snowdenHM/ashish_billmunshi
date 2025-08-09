from django.apps import AppConfig


class SubscriptionsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.subscriptions'
    verbose_name = 'Subscriptions & Billing'

    def ready(self):
        # Import signals to ensure they are registered
        import apps.subscriptions.signals