from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import ClipPreviewView, PipelineMetricsView, VideoProjectViewSet, BrandKitViewSet
from .views_clips import (
    ClipDetailView, ClipRenderView, ClipRetryView, ProjectRedispatchView, ProjectRerenderView,
)

router = DefaultRouter()
router.register(r'projects', VideoProjectViewSet, basename='project')
router.register(r'brand-kits', BrandKitViewSet, basename='brandkit')

urlpatterns = [
    path('clips/<uuid:clip_id>/preview/', ClipPreviewView.as_view(), name='clip-preview'),
    path('clips/<uuid:clip_id>/', ClipDetailView.as_view(), name='clip-detail'),
    path('clips/<uuid:clip_id>/render/', ClipRenderView.as_view(), name='clip-render'),
    path('clips/<uuid:clip_id>/retry/', ClipRetryView.as_view(), name='clip-retry'),
    path('projects/<uuid:pk>/rerender/', ProjectRerenderView.as_view(), name='project-rerender'),
    path('projects/<uuid:pk>/redispatch/', ProjectRedispatchView.as_view(), name='project-redispatch'),
    path('ops/pipeline-metrics/', PipelineMetricsView.as_view(), name='ops-pipeline-metrics'),
    path('', include(router.urls)),
]