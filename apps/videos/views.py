from rest_framework import viewsets, permissions, parsers
from .models import VideoProject, BrandKit
from .serializers import VideoProjectSerializer, BrandKitSerializer

class BrandKitViewSet(viewsets.ModelViewSet):
    serializer_class = BrandKitSerializer
    permission_classes = [permissions.IsAuthenticated]
    
    # Hacemos que el usuario solo vea SUS propios BrandKits
    def get_queryset(self):
        return BrandKit.objects.filter(user=self.request.user)

    # Al crear, asignamos el usuario automáticamente desde el token
    def perform_create(self, serializer):
        serializer.save(user=self.request.user)


class VideoProjectViewSet(viewsets.ModelViewSet):
    serializer_class = VideoProjectSerializer
    permission_classes = [permissions.IsAuthenticated]
    # Necesario para subir archivos (Multipart)
    parser_classes = (parsers.MultiPartParser, parsers.FormParser)

    def get_queryset(self):
        return VideoProject.objects.filter(user=self.request.user).order_by('-created_at')

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)