"""
Bodies of the subscription and payment endpoints (views_subscription.py,
views_purchases.py), named like the front contract (front/src/api/contracts/billing.ts).
"""
from urllib.parse import urlsplit

from django.conf import settings
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from .models import Payment
from .serializers import PublicPlanSerializer
from .services.payments import format_tokens
from .services.token_packages import CUSTOM_MAX_TOKENS, CUSTOM_MIN_TOKENS, find_package

RETURN_URL_ERRORS = {
    'invalid': 'Mandá una dirección completa de la app (https://…).',
    'max_length': 'La dirección es demasiado larga.',
}
RETURN_URL_OUTSIDE = 'La dirección de vuelta tiene que ser una página de la app.'
# The checkout URL carries it encoded (up to three times longer) and has 2000 characters.
RETURN_URL_MAX = 600


def _origin(url):
    parts = urlsplit(url)
    return f'{parts.scheme}://{parts.netloc}'.lower()


def check_return_url(url):
    """
    The provider sends the browser back here: only a page of the web app
    (FRONTEND_URL or CORS_ALLOWED_ORIGINS), never another site. Any origin
    with DEBUG or CORS_ALLOW_ALL_ORIGINS, as in development.
    """
    if urlsplit(url).scheme not in ('http', 'https'):
        raise serializers.ValidationError(RETURN_URL_ERRORS['invalid'])
    if settings.DEBUG or getattr(settings, 'CORS_ALLOW_ALL_ORIGINS', False):
        return url
    allowed = {_origin(settings.FRONTEND_URL), *(_origin(item) for item in getattr(settings, 'CORS_ALLOWED_ORIGINS', []))}
    if _origin(url) not in allowed:
        raise serializers.ValidationError(RETURN_URL_OUTSIDE)
    return url


class PendingPlanSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()
    effective_at = serializers.DateTimeField()


class SubscriptionSerializer(serializers.Serializer):
    """Front contract `Subscription`."""

    plan = PublicPlanSerializer()
    status = serializers.ChoiceField(choices=['active', 'past_due', 'canceled'])
    # Null without a paid period (free plan, or nothing paid through the checkout).
    renews_at = serializers.DateTimeField(allow_null=True)
    # The downgrade waiting for the end of the period.
    pending_plan = PendingPlanSerializer(allow_null=True)


class ChangePlanRequestSerializer(serializers.Serializer):
    plan_id = serializers.UUIDField(error_messages={'invalid': 'Elegí un plan.', 'required': 'Elegí un plan.', 'null': 'Elegí un plan.'})
    return_url = serializers.URLField(required=False, max_length=RETURN_URL_MAX, error_messages=RETURN_URL_ERRORS)

    def validate_return_url(self, value):
        return check_return_url(value)


class ChangePlanResponseSerializer(serializers.Serializer):
    applied = serializers.ChoiceField(choices=['now', 'period_end'])
    checkout_url = serializers.URLField(allow_null=True)
    subscription = SubscriptionSerializer()


class TokenPackageSerializer(serializers.Serializer):
    id = serializers.CharField()
    tokens = serializers.IntegerField()
    price_usd = serializers.DecimalField(max_digits=10, decimal_places=2)
    discount_rate = serializers.DecimalField(max_digits=4, decimal_places=2)


class TokenPackagesSerializer(serializers.Serializer):
    """Front contract `TokenPackages` (for the OpenAPI schema; the view writes services/token_packages.py)."""

    packages = TokenPackageSerializer(many=True)
    custom_price_per_token_usd = serializers.DecimalField(max_digits=6, decimal_places=4)
    custom_min_tokens = serializers.IntegerField()
    local_currency = serializers.CharField()
    usd_to_local_rate = serializers.DecimalField(max_digits=12, decimal_places=4)


class CreatePurchaseRequestSerializer(serializers.Serializer):
    package_id = serializers.CharField(required=False, max_length=32)
    custom_tokens = serializers.IntegerField(
        required=False,
        error_messages={'invalid': 'Escribí una cantidad de tokens, sin decimales.', 'max_string_length': 'La cantidad es demasiado grande.'},
    )
    return_url = serializers.URLField(max_length=RETURN_URL_MAX, error_messages={**RETURN_URL_ERRORS, 'required': 'Falta la dirección de vuelta.'})

    def validate_return_url(self, value):
        return check_return_url(value)

    def validate_package_id(self, value):
        if find_package(value) is None:
            raise serializers.ValidationError('El paquete no existe.')
        return value

    def validate_custom_tokens(self, value):
        if value < CUSTOM_MIN_TOKENS:
            raise serializers.ValidationError(f'El mínimo es {format_tokens(CUSTOM_MIN_TOKENS)} tokens.')
        if value > CUSTOM_MAX_TOKENS:
            raise serializers.ValidationError(f'El máximo es {format_tokens(CUSTOM_MAX_TOKENS)} tokens por compra.')
        return value

    def validate(self, attrs):
        has_package = 'package_id' in attrs
        has_custom = 'custom_tokens' in attrs
        if has_package == has_custom:
            raise serializers.ValidationError(
                'Elegí un paquete o una cantidad de tokens, no los dos.' if has_package else 'Elegí un paquete o escribí una cantidad de tokens.'
            )
        return attrs


class SimulatePaymentRequestSerializer(serializers.Serializer):
    approved = serializers.BooleanField(error_messages={'required': 'Decí si el pago se aprueba.', 'invalid': 'Decí si el pago se aprueba.'})


class PaymentSerializer(serializers.ModelSerializer):
    """Front contract `TokenPurchase` / `Payment`: a token purchase, or the payment of a plan upgrade (`plan_id`, tokens 0)."""

    plan_id = serializers.UUIDField(read_only=True, allow_null=True)
    checkout_url = serializers.SerializerMethodField()
    failure_reason = serializers.SerializerMethodField()

    class Meta:
        model = Payment
        fields = ['id', 'status', 'tokens', 'amount_usd', 'plan_id', 'checkout_url', 'failure_reason', 'created_at', 'balance_after']
        read_only_fields = fields

    @extend_schema_field(OpenApiTypes.URI)
    def get_checkout_url(self, payment):
        # Only while there is something to pay.
        return (payment.checkout_url or None) if payment.status == Payment.Status.PENDING else None

    @extend_schema_field(OpenApiTypes.STR)
    def get_failure_reason(self, payment):
        return payment.failure_reason or None
