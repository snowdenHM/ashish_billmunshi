from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from apps.teams.models import Role
from apps.subscriptions.models import SubscriptionPlan, SubscriptionFeature

User = get_user_model()

class Command(BaseCommand):
    help = 'Setup default data for the application'

    def handle(self, *args, **options):
        self.setup_roles()
        self.setup_subscription_plans()
        self.setup_features()
        self.stdout.write(
            self.style.SUCCESS('Successfully set up default data')
        )

    def setup_roles(self):
        """Create default roles"""
        roles_data = [
            {
                'name': Role.OWNER,
                'description': 'Organization Owner with full access',
                'can_manage_organization': True,
                'can_manage_members': True,
                'can_manage_api_keys': True,
                'can_view_analytics': True,
                'can_manage_billing': True,
            },
            {
                'name': Role.ADMIN,
                'description': 'Organization Admin with management access',
                'can_manage_organization': True,
                'can_manage_members': True,
                'can_manage_api_keys': True,
                'can_view_analytics': True,
                'can_manage_billing': False,
            },
            {
                'name': Role.MEMBER,
                'description': 'Organization Member with standard access',
                'can_manage_organization': False,
                'can_manage_members': False,
                'can_manage_api_keys': False,
                'can_view_analytics': True,
                'can_manage_billing': False,
            },
            {
                'name': Role.VIEWER,
                'description': 'Organization Viewer with read-only access',
                'can_manage_organization': False,
                'can_manage_members': False,
                'can_manage_api_keys': False,
                'can_view_analytics': True,
                'can_manage_billing': False,
            },
        ]

        for role_data in roles_data:
            role, created = Role.objects.get_or_create(
                name=role_data['name'],
                defaults=role_data
            )
            if created:
                self.stdout.write(f'Created role: {role.name}')

    def setup_subscription_plans(self):
        """Create default subscription plans"""
        plans_data = [
            {
                'name': 'Free',
                'description': 'Perfect for getting started',
                'plan_type': 'free',
                'price': 0,
                'billing_interval': 'monthly',
                'max_users': 1,
                'max_organizations': 1,
                'max_api_calls_per_month': 1000,
                'max_api_keys': 1,
                'max_storage_gb': 1,
                'trial_days': 0,
                'sort_order': 1,
            },
            {
                'name': 'Starter',
                'description': 'For small teams and projects',
                'plan_type': 'basic',
                'price': 29,
                'billing_interval': 'monthly',
                'max_users': 5,
                'max_organizations': 1,
                'max_api_calls_per_month': 10000,
                'max_api_keys': 3,
                'max_storage_gb': 10,
                'trial_days': 14,
                'sort_order': 2,
            },
            {
                'name': 'Professional',
                'description': 'For growing teams and businesses',
                'plan_type': 'pro',
                'price': 99,
                'billing_interval': 'monthly',
                'max_users': 25,
                'max_organizations': 3,
                'max_api_calls_per_month': 100000,
                'max_api_keys': 10,
                'max_storage_gb': 100,
                'custom_branding': True,
                'priority_support': True,
                'advanced_analytics': True,
                'api_rate_limit_boost': True,
                'trial_days': 14,
                'sort_order': 3,
                'featured': True,
            },
            {
                'name': 'Enterprise',
                'description': 'For large organizations with advanced needs',
                'plan_type': 'enterprise',
                'price': 299,
                'billing_interval': 'monthly',
                'max_users': 100,
                'max_organizations': 10,
                'max_api_calls_per_month': 1000000,
                'max_api_keys': 50,
                'max_storage_gb': 1000,
                'custom_branding': True,
                'priority_support': True,
                'advanced_analytics': True,
                'sso_integration': True,
                'api_rate_limit_boost': True,
                'white_label': True,
                'trial_days': 30,
                'sort_order': 4,
            },
        ]

        for plan_data in plans_data:
            plan, created = SubscriptionPlan.objects.get_or_create(
                name=plan_data['name'],
                defaults=plan_data
            )
            if created:
                self.stdout.write(f'Created plan: {plan.name}')

    def setup_features(self):
        """Create default subscription features"""
        features_data = [
            {
                'name': 'Custom Domain',
                'feature_key': 'custom_domain',
                'feature_type': 'boolean',
                'description': 'Use your own custom domain',
            },
            {
                'name': 'API Rate Limit',
                'feature_key': 'api_rate_limit',
                'feature_type': 'numeric',
                'description': 'API requests per minute',
                'default_numeric_value': 60,
            },
            {
                'name': 'Support Level',
                'feature_key': 'support_level',
                'feature_type': 'text',
                'description': 'Level of customer support',
                'default_text_value': 'standard',
            },
        ]

        for feature_data in features_data:
            feature, created = SubscriptionFeature.objects.get_or_create(
                feature_key=feature_data['feature_key'],
                defaults=feature_data
            )
            if created:
                self.stdout.write(f'Created feature: {feature.name}')