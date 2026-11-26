from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import ClipPreviewView, PipelineMetricsView, VideoProjectViewSet, BrandKitViewSet
from .views_social import ScheduledPostViewSet
from .views_project_data import ClipDownloadView, ProbeLinkView, ProjectActivityView, ProjectClipsDownloadView

router = DefaultRouter()
router.register(r'projects', VideoProjectViewSet, basename='project')
router.register(r'brand-kits', BrandKitViewSet, basename='brandkit')
router.register(r'scheduled-posts', ScheduledPostViewSet, basename='scheduled-post')

urlpatterns = [
    path('clips/<uuid:clip_id>/preview/', ClipPreviewView.as_view(), name='clip-preview'),
    path('ops/pipeline-metrics/', PipelineMetricsView.as_view(), name='ops-pipeline-metrics'),
    # Before the router: its project detail route would take "probe-link" as an id.
    path('projects/probe-link/', ProbeLinkView.as_view(), name='project-probe-link'),
    path('projects/<uuid:project_id>/activity/', ProjectActivityView.as_view(), name='project-activity'),
    path('projects/<uuid:project_id>/clips/download/', ProjectClipsDownloadView.as_view(), name='project-clips-download'),
    path('clips/<uuid:clip_id>/download/', ClipDownloadView.as_view(), name='clip-download'),
    path('', include(router.urls)),
]