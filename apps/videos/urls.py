from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import VideoProjectViewSet, BrandKitViewSet

router = DefaultRouter()
router.register(r'projects', VideoProjectViewSet, basename='project')
router.register(r'brand-kits', BrandKitViewSet, basename='brandkit')

urlpatterns = [
    path('', include(router.urls)),
]