"""
Token purchases and the payments of plan upgrades (gap 12, services/payments.py):

- ``GET  /api/v1/token-packages/``: the 3 fixed packages, the price of a free
  amount and the reference conversion. Every role (the insufficient-balance
  dialog shows them).
- ``POST /api/v1/token-purchases/``: starts a purchase and answers the
  provider's checkout URL. Takes ``Idempotency-Key``. 501 without a provider.
- ``GET  /api/v1/token-purchases/<id>/``: pending, approved or rejected. A
  token purchase or the payment of a plan upgrade (``plan_id``).
- ``POST /api/v1/token-purchases/<id>/simulate/``: only for the simulated
  provider (DEBUG or PAYMENTS_ALLOW_SIMULATED): the web app's test checkout
  (``/dev/checkout``) approves or rejects the payment. Without DEBUG, only
  staff and the emails of PAYMENTS_SIMULATE_ALLOWED_EMAILS. Otherwise 404.

All but the packages are for Owner and Admin (``billing.manage``).
"""
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.response import Response

from .models import Payment
from .serializers_billing import (
    CreatePurchaseRequestSerializer,
    PaymentSerializer,
    SimulatePaymentRequestSerializer,
    TokenPackagesSerializer,
)
from .services.payment_providers import SIMULATED, PaymentsUnavailable, may_simulate, simulated_payments_allowed
from .services.payments import CheckoutFailed, IdempotencyKeyReused, PurchaseInProgress, settle_payment, start_token_purchase
from .services.token_packages import custom_price_usd, find_package, token_packages_payload
from .views_subscription import NOT_FOUND, ROLE_DENIED, BillingView, checkout_failed, payments_unavailable

PAYMENT_NOT_FOUND = "No encontramos ese pago."
SIMULATED_REJECTION = "El banco rechazó el pago. Probá con otra tarjeta."
IDEMPOTENCY_HEADER = 'Idempotency-Key'
IDEMPOTENCY_KEY_MAX = 255

IDEMPOTENCY_PARAMETER = OpenApiParameter(
    name=IDEMPOTENCY_HEADER,
    type=str,
    location=OpenApiParameter.HEADER,
    required=False,
    description='One per submit, the same when it is retried: a known key answers the payment it already started.',
)


class TokenPackagesView(BillingView):
    workspace_actions = {'GET': 'wallet.view'}

    @extend_schema(responses={200: TokenPackagesSerializer}, tags=['billing'])
    def get(self, request):
        self.active_workspace()
        return Response(token_packages_payload())


class TokenPurchaseCreateView(BillingView):
    workspace_actions = {'POST': 'billing.manage'}

    @extend_schema(
        request=CreatePurchaseRequestSerializer,
        parameters=[IDEMPOTENCY_PARAMETER],
        responses={
            201: PaymentSerializer,
            400: OpenApiResponse(description='Paquete inexistente, cantidad fuera del mínimo o el máximo, o una dirección de vuelta ajena a la app.'),
            403: ROLE_DENIED,
            409: OpenApiResponse(description='`code: "idempotency_key_reused"`: esa clave ya empezó otra compra; `code: "purchase_in_progress"`: la compra de esa clave todavía abre su checkout.'),
            501: OpenApiResponse(description='`code: "payments_unavailable"`: no hay proveedor de pagos.'),
            502: OpenApiResponse(description='`code: "checkout_failed"`: el proveedor no abrió el checkout.'),
        },
        tags=['billing'],
    )
    def post(self, request):
        workspace = self.active_workspace()
        serializer = CreatePurchaseRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        key = (request.headers.get(IDEMPOTENCY_HEADER) or '').strip() or None
        if key is not None and len(key) > IDEMPOTENCY_KEY_MAX:
            raise ValidationError({'detail': f'{IDEMPOTENCY_HEADER} admite hasta {IDEMPOTENCY_KEY_MAX} caracteres.'})
        package = find_package(data['package_id']) if 'package_id' in data else None
        tokens = package.tokens if package else data['custom_tokens']
        try:
            payment, _created = start_token_purchase(
                workspace,
                user=request.user,
                tokens=tokens,
                amount_usd=package.price_usd if package else custom_price_usd(tokens),
                package_id=package.id if package else '',
                return_url=data['return_url'],
                idempotency_key=key,
            )
        except PaymentsUnavailable:
            return payments_unavailable()
        except IdempotencyKeyReused:
            return Response(
                {'error': 'Esa clave ya se usó para otra compra.', 'code': 'idempotency_key_reused'},
                status=status.HTTP_409_CONFLICT,
            )
        except PurchaseInProgress:
            return Response(
                {'error': 'Esta compra todavía se está iniciando. Probá de nuevo en unos segundos.', 'code': 'purchase_in_progress'},
                status=status.HTTP_409_CONFLICT,
            )
        except CheckoutFailed:
            return checkout_failed()
        # A repeated key answers what the first request created, with the same status.
        return Response(PaymentSerializer(payment).data, status=status.HTTP_201_CREATED)


class _PaymentView(BillingView):
    def payment(self, payment_id):
        payment = Payment.objects.filter(pk=payment_id, workspace=self.active_workspace()).first()
        if payment is None:
            raise NotFound(PAYMENT_NOT_FOUND)
        return payment


class TokenPurchaseDetailView(_PaymentView):
    workspace_actions = {'GET': 'billing.manage'}

    @extend_schema(responses={200: PaymentSerializer, 403: ROLE_DENIED, 404: NOT_FOUND}, tags=['billing'])
    def get(self, request, payment_id):
        return Response(PaymentSerializer(self.payment(payment_id)).data)


class SimulatePaymentView(_PaymentView):
    workspace_actions = {'POST': 'billing.manage'}

    @extend_schema(
        request=SimulatePaymentRequestSerializer,
        responses={200: PaymentSerializer, 403: ROLE_DENIED, 404: NOT_FOUND},
        tags=['billing'],
    )
    def post(self, request, payment_id):
        # Outside development and demos this endpoint does not exist; without DEBUG, only for staff
        # and PAYMENTS_SIMULATE_ALLOWED_EMAILS.
        if not simulated_payments_allowed() or not may_simulate(request.user):
            raise NotFound(PAYMENT_NOT_FOUND)
        payment = self.payment(payment_id)
        # Never a payment of a real provider, even if PAYMENTS_PROVIDER changed since.
        if payment.provider != SIMULATED:
            raise NotFound(PAYMENT_NOT_FOUND)
        serializer = SimulatePaymentRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        # Settled already: it answers the payment as it is (settle_payment changes nothing).
        payment, _changed = settle_payment(
            payment.pk, approved=serializer.validated_data['approved'], failure_reason=SIMULATED_REJECTION,
        )
        return Response(PaymentSerializer(payment).data)
