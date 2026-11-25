from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import ClipPreviewView, PipelineMetricsView, VideoProjectViewSet, BrandKitViewSet

router = DefaultRouter()
router.register(r'projects', VideoProjectViewSet, basename='project')
router.register(r'brand-kits', BrandKitViewSet, basename='brandkit')

urlpatterns = [
    path('clips/<uuid:clip_id>/preview/', ClipPreviewView.as_view(), name='clip-preview'),
    path('ops/pipeline-metrics/', PipelineMetricsView.as_view(), name='ops-pipeline-metrics'),
    path('', include(router.urls)),
]