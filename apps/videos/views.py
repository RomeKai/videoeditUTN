import os
import math
from rest_framework import viewsets, permissions, parsers, status
from rest_framework.response import Response

# --- CAMBIO V2.0: Importación directa desde la raíz ---
from moviepy import VideoFileClip 

from apps.videos.models import VideoProject, BrandKit
from apps.users.models import Workspace
from apps.payments.models import Transaction
from apps.videos.serializers import VideoProjectSerializer, BrandKitSerializer
from apps.videos.tasks import process_video_pipeline 

class BrandKitViewSet(viewsets.ModelViewSet):
    serializer_class = BrandKitSerializer
    permission_classes = [permissions.IsAuthenticated]
    
    def get_queryset(self):
        return BrandKit.objects.filter(workspace__members=self.request.user)

    def perform_create(self, serializer):
        workspace = self.request.user.owned_workspaces.first()
        serializer.save(workspace=workspace)


class VideoProjectViewSet(viewsets.ModelViewSet):
    serializer_class = VideoProjectSerializer
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = (parsers.MultiPartParser, parsers.FormParser)

    def get_queryset(self):
        return VideoProject.objects.filter(workspace__members=self.request.user).order_by('-created_at')

    def create(self, request, *args, **kwargs):
        user = request.user
        
        # 1. DETECTAR WORKSPACE
        workspace_id = request.data.get('workspace_id')
        if workspace_id:
            try:
                workspace = Workspace.objects.get(id=workspace_id, members=user)
            except Workspace.DoesNotExist:
                return Response({"error": "No tienes acceso a este Workspace"}, status=403)
        else:
            workspace = user.owned_workspaces.first()
            if not workspace:
                return Response({"error": "Usuario sin Workspace asignado"}, status=400)

        # 2. VALIDAR
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        # 3. GUARDADO TEMPORAL
        try:
            project = serializer.save(workspace=workspace, uploaded_by=user, status='pending')
        except Exception:
            return Response({"error": "Error al guardar archivo"}, status=500)

        # 4. CÁLCULO DE COSTO (Usando MoviePy v2.0)
        file_path = project.source_file.path
        try:
            # En v2.0 esto sigue funcionando igual para leer metadata
            clip = VideoFileClip(file_path)
            duration_seconds = clip.duration
            clip.close() 

            duration_minutes = math.ceil(duration_seconds / 60)
            cost_in_tokens = max(1, duration_minutes)

        except Exception as e:
            project.delete()
            # Logueamos el error para ver si MoviePy se queja de algo más
            print(f"Error MoviePy: {e}") 
            return Response({"error": "El archivo de video es ilegible"}, status=400)

        # 5. COBRO
        try:
            description = f"Procesamiento: {project.title} ({duration_minutes} min)"
            tx = Transaction.reserve_funds(
                workspace=workspace,
                user=user,
                amount=cost_in_tokens,
                description=description
            )
        except Exception: 
            project.delete()
            if os.path.exists(file_path): os.remove(file_path)
            return Response({
                "error": "Saldo insuficiente.",
                "required": cost_in_tokens,
                "balance": workspace.wallet.balance
            }, status=status.HTTP_402_PAYMENT_REQUIRED)

        # 6. ÉXITO
        process_video_pipeline.delay(project.id, tx.id)

        response_data = serializer.data
        response_data['cost_details'] = {
            "duration_minutes": duration_minutes,
            "tokens_deducted": cost_in_tokens
        }
        return Response(response_data, status=status.HTTP_201_CREATED)