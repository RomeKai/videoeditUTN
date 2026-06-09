import os
import math
import logging
from decimal import Decimal
from rest_framework import viewsets, permissions, parsers, status
from rest_framework.response import Response
from rest_framework.decorators import action
from django.core.exceptions import ValidationError

# Modelos y Serializers de Videos
from .models import VideoProject, BrandKit
from .serializers import VideoProjectSerializer, BrandKitSerializer
from .tasks import process_initial_ingestion

from apps.users.models import Workspace
from apps.payments.models import Transaction
from apps.payments.services import wallet_service

logger = logging.getLogger(__name__)

class BrandKitViewSet(viewsets.ModelViewSet):
    serializer_class = BrandKitSerializer
    permission_classes = [permissions.IsAuthenticated]
    
    def get_queryset(self):
        return BrandKit.objects.filter(workspace__members=self.request.user)

    def perform_create(self, serializer):
        workspace = self.request.user.workspaces.first()
        if not workspace:
            raise ValidationError("No tienes un Workspace para crear BrandKits.")
        serializer.save(workspace=workspace)

class VideoProjectViewSet(viewsets.ModelViewSet):
    serializer_class = VideoProjectSerializer
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = (parsers.JSONParser, parsers.MultiPartParser, parsers.FormParser)

    def get_queryset(self):
        return VideoProject.objects.filter(workspace__members=self.request.user).order_by('-created_at')

    @action(detail=True, methods=['get'], url_path='paper-edit-data')
    def paper_edit_data(self, request, pk=None):
        project = self.get_object()
        from apps.videos.services.storage_service import CloudflareR2Manager
        
        proxy_url = None
        if project.proxy_r2_key:
            proxy_url = CloudflareR2Manager.generate_presigned_url(
                project.proxy_r2_key, 
                expiration_seconds=7200
            )
        
        from .serializers import VideoClipSerializer
        clips = project.clips.all()
        clips_serializer = VideoClipSerializer(clips, many=True)

        return Response({
            "project_id": project.id,
            "title": project.title,
            "status": project.status,
            "proxy_url": proxy_url,
            "transcript": project.transcript_data,
            "ai_suggestions": clips_serializer.data,
            "metadata": project.metadata
        }, status=200)

    @action(detail=True, methods=['post'], url_path='approve-paper-edit')
    def approve_paper_edit(self, request, pk=None):
        project = self.get_object()
        
        if project.status != VideoProject.Status.AWAITING_APPROVAL:
            return Response({"error": f"El proyecto no estÃ¡ en espera de aprobaciÃ³n. Estado actual: {project.status}"}, status=400)

        approved_segments = request.data.get('approved_segments', [])
        if not approved_segments or not isinstance(approved_segments, list):
            return Response({"error": "Debe proporcionar una lista de 'approved_segments'."}, status=400)

        total_duration = 0.0
        try:
            for seg in approved_segments:
                start = float(seg.get('start', 0))
                end = float(seg.get('end', 0))
                if end <= start:
                    return Response({"error": f"Segmento invÃ¡lido: end ({end}) <= start ({start})"}, status=400)
                total_duration += (end - start)
        except (ValueError, TypeError):
            return Response({"error": "Los timestamps deben ser valores numÃ©ricos (float)."}, status=400)

        from apps.payments.services.pricing_engine import PricingEngine
        workspace = project.workspace
        
        features_requested = {
            'seo_optimization': project.metadata.get('use_seo', False),
            'ai_thumbnail': project.metadata.get('use_ai_thumbnail', False),
        }

        try:
            final_cost = PricingEngine.calculate_render_cost(
                duration_seconds=int(math.ceil(total_duration)),
                resolution=project.aspect_ratio,
                features_requested=features_requested,
                plan=workspace.subscription_plan
            )
        except Exception as e:
            return Response({"error": f"Error en el motor de precios: {str(e)}"}, status=400)

        try:
            # CentralizaciÃ³n en wallet_service para integridad financiera
            tx = wallet_service.reserve_funds(
                wallet_id=workspace.wallet.id,
                amount=final_cost,
                user=request.user,
                description=f"Render Final (Paper Edit): {project.title}"
            )
        except (wallet_service.InsufficientFundsError, ValidationError) as e:
            return Response({"error": str(e), "required": final_cost}, status=402)
        except Exception as e:
            logger.error(f"Error reserving funds: {e}")
            return Response({"error": "Error procesando el pago."}, status=500)

        project.metadata['approved_segments'] = approved_segments
        project.metadata['final_duration'] = total_duration
        project.status = VideoProject.Status.RENDERING
        project.save()

        from .tasks import render_video_segments
        render_video_segments.delay(project.id)

        return Response({
            "message": "EdiciÃ³n aprobada. Iniciando renderizado de alta calidad...",
            "project_id": project.id,
            "final_cost": final_cost,
            "transaction_id": tx.id
        }, status=200)

    def create(self, request, *args, **kwargs):
        user = request.user
        workspace = user.workspaces.first()
        
        if not workspace:
            return Response({"error": "El usuario no tiene un Workspace asignado."}, status=400)

        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            project = serializer.save(workspace=workspace, uploaded_by=user, status=VideoProject.Status.UPLOADED)
        except Exception as e:
            return Response({"error": f"Error al guardar proyecto: {str(e)}"}, status=500)

        from apps.payments.services.pricing_engine import PricingEngine, PlanLimitExceededError, FeatureNotAllowedError
        
        cost_in_tokens = Decimal('0.0')
        duration_seconds = 0

        if project.source_file:
            try:
                from moviepy import VideoFileClip
                with VideoFileClip(project.source_file.path) as clip:
                    duration_seconds = int(clip.duration)
                
                features_requested = {
                    'seo_optimization': project.metadata.get('use_seo', False),
                    'ai_thumbnail': project.metadata.get('use_ai_thumbnail', False),
                }

                cost_in_tokens = PricingEngine.calculate_render_cost(
                    duration_seconds=duration_seconds,
                    resolution=project.aspect_ratio,
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
        
        elif project.video_url:
            cost_in_tokens = Decimal('10.0')

        try:
            # Asegurar que el workspace tenga billetera (autocuraciÃ³n)
            from apps.payments.models import Wallet
            wallet, _ = Wallet.objects.get_or_create(workspace=workspace)
            
            tx = wallet_service.reserve_funds(
                wallet_id=wallet.id,
                amount=cost_in_tokens,
                user=user,
                description=f"Procesamiento: {project.title}"
            )
            
        except (wallet_service.InsufficientFundsError, ValidationError) as e:
            project.delete()
            if project.source_file and os.path.exists(project.source_file.path):
                os.remove(project.source_file.path)
            
            return Response({
                "error": str(e),
                "required": cost_in_tokens
            }, status=status.HTTP_402_PAYMENT_REQUIRED)
            
        except Exception as e:
            project.delete()
            logger.error(f"Error reserving funds on create: {e}")
            return Response({"error": "Error procesando el pago."}, status=500)

        process_initial_ingestion.delay(project.id)

        response_data = serializer.data
        response_data['cost_details'] = {
            "tokens_reserved": cost_in_tokens,
            "transaction_id": tx.id
        }
        response_data['message'] = "Video subido. Iniciando ingesta multimodal..."

        return Response(response_data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'], url_path='prompt-edit')
    def prompt_edit(self, request, pk=None):
        """
        Endpoint: User sends a natural language prompt to edit the project.
        Receives: {"prompt": "string"}
        """
        project = self.get_object()
        prompt = request.data.get('prompt')
        
        if not prompt:
            return Response({"error": "Debe proporcionar un 'prompt'."}, status=400)

        from .services.prompt_editor import PromptEditorEngine
        updates = PromptEditorEngine.interpret_edit_prompt(prompt)
        
        if not updates:
            return Response({"message": "No se identificaron cambios para aplicar."}, status=200)

        # Apply updates to the project
        for field, value in updates.items():
            if hasattr(project, field):
                setattr(project, field, value)
        
        project.save()
        
        return Response({
            "message": "Prompt aplicado exitosamente.",
            "applied_updates": updates,
            "project_id": project.id
        }, status=200)
