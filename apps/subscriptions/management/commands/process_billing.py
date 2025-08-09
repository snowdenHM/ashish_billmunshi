"""
Management command to process billing and subscription renewals
"""
from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta
from apps.subscriptions.models import OrganizationSubscription, SubscriptionInvoice
from apps.subscriptions.utils import BillingCalculator
from apps.subscriptions.tasks import send_subscription_email
import logging

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Process billing for subscriptions due for renewal'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be processed without making changes',
        )
        parser.add_argument(
            '--days-ahead',
            type=int,
            default=0,
            help='Process subscriptions due N days from now (default: today)',
        )
        parser.add_argument(
            '--plan-type',
            type=str,
            choices=['free', 'basic', 'pro', 'enterprise'],
            help='Only process subscriptions of specific plan type',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        days_ahead = options['days_ahead']
        plan_type = options['plan_type']

        if dry_run:
            self.stdout.write(
                self.style.WARNING('DRY RUN MODE - No changes will be made')
            )

        # Calculate target date
        target_date = timezone.now() + timedelta(days=days_ahead)

        # Get subscriptions due for renewal
        subscriptions = OrganizationSubscription.objects.filter(
            status='active',
            next_billing_date__date=target_date.date()
        ).select_related('organization', 'plan')

        if plan_type:
            subscriptions = subscriptions.filter(plan__plan_type=plan_type)

        self.stdout.write(
            f'Found {subscriptions.count()} subscriptions due for billing on {target_date.date()}'
        )

        processed_count = 0
        failed_count = 0

        for subscription in subscriptions:
            try:
                self.stdout.write(f'Processing {subscription.organization.name}...')

                if not dry_run:
                    # Generate invoice
                    invoice = BillingCalculator.generate_invoice(
                        subscription=subscription,
                        period_start=subscription.current_period_start,
                        period_end=subscription.current_period_end
                    )

                    # Update subscription for next period
                    if subscription.plan.billing_interval == 'monthly':
                        next_period_start = subscription.current_period_end
                        next_period_end = next_period_start + timedelta(days=30)
                    elif subscription.plan.billing_interval == 'yearly':
                        next_period_start = subscription.current_period_end
                        next_period_end = next_period_start + timedelta(days=365)
                    else:
                        next_period_start = subscription.current_period_end
                        next_period_end = next_period_start + timedelta(days=30)

                    subscription.current_period_start = next_period_start
                    subscription.current_period_end = next_period_end
                    subscription.next_billing_date = next_period_end
                    subscription.save()

                    # Send invoice email
                    send_subscription_email.delay(
                        subscription.id,
                        'invoice_generated',
                        {'invoice': invoice},
                        f'Invoice {invoice.invoice_number}'
                    )

                    self.stdout.write(
                        self.style.SUCCESS(
                            f'  ✓ Created invoice {invoice.invoice_number}'
                        )
                    )
                else:
                    self.stdout.write('  → Would create invoice and update billing period')

                processed_count += 1

            except Exception as e:
                failed_count += 1
                self.stdout.write(
                    self.style.ERROR(f'  ✗ Failed: {str(e)}')
                )
                logger.error(f'Failed to process billing for {subscription.id}: {str(e)}')

        # Summary
        self.stdout.write('')
        self.stdout.write(
            self.style.SUCCESS(f'Successfully processed: {processed_count}')
        )
        if failed_count > 0:
            self.stdout.write(
                self.style.ERROR(f'Failed to process: {failed_count}')
            )

        if dry_run:
            self.stdout.write(
                self.style.WARNING('This was a dry run - no actual changes were made')
            )