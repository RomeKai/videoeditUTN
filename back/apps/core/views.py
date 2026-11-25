from django.conf import settings
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView


class SystemStatusView(APIView):
    """Public: whether the platform is in maintenance. Reads MAINTENANCE_MODE from settings, off by default."""

    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request):
        return Response({
            'maintenance': getattr(settings, 'MAINTENANCE_MODE', False),
            'maintenance_window': None,
        })
