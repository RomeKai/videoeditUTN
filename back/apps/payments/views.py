# payments views
from drf_spectacular.utils import extend_schema
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from .serializers import PublicPlanSerializer
from .services.plans import get_public_plans


class PublicPlanListView(APIView):
    """Active plans for the pricing page. Public: no token is read, so a stale one cannot break it."""

    authentication_classes = []
    permission_classes = [AllowAny]

    @extend_schema(responses=PublicPlanSerializer(many=True), tags=['plans'])
    def get(self, request):
        return Response(get_public_plans())
