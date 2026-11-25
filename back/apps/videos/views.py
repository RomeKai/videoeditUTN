import os
import logging
from decimal import Decimal
from rest_framework import viewsets, permissions, parsers, status
from rest_framework.response import Response
from rest_framework.decorators import action
from rest_framework.views import APIView
from django.core.exceptions import ValidationError
from django.db import transaction

# Modelos y Serializers de Videos
from .models import VideoProject, BrandKit
from .serializers import VideoProjectSerializer, BrandKitSerializer
from .tasks import process_initial_ingestion
from .services.segments import InvalidSegmentsError, validate_approved_segments

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

from .services.render_pricing import pricing_resolution as _pricing_resolution  # noqa: E402


class VideoProjectViewSet(viewsets.ModelViewSet):
    serializer_class = VideoProjectSerializer
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = (parsers.JSONParser, parsers.MultiPartParser, parsers.FormParser)

    def get_queryset(self):
        return VideoProject.objects.filter(workspace__members=self.request.user).order_by('-created_at')

    def get_serializer_context(self):
        context = super().get_serializer_context()
        prompt = getattr(self, '_pte_prompt', None)
        if prompt is not None:
            context['pte_prompt'] = prompt
        return context

    @action(detail=False, methods=['post'], url_path='from-prompt')
    def from_prompt(self, request):
        """
        Prompt-to-Edit (PTE-01b): same input, validation, auth and token
        reservation as a normal creation, plus ``prompt``. The prompt is parsed
        when ingestion starts, not in this request; the applied config shows up
        in ``metadata['pte']``.
        """
        from .services.ai.prompt_parser import clean_prompt, prompt_max_chars

        raw = request.data.get('prompt')
        if raw is None or not str(raw).strip():
            return Response({"prompt": ["Este campo es obligatorio."]}, status=status.HTTP_400_BAD_REQUEST)
        if len(str(raw).strip()) > prompt_max_chars():
            return Response(
                {"prompt": [f"Máximo {prompt_max_chars()} caracteres."]},
                status=status.HTTP_400_BAD_REQUEST,
            )
        self._pte_prompt = clean_prompt(raw)
        response = self.create(request)
        if response.status_code == status.HTTP_201_CREATED:
            response.data['prompt_config'] = {
                "status": "pending",
                "detail": "Se aplica al iniciar la ingesta; el resultado queda en metadata.pte.",
            }
        return response

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
            return Response({"error": f"El proyecto no está en espera de aprobación. Estado actual: {project.status}"}, status=400)

        approved_segments = request.data.get('approved_segments', [])
        if not approved_segments or not isinstance(approved_segments, list):
            return Response({"error": "Debe proporcionar una lista de 'approved_segments'."}, status=400)

        try:
            approved_segments = validate_approved_segments(approved_segments)
        except InvalidSegmentsError as e:
            if e.kind == "timestamps":
                return Response({"error": "Los timestamps deben ser valores numéricos (float)."}, status=400)
            return Response({"error": (
                "Los segmentos aprobados son inválidos: cada segmento debe tener "
                "'start' y 'end' numéricos con 0 <= start < end."
            )}, status=400)

        total_duration = sum(seg["end"] - seg["start"] for seg in approved_segments)

        from .services.render_pricing import render_cost
        workspace = project.workspace

        try:
            final_cost = render_cost(project, total_duration)
        except Exception as e:
            return Response({"error": f"Error en el motor de precios: {str(e)}"}, status=400)

        # Claim the approval before reserving: two concurrent approvals must not
        # both reserve and render. Only the request that moves the project out of
        # AWAITING_APPROVAL goes on.
        claimed = VideoProject.objects.filter(
            pk=project.pk, status=VideoProject.Status.AWAITING_APPROVAL,
        ).update(status=VideoProject.Status.RENDERING)
        if not claimed:
            return Response({"error": "El proyecto ya se está renderizando."}, status=409)

        def release_claim():
            VideoProject.objects.filter(
                pk=project.pk, status=VideoProject.Status.RENDERING,
            ).update(status=VideoProject.Status.AWAITING_APPROVAL)

        try:
            # Centralización en wallet_service para integridad financiera
            tx = wallet_service.reserve_funds(
                wallet_id=workspace.wallet.id,
                amount=final_cost,
                user=request.user,
                description=f"Render Final (Paper Edit): {project.title}"[:255]
            )
        except (wallet_service.InsufficientFundsError, ValidationError) as e:
            release_claim()
            return Response({"error": str(e), "required": final_cost}, status=402)
        except Exception as e:
            release_claim()
            logger.error(f"Error reserving funds: {e}")
            return Response({"error": "Error procesando el pago."}, status=500)

        with transaction.atomic():
            # Merged into the row as it is now: the metadata read at the start of
            # this request may be stale (a PATCH or the pipeline wrote since).
            project = VideoProject.objects.select_for_update().get(pk=project.pk)
            # Single source of truth for the render task: the typed column.
            project.approved_segments = approved_segments
            project.metadata = {
                **(project.metadata or {}),
                'final_duration': total_duration,
                # Charged when the render is delivered, returned if it fails for good.
                'render_reservation_id': str(tx.id),
            }
            project.status = VideoProject.Status.RENDERING
            project.save(update_fields=['approved_segments', 'metadata', 'status'])

        from .tasks import render_video_segments
        render_video_segments.delay(project.id)

        return Response({
            "message": "Edición aprobada. Iniciando renderizado de alta calidad...",
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
                source_media = project.metadata.get('source_media') or {}
                if source_media.get('duration_s'):
                    # Measured by the upload validation (CORE-05): no second decode.
                    duration_seconds = int(source_media['duration_s'])
                else:
                    from moviepy import VideoFileClip
                    with VideoFileClip(project.source_file.path) as clip:
                        duration_seconds = int(clip.duration)
                
                features_requested = {
                    'seo_optimization': project.metadata.get('use_seo', False),
                    'ai_thumbnail': project.metadata.get('use_ai_thumbnail', False),
                }

                cost_in_tokens = PricingEngine.calculate_render_cost(
                    duration_seconds=duration_seconds,
                    resolution=_pricing_resolution(project),
                    features_requested=features_requested,
                    plan=workspace.subscription_plan
                )
                # What was priced, kept apart from source_media (a PATCH of the
                # file replaces that): a whole-video edit never renders more.
                project.metadata = {**(project.metadata or {}), 'priced_duration_s': duration_seconds}
                
            except (PlanLimitExceededError, FeatureNotAllowedError) as e:
                project.delete()
                return Response({"error": str(e)}, status=status.HTTP_403_FORBIDDEN)
            except Exception as e:
                project.delete()
                logger.error(f"Error calculando costo V3: {e}")
                return Response({"error": "Error interno al procesar el presupuesto del video."}, status=status.HTTP_400_BAD_REQUEST)
        
        elif project.video_url:
            if _pricing_resolution(project) != '720p':
                # The flat link price does not know the duration, so it cannot
                # price a higher tier; an auto-rendered link would get it free.
                project.delete()
                return Response(
                    {"error": "Los proyectos por link se procesan en 720p. Para 1080p o 4K, subí el archivo."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            cost_in_tokens = Decimal('10.0')
            # The duration of a URL video is unknown here: the requested
            # resolution and the SEO add-on are checked against the plan, and
            # only the SEO add-on is charged on top of the flat price (ADR-013).
            use_seo = bool(project.metadata.get('use_seo'))
            try:
                add_on = PricingEngine.calculate_render_cost(
                    duration_seconds=0,
                    resolution=_pricing_resolution(project),
                    features_requested={'seo_optimization': use_seo},
                    plan=workspace.subscription_plan,
                )
                if use_seo:
                    # Without add-ons the engine still returns its 0.01 floor.
                    cost_in_tokens += add_on
            except (PlanLimitExceededError, FeatureNotAllowedError) as e:
                project.delete()
                return Response({"error": str(e)}, status=status.HTTP_403_FORBIDDEN)

        try:
            # Asegurar que el workspace tenga billetera (autocuración)
            from apps.payments.models import Wallet
            wallet, _ = Wallet.objects.get_or_create(workspace=workspace)
            
            tx = wallet_service.reserve_funds(
                wallet_id=wallet.id,
                amount=cost_in_tokens,
                user=user,
                description=f"Procesamiento: {project.title}"[:255]
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

        # Links the reservation to the project so the pipeline can refund it when
        # moderation stops the project (ADR-011 D-M5). Written before the task is queued.
        project.metadata = {**(project.metadata or {}), 'reservation_id': str(tx.id)}
        VideoProject.objects.filter(pk=project.pk).update(metadata=project.metadata)

        process_initial_ingestion.delay(project.id)

        response_data = serializer.data
        response_data['cost_details'] = {
            "tokens_reserved": cost_in_tokens,
            "transaction_id": tx.id
        }
        response_data['message'] = "Video subido. Iniciando ingesta multimodal..."

        return Response(response_data, status=status.HTTP_201_CREATED)


class ClipPreviewView(APIView):
    """
    PTE-03: free, temporary 360p preview of a clip.

    - ``POST /api/v1/clips/{id}/preview/`` queues a preview (202), or returns the
      one already in progress for the clip (200). It never reserves tokens.
      429 when the workspace already has PREVIEW_MAX_ACTIVE_PER_WORKSPACE
      previews in progress; 409 when the project has no source video yet.
    - ``GET`` returns the newest preview that has not expired, with a temporary
      URL once it is ready; 404 when there is none.
    """
    permission_classes = [permissions.IsAuthenticated]

    def _get_clip(self, request, clip_id):
        from django.shortcuts import get_object_or_404

        from .models import VideoClip

        return get_object_or_404(
            VideoClip.objects.select_related('project'),
            pk=clip_id, project__workspace__members=request.user,
        )

    @staticmethod
    def _payload(preview):
        from django.conf import settings as django_settings

        from .services.clip_preview import preview_url

        return {
            "preview_id": preview.id,
            "clip_id": preview.clip_id,
            "status": preview.status,
            "url": preview_url(preview),
            "expires_at": preview.expires_at,
            "error_code": preview.error_code or None,
            "cost_tokens": 0,
            "simplified": {
                "height": getattr(django_settings, 'PREVIEW_HEIGHT', 360),
                "face_tracking": False,
                "silence_removal": False,
            },
        }

    def post(self, request, clip_id):
        from .services.clip_preview import PreviewLimitExceeded, request_preview

        clip = self._get_clip(request, clip_id)
        if not clip.project.source_file:
            return Response(
                {"error": "El video fuente todavía no está disponible."},
                status=status.HTTP_409_CONFLICT,
            )
        from .tasks.ingestion import _is_whole_video

        if _is_whole_video(clip.project):
            # Its real render is dispatched right away and paid by the project.
            return Response(
                {"error": "El video completo se renderiza entero: no tiene vista previa."},
                status=status.HTTP_409_CONFLICT,
            )
        try:
            preview, created = request_preview(clip, request.user)
        except PreviewLimitExceeded:
            return Response(
                {"error": "Ya hay vistas previas en proceso en este espacio. Probá en unos segundos."},
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )
        return Response(
            self._payload(preview),
            status=status.HTTP_202_ACCEPTED if created else status.HTTP_200_OK,
        )

    def get(self, request, clip_id):
        from .services.clip_preview import current_preview

        preview = current_preview(self._get_clip(request, clip_id))
        if preview is None:
            return Response({"error": "No hay vista previa vigente."}, status=status.HTTP_404_NOT_FOUND)
        return Response(self._payload(preview))
