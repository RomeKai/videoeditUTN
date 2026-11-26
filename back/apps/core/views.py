from django.conf import settings
from drf_spectacular.utils import extend_schema
from rest_framework import serializers
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from .maintenance import maintenance_window


class MaintenanceWindowSerializer(serializers.Serializer):
    starts_at = serializers.DateTimeField()
    ends_at = serializers.DateTimeField()


class SystemStatusSerializer(serializers.Serializer):
    maintenance = serializers.BooleanField()
    maintenance_window = MaintenanceWindowSerializer(allow_null=True)


class SystemStatusView(APIView):
    """
    Public: whether the platform is in maintenance and its estimated window
    (apps.core.maintenance). It keeps answering 200 during maintenance, while
    the rest of the API answers 503.
    """

    authentication_classes = []
    permission_classes = [AllowAny]

    @extend_schema(
        summary='Si la plataforma está en mantenimiento y hasta cuándo.',
        responses={200: SystemStatusSerializer},
    )
    def get(self, request):
        maintenance = settings.MAINTENANCE_MODE
        return Response(SystemStatusSerializer({
            'maintenance': maintenance,
            'maintenance_window': maintenance_window() if maintenance else None,
        }).data)
