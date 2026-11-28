import stripe
from django.conf import settings
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from .services.stripe_service import process_checkout_event, stripe_configured


class StripeWebhookView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = []

    @extend_schema(
        request=None,
        responses={
            200: OpenApiResponse(description='Signed event received.'),
            400: OpenApiResponse(description='Invalid Stripe event or signature.'),
            404: OpenApiResponse(description='Stripe webhook is not configured.'),
            503: OpenApiResponse(description='The event should be retried.'),
        },
        tags=['billing'],
    )
    def post(self, request):
        if not stripe_configured():
            return Response(status=404)
        try:
            event = stripe.Webhook.construct_event(
                request.body, request.headers.get('Stripe-Signature', ''), settings.STRIPE_WEBHOOK_SECRET,
            )
        except (AttributeError, TypeError, ValueError, stripe.SignatureVerificationError):
            return Response(status=400)
        # A test-mode endpoint must never authorize live fulfillment.
        livemode = settings.STRIPE_SECRET_KEY.startswith(('sk_live_', 'rk_live_'))
        if event.get('livemode') is not livemode:
            return Response(status=400)
        return Response(status=200 if process_checkout_event(event) else 503)
