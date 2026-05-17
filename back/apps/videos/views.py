import os
import math
import logging
from decimal import Decimal
from rest_framework import viewsets, permissions, parsers, status
from rest_framework.response import Response
from django.core.exceptions import ValidationError # Para capturar el error de saldo


# Modelos y Serializers de Videos
from .models import VideoProject, BrandKit
from .serializers import VideoProjectSerializer, BrandKitSerializer
from .tasks import process_initial_ingestion

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

        # 3. GUARDADO INICIAL (Status: Uploaded)
        try:
            project = serializer.save(workspace=workspace, uploaded_by=user, status=VideoProject.Status.UPLOADED)
        except Exception as e:
            return Response({"error": f"Error al guardar proyecto: {str(e)}"}, status=500)

        # 4. CÁLCULO DE COSTO DINÁMICO (FINOPS V3)
        from apps.payments.services.pricing_engine import PricingEngine, PlanLimitExceededError, FeatureNotAllowedError
        
        cost_in_tokens = Decimal('0.0')
        duration_seconds = 0

        # CASO A: Archivo Local
        if project.source_file:
            try:
                from moviepy import VideoFileClip
                with VideoFileClip(project.source_file.path) as clip:
                    duration_seconds = int(clip.duration)
                
                # Detectamos features solicitadas (estos campos vendrán del frontend o metadata)
                features_requested = {
                    'seo_optimization': project.metadata.get('use_seo', False),
                    'ai_thumbnail': project.metadata.get('use_ai_thumbnail', False),
                }

                # Calculamos costo usando el motor oficial V3
                cost_in_tokens = PricingEngine.calculate_render_cost(
                    duration_seconds=duration_seconds,
                    resolution=project.aspect_ratio, # '720p', '1080p', etc.
                    features_requested=features_requested,
                    plan=workspace.subscription_plan
                )
                
            except (PlanLimitExceededError, FeatureNotAllowedError) as e:
                project.delete()
                return Response({"error": str(e)}, status=status.HTTP_403_FORBIDDEN)
            except Exception as e:
                project.delete()
                logger.error(f"Error calculando costo V3: {e}")
                return Response({"error": "Error interno al procesar el presupuesto del video."}, status=status.HTTP_400_BAD_REQUEST)
        
        # CASO B: URL (YouTube/Vimeo)
        elif project.video_url:
            # Para URLs, reservamos un costo base de 10 minutos (provisional)
            # El worker ajustará el costo real tras la descarga
            cost_in_tokens = Decimal('10.0')

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
        process_initial_ingestion.delay(project.id)

        # Respuesta API
        response_data = serializer.data
        response_data['cost_details'] = {
            "tokens_reserved": cost_in_tokens,
            "transaction_id": tx.id
        }
        response_data['message'] = "Video subido. Iniciando ingesta multimodal..."

        return Response(response_data, status=status.HTTP_201_CREATED)