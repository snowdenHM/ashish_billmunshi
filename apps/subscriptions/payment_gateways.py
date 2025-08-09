"""
Payment gateway integrations for subscription billing
"""
import json
import hashlib
import hmac
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional
from decimal import Decimal
from django.conf import settings
from django.utils import timezone
import logging

logger = logging.getLogger(__name__)


class PaymentGatewayBase(ABC):
    """
    Base class for payment gateway integrations
    """

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.api_key = config.get('api_key')
        self.secret_key = config.get('secret_key')
        self.webhook_secret = config.get('webhook_secret')
        self.base_url = config.get('base_url')

    @abstractmethod
    def create_customer(self, user_data: Dict[str, Any]) -> Dict[str, Any]:
        """Create a customer in the payment gateway"""
        pass

    @abstractmethod
    def create_subscription(self, customer_id: str, plan_id: str, **kwargs) -> Dict[str, Any]:
        """Create a subscription"""
        pass

    @abstractmethod
    def cancel_subscription(self, subscription_id: str) -> Dict[str, Any]:
        """Cancel a subscription"""
        pass

    @abstractmethod
    def update_subscription(self, subscription_id: str, **kwargs) -> Dict[str, Any]:
        """Update a subscription"""
        pass

    @abstractmethod
    def create_invoice(self, customer_id: str, amount: Decimal, **kwargs) -> Dict[str, Any]:
        """Create an invoice"""
        pass

    @abstractmethod
    def verify_webhook(self, payload: str, signature: str) -> bool:
        """Verify webhook signature"""
        pass

    @abstractmethod
    def process_webhook(self, event_data: Dict[str, Any]) -> Dict[str, Any]:
        """Process webhook event"""
        pass


class StripeGateway(PaymentGatewayBase):
    """
    Stripe payment gateway integration
    """

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        try:
            import stripe
            self.stripe = stripe
            stripe.api_key = self.api_key
        except ImportError:
            raise ImportError("Stripe library not installed. Install with: pip install stripe")

    def create_customer(self, user_data: Dict[str, Any]) -> Dict[str, Any]:
        """Create a Stripe customer"""
        try:
            customer = self.stripe.Customer.create(
                email=user_data['email'],
                name=user_data.get('name', ''),
                metadata={
                    'user_id': user_data.get('user_id'),
                    'organization_id': user_data.get('organization_id')
                }
            )

            return {
                'success': True,
                'customer_id': customer.id,
                'data': customer
            }

        except Exception as e:
            logger.error(f"Failed to create Stripe customer: {str(e)}")
            return {
                'success': False,
                'error': str(e)
            }

    def create_subscription(self, customer_id: str, plan_id: str, **kwargs) -> Dict[str, Any]:
        """Create a Stripe subscription"""
        try:
            subscription_data = {
                'customer': customer_id,
                'items': [{'price': plan_id}],
                'metadata': kwargs.get('metadata', {})
            }

            # Add trial period if specified
            if kwargs.get('trial_days'):
                subscription_data['trial_period_days'] = kwargs['trial_days']

            # Add discount if specified
            if kwargs.get('coupon'):
                subscription_data['coupon'] = kwargs['coupon']

            subscription = self.stripe.Subscription.create(**subscription_data)

            return {
                'success': True,
                'subscription_id': subscription.id,
                'status': subscription.status,
                'data': subscription
            }

        except Exception as e:
            logger.error(f"Failed to create Stripe subscription: {str(e)}")
            return {
                'success': False,
                'error': str(e)
            }

    def cancel_subscription(self, subscription_id: str) -> Dict[str, Any]:
        """Cancel a Stripe subscription"""
        try:
            subscription = self.stripe.Subscription.modify(
                subscription_id,
                cancel_at_period_end=True
            )

            return {
                'success': True,
                'status': subscription.status,
                'data': subscription
            }

        except Exception as e:
            logger.error(f"Failed to cancel Stripe subscription: {str(e)}")
            return {
                'success': False,
                'error': str(e)
            }

    def update_subscription(self, subscription_id: str, **kwargs) -> Dict[str, Any]:
        """Update a Stripe subscription"""
        try:
            update_data = {}

            if kwargs.get('plan_id'):
                # Change plan
                subscription = self.stripe.Subscription.retrieve(subscription_id)
                update_data['items'] = [{
                    'id': subscription['items']['data'][0]['id'],
                    'price': kwargs['plan_id']
                }]

            if kwargs.get('prorate') is not None:
                update_data['proration_behavior'] = 'create_prorations' if kwargs['prorate'] else 'none'

            subscription = self.stripe.Subscription.modify(subscription_id, **update_data)

            return {
                'success': True,
                'data': subscription
            }

        except Exception as e:
            logger.error(f"Failed to update Stripe subscription: {str(e)}")
            return {
                'success': False,
                'error': str(e)
            }

    def create_invoice(self, customer_id: str, amount: Decimal, **kwargs) -> Dict[str, Any]:
        """Create a Stripe invoice"""
        try:
            # Create invoice item
            self.stripe.InvoiceItem.create(
                customer=customer_id,
                amount=int(amount * 100),  # Convert to cents
                currency=kwargs.get('currency', 'usd'),
                description=kwargs.get('description', 'Subscription charge')
            )

            # Create and finalize invoice
            invoice = self.stripe.Invoice.create(
                customer=customer_id,
                auto_advance=True
            )

            invoice = self.stripe.Invoice.finalize_invoice(invoice.id)

            return {
                'success': True,
                'invoice_id': invoice.id,
                'data': invoice
            }

        except Exception as e:
            logger.error(f"Failed to create Stripe invoice: {str(e)}")
            return {
                'success': False,
                'error': str(e)
            }

    def verify_webhook(self, payload: str, signature: str) -> bool:
        """Verify Stripe webhook signature"""
        try:
            self.stripe.Webhook.construct_event(
                payload, signature, self.webhook_secret
            )
            return True
        except Exception as e:
            logger.error(f"Stripe webhook verification failed: {str(e)}")
            return False

    def process_webhook(self, event_data: Dict[str, Any]) -> Dict[str, Any]:
        """Process Stripe webhook event"""
        event_type = event_data.get('type')
        data_object = event_data.get('data', {}).get('object', {})

        if event_type == 'invoice.payment_succeeded':
            return self._process_payment_success(data_object)
        elif event_type == 'invoice.payment_failed':
            return self._process_payment_failure(data_object)
        elif event_type == 'customer.subscription.updated':
            return self._process_subscription_update(data_object)
        elif event_type == 'customer.subscription.deleted':
            return self._process_subscription_cancellation(data_object)

        return {'processed': False, 'event_type': event_type}

    def _process_payment_success(self, invoice_data: Dict[str, Any]) -> Dict[str, Any]:
        """Process successful payment"""
        return {
            'event_type': 'payment_succeeded',
            'subscription_id': invoice_data.get('subscription'),
            'customer_id': invoice_data.get('customer'),
            'amount': invoice_data.get('amount_paid', 0) / 100,
            'invoice': {
                'id': invoice_data.get('id'),
                'number': invoice_data.get('number'),
                'subtotal': invoice_data.get('subtotal', 0) / 100,
                'total': invoice_data.get('total', 0) / 100,
                'paid': True
            }
        }

    def _process_payment_failure(self, invoice_data: Dict[str, Any]) -> Dict[str, Any]:
        """Process failed payment"""
        return {
            'event_type': 'payment_failed',
            'subscription_id': invoice_data.get('subscription'),
            'customer_id': invoice_data.get('customer'),
            'amount': invoice_data.get('amount_due', 0) / 100,
            'invoice': {
                'id': invoice_data.get('id'),
                'number': invoice_data.get('number'),
                'paid': False
            }
        }

    def _process_subscription_update(self, subscription_data: Dict[str, Any]) -> Dict[str, Any]:
        """Process subscription update"""
        return {
            'event_type': 'subscription_updated',
            'subscription_id': subscription_data.get('id'),
            'customer_id': subscription_data.get('customer'),
            'status': subscription_data.get('status'),
            'current_period_start': subscription_data.get('current_period_start'),
            'current_period_end': subscription_data.get('current_period_end')
        }

    def _process_subscription_cancellation(self, subscription_data: Dict[str, Any]) -> Dict[str, Any]:
        """Process subscription cancellation"""
        return {
            'event_type': 'subscription_cancelled',
            'subscription_id': subscription_data.get('id'),
            'customer_id': subscription_data.get('customer'),
            'cancelled_at': subscription_data.get('canceled_at')
        }


class PayPalGateway(PaymentGatewayBase):
    """
    PayPal payment gateway integration
    """

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.client_id = config.get('client_id')
        self.client_secret = config.get('client_secret')
        self.environment = config.get('environment', 'sandbox')  # 'sandbox' or 'live'

        if self.environment == 'sandbox':
            self.base_url = 'https://api.sandbox.paypal.com'
        else:
            self.base_url = 'https://api.paypal.com'

    def create_customer(self, user_data: Dict[str, Any]) -> Dict[str, Any]:
        """Create a PayPal customer (simplified)"""
        # PayPal doesn't have a direct customer creation API like Stripe
        # Instead, we'll store the customer data for later use
        return {
            'success': True,
            'customer_id': f"paypal_customer_{user_data.get('user_id')}",
            'data': user_data
        }

    def create_subscription(self, customer_id: str, plan_id: str, **kwargs) -> Dict[str, Any]:
        """Create a PayPal subscription"""
        # This would require implementing PayPal's subscription API
        # For now, return a mock response
        return {
            'success': True,
            'subscription_id': f"paypal_sub_{plan_id}_{customer_id}",
            'status': 'active',
            'data': {
                'id': f"paypal_sub_{plan_id}_{customer_id}",
                'status': 'ACTIVE',
                'plan_id': plan_id
            }
        }

    def cancel_subscription(self, subscription_id: str) -> Dict[str, Any]:
        """Cancel a PayPal subscription"""
        return {
            'success': True,
            'status': 'cancelled',
            'data': {'id': subscription_id, 'status': 'CANCELLED'}
        }

    def update_subscription(self, subscription_id: str, **kwargs) -> Dict[str, Any]:
        """Update a PayPal subscription"""
        return {
            'success': True,
            'data': {'id': subscription_id, 'updated': True}
        }

    def create_invoice(self, customer_id: str, amount: Decimal, **kwargs) -> Dict[str, Any]:
        """Create a PayPal invoice"""
        return {
            'success': True,
            'invoice_id': f"paypal_inv_{customer_id}",
            'data': {
                'id': f"paypal_inv_{customer_id}",
                'total': str(amount),
                'status': 'SENT'
            }
        }

    def verify_webhook(self, payload: str, signature: str) -> bool:
        """Verify PayPal webhook signature"""
        # Implement PayPal webhook verification
        # This involves verifying the signature using PayPal's verification API
        return True  # Simplified for now

    def process_webhook(self, event_data: Dict[str, Any]) -> Dict[str, Any]:
        """Process PayPal webhook event"""
        event_type = event_data.get('event_type')

        if event_type == 'BILLING.SUBSCRIPTION.ACTIVATED':
            return self._process_subscription_activated(event_data)
        elif event_type == 'BILLING.SUBSCRIPTION.CANCELLED':
            return self._process_subscription_cancelled(event_data)
        elif event_type == 'PAYMENT.SALE.COMPLETED':
            return self._process_payment_completed(event_data)

        return {'processed': False, 'event_type': event_type}

    def _process_subscription_activated(self, event_data: Dict[str, Any]) -> Dict[str, Any]:
        """Process subscription activation"""
        resource = event_data.get('resource', {})
        return {
            'event_type': 'subscription_activated',
            'subscription_id': resource.get('id'),
            'status': 'active'
        }

    def _process_subscription_cancelled(self, event_data: Dict[str, Any]) -> Dict[str, Any]:
        """Process subscription cancellation"""
        resource = event_data.get('resource', {})
        return {
            'event_type': 'subscription_cancelled',
            'subscription_id': resource.get('id'),
            'status': 'cancelled'
        }

    def _process_payment_completed(self, event_data: Dict[str, Any]) -> Dict[str, Any]:
        """Process completed payment"""
        resource = event_data.get('resource', {})
        return {
            'event_type': 'payment_succeeded',
            'payment_id': resource.get('id'),
            'amount': resource.get('amount', {}).get('total')
        }


class MockGateway(PaymentGatewayBase):
    """
    Mock payment gateway for testing and development
    """

    def create_customer(self, user_data: Dict[str, Any]) -> Dict[str, Any]:
        """Create a mock customer"""
        customer_id = f"mock_cust_{user_data.get('user_id', 'unknown')}"
        return {
            'success': True,
            'customer_id': customer_id,
            'data': {
                'id': customer_id,
                'email': user_data.get('email'),
                'created': timezone.now().isoformat()
            }
        }

    def create_subscription(self, customer_id: str, plan_id: str, **kwargs) -> Dict[str, Any]:
        """Create a mock subscription"""
        subscription_id = f"mock_sub_{customer_id}_{plan_id}"
        return {
            'success': True,
            'subscription_id': subscription_id,
            'status': 'active',
            'data': {
                'id': subscription_id,
                'customer': customer_id,
                'plan': plan_id,
                'status': 'active',
                'current_period_start': timezone.now().timestamp(),
                'current_period_end': (timezone.now() + timezone.timedelta(days=30)).timestamp(),
                'trial_start': kwargs.get('trial_days') and timezone.now().timestamp(),
                'trial_end': kwargs.get('trial_days') and (
                            timezone.now() + timezone.timedelta(days=kwargs['trial_days'])).timestamp()
            }
        }

    def cancel_subscription(self, subscription_id: str) -> Dict[str, Any]:
        """Cancel a mock subscription"""
        return {
            'success': True,
            'status': 'cancelled',
            'data': {
                'id': subscription_id,
                'status': 'cancelled',
                'cancelled_at': timezone.now().timestamp()
            }
        }

    def update_subscription(self, subscription_id: str, **kwargs) -> Dict[str, Any]:
        """Update a mock subscription"""
        return {
            'success': True,
            'data': {
                'id': subscription_id,
                'updated': True,
                'changes': kwargs
            }
        }

    def create_invoice(self, customer_id: str, amount: Decimal, **kwargs) -> Dict[str, Any]:
        """Create a mock invoice"""
        invoice_id = f"mock_inv_{customer_id}_{int(timezone.now().timestamp())}"
        return {
            'success': True,
            'invoice_id': invoice_id,
            'data': {
                'id': invoice_id,
                'customer': customer_id,
                'amount_due': int(amount * 100),
                'amount_paid': int(amount * 100),
                'status': 'paid',
                'paid': True,
                'created': timezone.now().timestamp()
            }
        }

    def verify_webhook(self, payload: str, signature: str) -> bool:
        """Verify mock webhook (always returns True)"""
        return True

    def process_webhook(self, event_data: Dict[str, Any]) -> Dict[str, Any]:
        """Process mock webhook event"""
        return {
            'event_type': event_data.get('type', 'unknown'),
            'processed': True,
            'mock': True,
            'data': event_data
        }


class PaymentGatewayFactory:
    """
    Factory class for creating payment gateway instances
    """

    _gateways = {
        'stripe': StripeGateway,
        'paypal': PayPalGateway,
        'mock': MockGateway
    }

    @classmethod
    def create_gateway(cls, gateway_type: str, config: Dict[str, Any]) -> PaymentGatewayBase:
        """
        Create a payment gateway instance

        Args:
            gateway_type: Type of gateway ('stripe', 'paypal', 'mock')
            config: Configuration dictionary for the gateway

        Returns:
            PaymentGatewayBase: Gateway instance

        Raises:
            ValueError: If gateway type is not supported
        """
        if gateway_type not in cls._gateways:
            raise ValueError(f"Unsupported gateway type: {gateway_type}")

        gateway_class = cls._gateways[gateway_type]
        return gateway_class(config)

    @classmethod
    def get_available_gateways(cls) -> list:
        """Get list of available gateway types"""
        return list(cls._gateways.keys())


class PaymentGatewayManager:
    """
    Manager class for handling payment gateway operations
    """

    def __init__(self):
        self.gateways = {}
        self._load_gateways()

    def _load_gateways(self):
        """Load configured payment gateways"""
        gateway_configs = getattr(settings, 'PAYMENT_GATEWAYS', {})

        for gateway_name, config in gateway_configs.items():
            if config.get('enabled', False):
                try:
                    gateway = PaymentGatewayFactory.create_gateway(
                        config['type'],
                        config
                    )
                    self.gateways[gateway_name] = gateway
                    logger.info(f"Loaded payment gateway: {gateway_name}")
                except Exception as e:
                    logger.error(f"Failed to load gateway {gateway_name}: {str(e)}")

    def get_gateway(self, gateway_name: str = None) -> PaymentGatewayBase:
        """
        Get a payment gateway instance

        Args:
            gateway_name: Name of the gateway, uses default if None

        Returns:
            PaymentGatewayBase: Gateway instance

        Raises:
            ValueError: If gateway is not found
        """
        if gateway_name is None:
            gateway_name = getattr(settings, 'DEFAULT_PAYMENT_GATEWAY', 'mock')

        if gateway_name not in self.gateways:
            available = list(self.gateways.keys())
            raise ValueError(f"Gateway '{gateway_name}' not found. Available: {available}")

        return self.gateways[gateway_name]

    def create_customer_for_organization(self, organization) -> Dict[str, Any]:
        """
        Create a customer in the payment gateway for an organization

        Args:
            organization: Organization instance

        Returns:
            dict: Result of customer creation
        """
        gateway = self.get_gateway()

        user_data = {
            'user_id': organization.owner.id,
            'organization_id': organization.id,
            'email': organization.owner.email,
            'name': organization.owner.get_display_name(),
            'organization_name': organization.name
        }

        return gateway.create_customer(user_data)

    def create_subscription_for_organization(self, subscription, gateway_plan_id: str, **kwargs) -> Dict[str, Any]:
        """
        Create a subscription in the payment gateway

        Args:
            subscription: OrganizationSubscription instance
            gateway_plan_id: Plan ID in the payment gateway
            **kwargs: Additional subscription parameters

        Returns:
            dict: Result of subscription creation
        """
        gateway = self.get_gateway()

        # Get or create customer
        customer_result = self.create_customer_for_organization(subscription.organization)
        if not customer_result['success']:
            return customer_result

        customer_id = customer_result['customer_id']

        # Add metadata
        kwargs.setdefault('metadata', {}).update({
            'organization_id': subscription.organization.id,
            'subscription_id': str(subscription.subscription_id),
            'plan_name': subscription.plan.name
        })

        return gateway.create_subscription(customer_id, gateway_plan_id, **kwargs)

    def process_webhook_for_subscription(self, gateway_name: str, payload: str, signature: str) -> Dict[str, Any]:
        """
        Process a webhook for subscription updates

        Args:
            gateway_name: Name of the payment gateway
            payload: Webhook payload
            signature: Webhook signature

        Returns:
            dict: Processing result
        """
        gateway = self.get_gateway(gateway_name)

        # Verify webhook signature
        if not gateway.verify_webhook(payload, signature):
            return {'success': False, 'error': 'Invalid webhook signature'}

        # Parse payload
        try:
            event_data = json.loads(payload)
        except json.JSONDecodeError as e:
            return {'success': False, 'error': f'Invalid JSON payload: {str(e)}'}

        # Process the webhook
        result = gateway.process_webhook(event_data)

        # If webhook was processed, trigger local subscription updates
        if result.get('processed'):
            from .tasks import process_subscription_webhook
            process_subscription_webhook.delay(result)

        return {'success': True, 'result': result}


# Global payment gateway manager instance
payment_manager = PaymentGatewayManager()


def get_payment_gateway(gateway_name: str = None) -> PaymentGatewayBase:
    """
    Convenience function to get a payment gateway

    Args:
        gateway_name: Name of the gateway, uses default if None

    Returns:
        PaymentGatewayBase: Gateway instance
    """
    return payment_manager.get_gateway(gateway_name)


# Configuration helper functions
def configure_stripe_gateway(api_key: str, webhook_secret: str, environment: str = 'test') -> Dict[str, Any]:
    """
    Create Stripe gateway configuration

    Args:
        api_key: Stripe API key
        webhook_secret: Stripe webhook endpoint secret
        environment: 'test' or 'live'

    Returns:
        dict: Gateway configuration
    """
    return {
        'type': 'stripe',
        'enabled': True,
        'api_key': api_key,
        'webhook_secret': webhook_secret,
        'environment': environment
    }


def configure_paypal_gateway(client_id: str, client_secret: str, environment: str = 'sandbox') -> Dict[str, Any]:
    """
    Create PayPal gateway configuration

    Args:
        client_id: PayPal client ID
        client_secret: PayPal client secret
        environment: 'sandbox' or 'live'

    Returns:
        dict: Gateway configuration
    """
    return {
        'type': 'paypal',
        'enabled': True,
        'client_id': client_id,
        'client_secret': client_secret,
        'environment': environment
    }


def configure_mock_gateway() -> Dict[str, Any]:
    """
    Create mock gateway configuration for testing

    Returns:
        dict: Gateway configuration
    """
    return {
        'type': 'mock',
        'enabled': True,
        'api_key': 'mock_api_key',
        'webhook_secret': 'mock_webhook_secret'
    }