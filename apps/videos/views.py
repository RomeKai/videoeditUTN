import os
import math
from rest_framework import viewsets, permissions, parsers, status
from rest_framework.response import Response
from django.core.exceptions import ValidationError # Para capturar el error de saldo


# Modelos y Serializers de Videos
from .models import VideoProject, BrandKit
from .serializers import VideoProjectSerializer, BrandKitSerializer
from .tasks import process_video_pipeline

from apps.users.models import Workspace
from apps.payments.models import Transaction

class BrandKitViewSet(viewsets.ModelViewSet):
    serializer_class = BrandKitSerializer
    permission_classes = [permissions.IsAuthenticated]
    
    def get_queryset(self):
        # Filtra BrandKits donde el usuario es miembro del workspace
        return BrandKit.objects.filter(workspace__members=self.request.user)

    def perform_create(self, serializer):
        # Asigna al primer workspace del usuario (puedes mejorar esto luego)
        workspace = self.request.user.workspaces.first()
        if not workspace:
            raise ValidationError("No tienes un Workspace para crear BrandKits.")
        serializer.save(workspace=workspace)

class VideoProjectViewSet(viewsets.ModelViewSet):
    serializer_class = VideoProjectSerializer
    permission_classes = [permissions.IsAuthenticated]
    # Soportamos JSON (URLs) y Multipart (Archivos)
    parser_classes = (parsers.JSONParser, parsers.MultiPartParser, parsers.FormParser)

    def get_queryset(self):
        return VideoProject.objects.filter(workspace__members=self.request.user).order_by('-created_at')

    def create(self, request, *args, **kwargs):
        user = request.user
        
        # 1. ASIGNACIÓN DE WORKSPACE
        # Intentamos obtener el workspace (asumiendo relación ManyToMany 'workspaces' en User)
        workspace = user.workspaces.first()
        
        if not workspace:
            return Response({"error": "El usuario no tiene un Workspace asignado."}, status=400)

        # 2. VALIDACIÓN DE DATOS
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        # 3. GUARDADO INICIAL (Status: Pending)
        try:
            project = serializer.save(workspace=workspace, uploaded_by=user, status='pending')
        except Exception as e:
            return Response({"error": f"Error al guardar proyecto: {str(e)}"}, status=500)

        # 4. CÁLCULO DE COSTO (TOKENS)
        cost_in_tokens = 0
        duration_minutes = 0

        # CASO A: Archivo Local
        if project.source_file:
            try:
                from moviepy.video.io.VideoFileClip import VideoFileClip
                # Usamos moviepy para leer la duración
                clip = VideoFileClip(project.source_file.path)
                duration_seconds = clip.duration
                clip.close()
                
                # Regla de negocio: 1 Token por minuto (mínimo 1)
                duration_minutes = math.ceil(duration_seconds / 60)
                cost_in_tokens = max(1, duration_minutes)
                
            except Exception as e:
                # Si el archivo está corrupto, limpiamos y fallamos
                project.delete()
                if os.path.exists(project.source_file.path):
                    os.remove(project.source_file.path)
                print(f"Error MoviePy: {e}")
                return Response({"error": "El archivo de video es ilegible o corrupto."}, status=400)
        
        # CASO B: URL (YouTube/Vimeo)
        elif project.video_url:
            # Como no sabemos la duración aún, cobramos 1 token por iniciar
            # El worker puede ajustar el costo después si es necesario
            duration_minutes = 0 
            cost_in_tokens = 1   

        # 5. COBRO (RESERVA DE FONDOS)
        try:
            description = f"Procesamiento: {project.title}"
            
            # Llamamos al método estático que definiste en apps.payments.models
            tx = Transaction.reserve_funds(
                workspace=workspace,
                user=user,
                amount=cost_in_tokens,
                description=description
            )
            
        except ValidationError as e:
            # Capturamos "Saldo insuficiente" definido en tu modelo Transaction
            project.delete()
            if project.source_file and os.path.exists(project.source_file.path):
                os.remove(project.source_file.path)
            
            return Response({
                "error": e.message, # "Saldo insuficiente en el Workspace."
                "required": cost_in_tokens
            }, status=status.HTTP_402_PAYMENT_REQUIRED)
            
        except Exception as e:
            # Cualquier otro error de base de datos
            project.delete()
            return Response({"error": "Error procesando el pago."}, status=500)

        # 6. ENVIAR A CELERY (Exitoso)
        # Pasamos ID del proyecto y ID de la transacción para confirmarla luego
        process_video_pipeline.delay(project.id, tx.id)

        # Respuesta API
        response_data = serializer.data
        response_data['cost_details'] = {
            "tokens_reserved": cost_in_tokens,
            "transaction_id": tx.id
        }
        response_data['message'] = "Proyecto creado. IA procesando..."
        
        return Response(response_data, status=status.HTTP_201_CREATED)