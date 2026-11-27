"""
The bell (J2; front contract ``notifications.*``,
front/src/api/contracts/notifications.ts):

- ``GET  /api/v1/notifications/``: the newest 20 of the user in the active
  workspace (X-Workspace-Id) and how many are unread.
- ``POST /api/v1/notifications/<id>/read/``: marks one as read.
- ``POST /api/v1/notifications/read-all/``: marks all of them as read.

Always the request user's own, in the active workspace, for every role:
someone else's notification, or one of another workspace, is a 404.
"""
from django.http import Http404
from django.utils import timezone
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.permissions import WorkspaceRolePermission
from apps.core.workspace import resolve_active_workspace

from .models import Notification
from .serializers import NotificationListSerializer, NotificationSerializer

PAGE = 20
UNAUTHORIZED = OpenApiResponse(description='Sin sesión.')
NOT_FOUND = OpenApiResponse(description='No existe, es de otra persona o de otro workspace.')


class _NotificationsView(APIView):
    permission_classes = [IsAuthenticated, WorkspaceRolePermission]
    # Reading your own notifications is not a change to the workspace: every role.
    workspace_actions = {'POST': 'project.view'}

    def mine(self):
        active = resolve_active_workspace(self.request)
        if active is None:
            return Notification.objects.none()
        return Notification.objects.filter(user=self.request.user, workspace=active.workspace)


class NotificationListView(_NotificationsView):
    @extend_schema(responses={200: NotificationListSerializer, 401: UNAUTHORIZED, 404: NOT_FOUND}, tags=['notifications'])
    def get(self, request):
        mine = self.mine()
        newest = mine.order_by('-created_at', '-pk')[:PAGE]
        return Response({
            'unread_count': mine.filter(read_at__isnull=True).count(),
            'results': NotificationSerializer(newest, many=True).data,
        })


class NotificationReadView(_NotificationsView):
    @extend_schema(request=None, responses={200: NotificationSerializer, 401: UNAUTHORIZED, 404: NOT_FOUND}, tags=['notifications'])
    def post(self, request, notification_id):
        mine = self.mine().filter(pk=notification_id)
        # Only the first read sets the time.
        mine.filter(read_at__isnull=True).update(read_at=timezone.now())
        notification = mine.first()
        if notification is None:
            raise Http404
        return Response(NotificationSerializer(notification).data)


class NotificationReadAllView(_NotificationsView):
    @extend_schema(request=None, responses={204: None, 401: UNAUTHORIZED, 404: NOT_FOUND}, tags=['notifications'])
    def post(self, request):
        self.mine().filter(read_at__isnull=True).update(read_at=timezone.now())
        return Response(status=status.HTTP_204_NO_CONTENT)
