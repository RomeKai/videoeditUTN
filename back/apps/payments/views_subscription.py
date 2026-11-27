"""
The subscription of the active workspace (gap 12, services/subscriptions.py):

- ``GET  /api/v1/subscription/``: the plan, when the paid period renews and
  the scheduled downgrade. Every role (the topbar shows the plan).
- ``POST /api/v1/subscription/change/``: change of plan, Owner and Admin only
  (``billing.manage``). Any other paid plan applies when the provider
  confirms its payment; a free plan waits for the end of the paid period
  (409 ``seats_exceeded`` with more seats in use than the plan has); the
  current plan cancels what is scheduled or waiting for its payment (older
  web builds: the web uses cancel-pending now).
- ``POST /api/v1/subscription/cancel-pending/``: «Seguir con …» in Planes,
  Owner and Admin only. It cancels the plan payment and the scheduled
  downgrade the screen showed, and never changes the plan: 409 with a
  ``code`` and nothing changed when the workspace moved on meanwhile.

All act in the active workspace (X-Workspace-Id) with ROLE_MATRIX (apps/core/permissions.py).
"""
from django.conf import settings
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.exceptions import NotFound
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.permissions import WorkspaceRolePermission
from apps.core.workspace import NOT_FOUND_DETAIL, resolve_active_workspace
from apps.users.models import Workspace

from .serializers_billing import (
    CancelPendingRequestSerializer, CancelPendingResponseSerializer, ChangePlanRequestSerializer,
    ChangePlanResponseSerializer, SubscriptionSerializer,
)
from .services.payment_providers import PaymentsUnavailable
from .services.payments import CheckoutFailed
from .services.subscriptions import (
    CheckoutInProgress, NothingToCancel, PendingChanged, PlanChanged, PlanChangeSuperseded, PlanNotFound,
    SeatsExceeded, cancel_pending_change, change_plan, subscription_payload,
)

NO_PLAN = "Este workspace no tiene un plan activo. Contactá a soporte."
PLAN_NOT_FOUND = "El plan no existe."
# `plan_id` stays for web builds from before cancel-pending, which match that field error (the backend can
# deploy first). The web of now reads `code`.
ALREADY_CURRENT = {
    'error': "Ya es tu plan actual.",
    'code': 'nothing_to_cancel',
    'plan_id': ["Ya es tu plan actual."],
}
# cancel-pending: what the screen showed is not what the workspace has now. Nothing changed.
NOTHING_LEFT_TO_CANCEL = {
    'error': "Ya no hay nada pendiente para cancelar.",
    'code': 'nothing_to_cancel',
}
PENDING_CHANGED = {
    'error': "Mientras tanto cambió lo que estaba pendiente. No cancelamos nada: revisá cómo quedó y, si querés, probá de nuevo.",
    'code': 'pending_changed',
}
# 501 and not 503: the web app reads any 503 as maintenance (K5).
PAYMENTS_UNAVAILABLE = {
    'error': "Los pagos todavía no están habilitados.",
    'code': 'payments_unavailable',
}
CHECKOUT_IN_PROGRESS = {
    'error': "Ya estamos abriendo el checkout de este cambio de plan. Probá de nuevo en unos segundos.",
    'code': 'checkout_in_progress',
}
PLAN_CHANGE_SUPERSEDED = {
    'error': "Elegiste otro plan mientras abríamos el pago. Revisá tu plan y, si querés, probá de nuevo.",
    'code': 'plan_change_superseded',
}
CHECKOUT_FAILED = {
    'error': "No pudimos abrir el checkout del proveedor de pagos. No se te cobró nada: probá de nuevo en unos minutos.",
    'code': 'checkout_failed',
}

ROLE_DENIED = OpenApiResponse(description='El rol no alcanza: `detail` en castellano.')
NOT_FOUND = OpenApiResponse(description='El workspace (o lo pedido) no existe para este usuario.')


class BillingView(APIView):
    """An endpoint of the active workspace's billing. ``workspace_actions`` maps each method to ROLE_MATRIX."""

    permission_classes = [IsAuthenticated, WorkspaceRolePermission]

    def active_workspace(self):
        active = resolve_active_workspace(self.request)
        if active is None:
            raise NotFound(NOT_FOUND_DETAIL)
        return active.workspace


def payments_unavailable():
    return Response(PAYMENTS_UNAVAILABLE, status=status.HTTP_501_NOT_IMPLEMENTED)


def checkout_failed():
    return Response(CHECKOUT_FAILED, status=status.HTTP_502_BAD_GATEWAY)


class SubscriptionView(BillingView):
    workspace_actions = {'GET': 'wallet.view'}

    @extend_schema(responses={200: SubscriptionSerializer, 404: NOT_FOUND}, tags=['billing'])
    def get(self, request):
        payload = subscription_payload(self.active_workspace())
        if payload is None:
            raise NotFound(NO_PLAN)
        return Response(SubscriptionSerializer(payload).data)


class ChangePlanView(BillingView):
    workspace_actions = {'POST': 'billing.manage'}

    @extend_schema(
        request=ChangePlanRequestSerializer,
        responses={
            200: ChangePlanResponseSerializer,
            400: OpenApiResponse(description='`code: "nothing_to_cancel"`: ya es el plan actual y no hay nada programado ni esperando su pago; o un campo inválido.'),
            403: ROLE_DENIED,
            404: NOT_FOUND,
            409: OpenApiResponse(description='`code: "seats_exceeded"`: más lugares ocupados que los del plan elegido; `code: "checkout_in_progress"`: el mismo pago todavía abre su checkout; `code: "plan_change_superseded"`: se eligió otro plan mientras abría.'),
            501: OpenApiResponse(description='`code: "payments_unavailable"`: subir a un plan pago sin proveedor de pagos.'),
            502: OpenApiResponse(description='`code: "checkout_failed"`: el proveedor no abrió el checkout.'),
        },
        tags=['billing'],
    )
    def post(self, request):
        workspace = self.active_workspace()
        serializer = ChangePlanRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        return_url = data.get('return_url') or f"{settings.FRONTEND_URL.rstrip('/')}/uso/planes"
        try:
            change = change_plan(workspace.pk, data['plan_id'], user=request.user, return_url=return_url)
        except NothingToCancel:
            return Response(ALREADY_CURRENT, status=status.HTTP_400_BAD_REQUEST)
        except PlanNotFound:
            raise NotFound(PLAN_NOT_FOUND)
        except Workspace.DoesNotExist:
            # Deleted while this request waited for its row (lock_workspace): nothing changed.
            raise NotFound(NOT_FOUND_DETAIL)
        except SeatsExceeded as exc:
            return Response(
                {'error': exc.message, 'code': 'seats_exceeded', 'seats_used': exc.used, 'max_members': exc.plan.max_members},
                status=status.HTTP_409_CONFLICT,
            )
        except CheckoutInProgress:
            return Response(CHECKOUT_IN_PROGRESS, status=status.HTTP_409_CONFLICT)
        except PlanChangeSuperseded:
            return Response(PLAN_CHANGE_SUPERSEDED, status=status.HTTP_409_CONFLICT)
        except PaymentsUnavailable:
            return payments_unavailable()
        except CheckoutFailed:
            return checkout_failed()
        body = {
            'applied': change.applied,
            'checkout_url': change.checkout_url,
            'subscription': SubscriptionSerializer(subscription_payload(change.workspace)).data,
        }
        return Response(body)


def plan_changed(plan):
    """The 409 of a cancel whose screen showed another plan as the current one: it names the plan of now."""
    now_on = f": ahora estás en el plan {plan.name}" if plan is not None else ""
    return {'error': f"Tu plan cambió mientras tanto{now_on}. No cancelamos nada.", 'code': 'plan_changed'}


class CancelPendingChangeView(BillingView):
    """«Seguir con …»: cancel only what Planes showed (services/subscriptions.py · cancel_pending_change)."""

    workspace_actions = {'POST': 'billing.manage'}

    @extend_schema(
        request=CancelPendingRequestSerializer,
        responses={
            200: CancelPendingResponseSerializer,
            400: OpenApiResponse(description='Un id inválido, o ni `payment_id` ni `pending_plan_id`.'),
            403: ROLE_DENIED,
            404: NOT_FOUND,
            409: OpenApiResponse(description='No cambia nada. `code: "plan_changed"`: el plan actual ya no es `current_plan_id`; `code: "pending_changed"`: espera algo que la pantalla no mostró; `code: "nothing_to_cancel"`: ya no espera nada de lo que mostró.'),
        },
        tags=['billing'],
    )
    def post(self, request):
        workspace = self.active_workspace()
        serializer = CancelPendingRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            workspace = cancel_pending_change(
                workspace.pk,
                current_plan_id=data['current_plan_id'],
                payment_id=data.get('payment_id'),
                pending_plan_id=data.get('pending_plan_id'),
            )
        except Workspace.DoesNotExist:
            # Deleted while this request waited for its row (lock_workspace): nothing changed.
            raise NotFound(NOT_FOUND_DETAIL)
        except PlanChanged as exc:
            return Response(plan_changed(exc.plan), status=status.HTTP_409_CONFLICT)
        except PendingChanged:
            return Response(PENDING_CHANGED, status=status.HTTP_409_CONFLICT)
        except NothingToCancel:
            return Response(NOTHING_LEFT_TO_CANCEL, status=status.HTTP_409_CONFLICT)
        return Response({'subscription': SubscriptionSerializer(subscription_payload(workspace)).data})
