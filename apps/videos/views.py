import os
import math # Necesario para redondear minutos hacia arriba
from rest_framework import viewsets, permissions, parsers, status
from rest_framework.response import Response
from moviepy.editor import VideoFileClip

# Modelos
from apps.videos.models import VideoProject, BrandKit
from apps.users.models import Workspace
from apps.payments.models import Transaction

# Serializers
from apps.videos.api.serializers import VideoProjectSerializer, BrandKitSerializer

# Tareas
from apps.videos.tasks import process_video_pipeline 
# (Asegúrate de tener importada la PricingConfig si la usas, o definimos la regla aquí)

class BrandKitViewSet(viewsets.ModelViewSet):
    serializer_class = BrandKitSerializer
    permission_classes = [permissions.IsAuthenticated]
    
    def get_queryset(self):
        # CAMBIO: Ahora filtramos por workspaces donde el usuario es miembro
        return BrandKit.objects.filter(workspace__members=self.request.user)

    def perform_create(self, serializer):
        # CAMBIO: Asignamos al workspace personal por defecto si no se especifica
        # (Idealmente deberías recibir workspace_id, pero para MVP esto sirve)
        workspace = self.request.user.owned_workspaces.first()
        serializer.save(workspace=workspace)


class VideoProjectViewSet(viewsets.ModelViewSet):
    serializer_class = VideoProjectSerializer
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = (parsers.MultiPartParser, parsers.FormParser)

    def get_queryset(self):
        # El usuario ve proyectos de sus equipos (Workspaces)
        return VideoProject.objects.filter(workspace__members=self.request.user).order_by('-created_at')

    def create(self, request, *args, **kwargs):
        """
        Lógica de Cobro: 1 Token = 1 Minuto.
        """
        user = request.user
        
        # 1. DETECTAR WORKSPACE (Contexto de Agencia)
        workspace_id = request.data.get('workspace_id')
        if workspace_id:
            try:
                workspace = Workspace.objects.get(id=workspace_id, members=user)
            except Workspace.DoesNotExist:
                return Response({"error": "No tienes acceso a este Workspace"}, status=403)
        else:
            # Fallback: Usar su espacio personal
            workspace = user.owned_workspaces.first()
            if not workspace:
                return Response({"error": "Usuario sin Workspace asignado"}, status=400)

        # 2. VALIDACIÓN INICIAL DE DATOS
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        # 3. GUARDADO TEMPORAL (Para poder leer el archivo en disco)
        try:
            project = serializer.save(
                workspace=workspace, 
                uploaded_by=user, 
                status='pending'
            )
        except Exception as e:
            return Response({"error": "Error al guardar archivo"}, status=500)

        # 4. CÁLCULO DE COSTO (TOKENS POR MINUTO)
        file_path = project.source_file.path
        try:
            # Leemos la duración sin cargar todo el video a RAM
            clip = VideoFileClip(file_path)
            duration_seconds = clip.duration
            clip.close() 

            # --- REGLA DE NEGOCIO ACTUALIZADA ---
            # 120 min video -> 120 Tokens
            # 1 min 10 seg -> 2 Tokens (Redondeo hacia arriba)
            duration_minutes = math.ceil(duration_seconds / 60)
            
            # Costo: 1 Token por minuto. Mínimo 1 token.
            cost_in_tokens = max(1, duration_minutes)

        except Exception as e:
            project.delete() # Borramos basura si falla
            return Response({"error": "El archivo de video es ilegible"}, status=400)

        # 5. TRANSACCIÓN FINANCIERA (Reserva de Fondos)
        try:
            description = f"Procesamiento: {project.title} ({duration_minutes} min)"
            
            # Intentamos cobrar al Workspace
            tx = Transaction.reserve_funds(
                workspace=workspace,
                user=user,
                amount=cost_in_tokens,
                description=description
            )
        except Exception: # ValidationError por falta de saldo
            project.delete()
            if os.path.exists(file_path): os.remove(file_path)
            
            return Response({
                "error": "Saldo insuficiente en el Workspace.",
                "required_tokens": cost_in_tokens,
                "current_balance": workspace.wallet.balance
            }, status=status.HTTP_402_PAYMENT_REQUIRED)

        # 6. ÉXITO: Lanzar tarea a Celery
        # Pasamos el ID del proyecto y de la transacción para confirmarla al terminar
        process_video_pipeline.delay(project.id, tx.id)

        # Respuesta al Frontend
        response_data = serializer.data
        response_data['cost_details'] = {
            "duration_minutes": duration_minutes,
            "tokens_deducted": cost_in_tokens
        }
        
        return Response(response_data, status=status.HTTP_201_CREATED)