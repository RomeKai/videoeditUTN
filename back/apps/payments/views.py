# payments views
"""
Plans, prices and the wallet of the active workspace (front contract
``billing.*``, front/src/api/contracts/billing.ts):

- ``GET  /api/v1/plans/`` and ``GET /api/v1/pricing/``: public.
- ``POST /api/v1/pricing/estimate/``: what a configuration costs here.
- ``GET  /api/v1/wallet/``: balance and what is reserved, per project.
- ``GET  /api/v1/wallet/usage/``: what was charged per day of a month.
- ``GET  /api/v1/transactions/`` and ``transactions/export/``: the movements.

The wallet's are scoped to the active workspace (X-Workspace-Id). Every role
sees the balance (``wallet.view``); usage and movements are for Dueño and
Admin (``billing.manage``), like the screens that show them.
"""
from django.http import Http404, HttpResponse
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers
from rest_framework.generics import ListAPIView
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.permissions import WorkspaceRolePermission
from apps.core.workspace import resolve_active_workspace

from .models import Transaction, Wallet
from .serializers import (
    EstimateRequestSerializer, PublicPlanSerializer, TransactionSerializer, TransactionsQuerySerializer,
    UsageQuerySerializer,
)
from .services import movements
from .services.estimate import EstimateRefused, estimate, low_balance_threshold, pricing_values
from .services.plans import get_public_plans


class PublicPlanListView(APIView):
    """Active plans for the pricing page. Public: no token is read, so a stale one cannot break it."""

    authentication_classes = []
    permission_classes = [AllowAny]

    @extend_schema(responses=PublicPlanSerializer(many=True), tags=['plans'])
    def get(self, request):
        return Response(get_public_plans())


class PricingView(APIView):
    """The formula's numbers and the upload limits, to estimate in the browser. Public."""

    authentication_classes = []
    permission_classes = [AllowAny]

    @extend_schema(responses=OpenApiTypes.OBJECT, tags=['billing'])
    def get(self, request):
        return Response(pricing_values())


class _WalletView(APIView):
    """A view of the active workspace's wallet. 404 when the user has no workspace at all."""

    permission_classes = [IsAuthenticated, WorkspaceRolePermission]

    def workspace(self):
        active = resolve_active_workspace(self.request)
        if active is None:
            raise Http404
        return active.workspace


class EstimateView(_WalletView):
    """
    C1, D2, D4, D5: cost, discount, balance after and ETA of a configuration.
    It writes nothing, so every role can ask (``wallet.view``). 403 with
    ``code`` (duration, resolution, seo) when the plan does not allow it.
    """

    workspace_actions = {'POST': 'wallet.view'}

    @extend_schema(
        request=EstimateRequestSerializer, responses={200: OpenApiTypes.OBJECT, 400: OpenApiTypes.OBJECT, 403: OpenApiTypes.OBJECT},
        tags=['billing'],
    )
    def post(self, request):
        body = EstimateRequestSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        data = body.validated_data
        try:
            result = estimate(
                self.workspace(), source=data['source'], duration_seconds=data.get('duration_seconds'),
                render_resolution=data.get('render_resolution'), use_seo=data['use_seo'],
            )
        except EstimateRefused as refused:
            return Response(refused.body, status=refused.status)
        return Response(result)


class WalletView(_WalletView):
    """Topbar, G1, C1: the balance, what was spent this month and what is reserved, per reservation."""

    workspace_actions = {'GET': 'wallet.view'}

    @extend_schema(responses=OpenApiTypes.OBJECT, tags=['billing'])
    def get(self, request):
        workspace = self.workspace()
        # Created with the workspace (payments/signals.py); an old one without it gets an empty one.
        wallet, created = Wallet.objects.get_or_create(workspace=workspace)
        if created:
            wallet.refresh_from_db()  # the balances as the database keeps them, with their ten decimals
        reservations = (
            Transaction.objects.filter(
                wallet=wallet, transaction_type=Transaction.Type.SPEND, status=Transaction.Status.RESERVED,
            )
            .order_by('-created_at', '-id')
            .values_list('id', 'project_id', 'project__title', 'amount', 'created_at')
        )
        now = timezone.now()
        return Response({
            'id': str(wallet.id),
            'workspace': str(workspace.pk),
            'available_balance': movements.money(wallet.available_balance),
            'reserved_balance': movements.money(wallet.reserved_balance),
            'updated_at': wallet.updated_at,
            'spent_this_month': movements.money(movements.spent_in_month(workspace, *movements.current_month(now))),
            'low_balance_threshold': movements.money(low_balance_threshold(workspace.subscription_plan)),
            'reservations': [
                {
                    'transaction_id': str(txn_id),
                    'project_id': str(project_id) if project_id else None,
                    'project_title': title if project_id else None,
                    'amount': movements.money(amount),
                    'created_at': created_at,
                }
                for txn_id, project_id, title, amount, created_at in reservations
            ],
        })


class WalletUsageView(_WalletView):
    """G1: what was charged on each day of ``month`` (default: this one), in projects, renders and SEO."""

    workspace_actions = {'GET': 'billing.manage'}

    @extend_schema(parameters=[UsageQuerySerializer], responses=OpenApiTypes.OBJECT, tags=['billing'])
    def get(self, request):
        query = UsageQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        month = query.validated_data.get('month') or movements.current_month(timezone.now())
        return Response(movements.usage(self.workspace(), *month))


class TransactionPagination(PageNumberPagination):
    page_size = 25
    page_size_query_param = 'page_size'
    max_page_size = 100
    invalid_page_message = "Página inválida."

    def get_paginated_response(self, data):
        response = super().get_paginated_response(data)
        response.data['totals'] = self.totals
        return response

    def get_paginated_response_schema(self, schema):
        response = super().get_paginated_response_schema(schema)
        response['properties']['totals'] = {'type': 'object'}
        return response


class _MovementsView(_WalletView):
    workspace_actions = {'GET': 'billing.manage'}

    def filters(self):
        query = TransactionsQuerySerializer(data=self.request.query_params)
        query.is_valid(raise_exception=True)
        return query.validated_data


class TransactionListView(_MovementsView, ListAPIView):
    """
    G2: the movements, newest first, with ``kind`` and the project linked.
    ``totals`` counts every kind under the same month and project, ignoring
    ``kind`` and the page. Three queries whatever the page size.
    """

    serializer_class = TransactionSerializer
    pagination_class = TransactionPagination

    @extend_schema(parameters=[TransactionsQuerySerializer], tags=['billing'])
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)

    def list(self, request, *args, **kwargs):
        filters = self.filters()
        workspace = self.workspace()
        scoped = movements.movements(workspace, month=filters.get('month'), project=filters.get('project'))
        self.paginator.totals = movements.totals(scoped)
        self.queryset = movements.with_details(movements.movements(workspace, **filters))
        return super().list(request, *args, **kwargs)


class TransactionExportView(_MovementsView):
    """G2 › Exportar: the same movements as CSV, the whole history (no page)."""

    @extend_schema(
        parameters=[TransactionsQuerySerializer],
        responses={(200, 'text/csv'): OpenApiResponse(response=OpenApiTypes.STR)},
        tags=['billing'],
    )
    def get(self, request):
        filters = self.filters()
        rows = movements.with_details(movements.movements(self.workspace(), **filters))
        response = HttpResponse(movements.csv_text(rows), content_type='text/csv; charset=utf-8')
        response['Content-Disposition'] = 'attachment; filename="movimientos.csv"'
        return response
