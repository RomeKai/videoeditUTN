"""
Scheduled posts API (DIST-03, contract `social.posts` / `social.post`).

Scoped to the active workspace through the clip's project. A post of another
workspace answers 404, like the rest of the workspace-scoped endpoints.
"""
from django.db.models import Count
from rest_framework import mixins, viewsets
from rest_framework.pagination import PageNumberPagination

from .models import ScheduledPost
from .serializers_social import PostsQuerySerializer, ScheduledPostSerializer
from .views import ActiveWorkspaceMixin


class ScheduledPostPagination(PageNumberPagination):
    page_size = 25
    page_size_query_param = 'page_size'
    max_page_size = 100

    def get_paginated_response(self, data):
        response = super().get_paginated_response(data)
        response.data['status_counts'] = self.status_counts
        return response


class ScheduledPostViewSet(ActiveWorkspaceMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin,
                           viewsets.GenericViewSet):
    serializer_class = ScheduledPostSerializer
    pagination_class = ScheduledPostPagination

    def base_queryset(self):
        workspace = self.active_workspace
        if workspace is None:
            return ScheduledPost.objects.none()
        return ScheduledPost.objects.filter(video_clip__project__workspace=workspace).select_related(
            'video_clip__project__workspace__subscription_plan',
        )

    def filtered_queryset(self, include_status):
        """The workspace's posts with the query filters; `status` only when asked."""
        query = PostsQuerySerializer(data=self.request.query_params)
        query.is_valid(raise_exception=True)
        params = query.validated_data

        posts = self.base_queryset()
        if include_status and 'status' in params:
            posts = posts.filter(status=params['status'])
        if 'platform' in params:
            posts = posts.filter(platform=params['platform'])
        if 'from' in params:
            posts = posts.filter(publish_at__gte=params['from'])
        if 'to' in params:
            posts = posts.filter(publish_at__lte=params['to'])
        if 'project' in params:
            posts = posts.filter(video_clip__project_id=params['project'])
        if 'clip' in params:
            posts = posts.filter(video_clip_id=params['clip'])
        return posts

    def get_queryset(self):
        if self.action == 'list':
            # `id` breaks ties between posts of the same minute, so pages never repeat a row.
            return self.filtered_queryset(include_status=True).order_by('publish_at', 'id')
        return self.base_queryset()

    def list(self, request, *args, **kwargs):
        # Chips count every status under the same filters, the status one aside.
        counts = dict.fromkeys(ScheduledPost.Status.values, 0)
        for row in self.filtered_queryset(include_status=False).order_by().values('status').annotate(n=Count('id')):
            counts[row['status']] = row['n']
        self.paginator.status_counts = counts
        return super().list(request, *args, **kwargs)
