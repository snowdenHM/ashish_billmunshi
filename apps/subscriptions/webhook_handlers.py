from django.http import HttpResponse, HttpResponseBadRequest
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django.utils.decorators import method_decorator
from django.views import View
import json
import logging
from .payment_gateways import payment_manager
from .models import OrganizationSubscription

logger = logging.getLogger(__name__)


@method_decorator(csrf_exempt, name='dispatch')
class StripeWebhookView(View):
    """Handle Stripe webhooks"""

    def post(self, request):
        payload = request.body
        sig_header = request.META.get('HTTP_STRIPE_SIGNATURE')

        if not sig_header:
            logger.warning("Missing Stripe signature header")
            return HttpResponseBadRequest("Missing signature")

        try:
            result = payment_manager.process_webhook_for_subscription(
                gateway_name='stripe',
                payload=payload.decode('utf-8'),
                signature=sig_header
            )

            if result['success']:
                return HttpResponse(status=200)
            else:
                logger.error(f"Webhook processing failed: {result}")
                return HttpResponseBadRequest("Webhook processing failed")

        except Exception as e:
            logger.error(f"Webhook error: {str(e)}")
            return HttpResponseBadRequest(f"Webhook error: {str(e)}")


@method_decorator(csrf_exempt, name='dispatch')
class PayPalWebhookView(View):
    """Handle PayPal webhooks"""

    def post(self, request):
        payload = request.body

        try:
            result = payment_manager.process_webhook_for_subscription(
                gateway_name='paypal',
                payload=payload.decode('utf-8'),
                signature=''  # PayPal uses different verification
            )

            if result['success']:
                return HttpResponse(status=200)
            else:
                return HttpResponseBadRequest("Webhook processing failed")

        except Exception as e:
            logger.error(f"PayPal webhook error: {str(e)}")
            return HttpResponseBadRequest(f"Webhook error: {str(e)}")