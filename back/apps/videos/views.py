import os
import logging
import uuid
from decimal import Decimal
from rest_framework import exceptions, viewsets, permissions, parsers, status
from rest_framework.response import Response
from rest_framework.decorators import action
from rest_framework.views import APIView
from django.core.exceptions import ValidationError
from django.db import transaction

# Modelos y Serializers de Videos
from .models import VideoProject, BrandKit
from .serializers import VideoProjectSerializer, BrandKitSerializer
from .tasks import process_initial_ingestion
from .services.segments import (
    InvalidSegmentsError, check_segment_lengths, max_render_segment_seconds, validate_approved_segments,
)

from apps.core.permissions import WorkspaceRolePermission
from apps.core.workspace import resolve_active_workspace
from apps.users.models import Workspace
from apps.payments.models import Transaction
from apps.payments.services import wallet_service

logger = logging.getLogger(__name__)

# Without the header, no workspace of their own and no active membership.
NO_WORKSPACE_ERROR = "El usuario no tiene un Workspace asignado."


def _active_workspace(request):
    """The workspace the request acts in (X-Workspace-Id, see apps.core.workspace), or None."""
    active = resolve_active_workspace(request)
    return active.workspace if active is not None else None


class ActiveWorkspaceMixin:
    """Scopes a viewset to the active workspace and checks the role it has there."""

    permission_classes = [permissions.IsAuthenticated, WorkspaceRolePermission]

    @property
    def active_workspace(self):
        return _active_workspace(self.request)

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context['workspace'] = self.active_workspace
        return context


class BrandKitViewSet(ActiveWorkspaceMixin, viewsets.ModelViewSet):
    serializer_class = BrandKitSerializer
    workspace_actions = {
        'create': 'brandkit.manage',
        'update': 'brandkit.manage',
        'partial_update': 'brandkit.manage',
        'destroy': 'brandkit.manage',
    }

    def get_queryset(self):
        workspace = self.active_workspace
        if workspace is None:
            return BrandKit.objects.none()
        return BrandKit.objects.filter(workspace=workspace)

    def perform_create(self, serializer):
        workspace = self.active_workspace
        if not workspace:
            # The same 400 as creating a project.
            raise exceptions.ValidationError({"error": NO_WORKSPACE_ERROR})
        serializer.save(workspace=workspace)

from .services.render_pricing import pricing_resolution as _pricing_resolution  # noqa: E402


class VideoProjectViewSet(ActiveWorkspaceMixin, viewsets.ModelViewSet):
    serializer_class = VideoProjectSerializer
    parser_classes = (parsers.JSONParser, parsers.MultiPartParser, parsers.FormParser)
    # Reads (list, retrieve, paper-edit-data) are for every role.
    workspace_actions = {
        'create': 'project.create',
        'from_prompt': 'project.create',
        # The web app only renames; any other field is the same role.
        'update': 'project.rename',
        'partial_update': 'project.rename',
        'destroy': 'project.delete',
        'approve_paper_edit': 'project.review',
    }

    def get_queryset(self):
        workspace = self.active_workspace
        if workspace is None:
            return VideoProject.objects.none()
        return VideoProject.objects.filter(workspace=workspace).select_related('uploaded_by').order_by('-created_at')

    def get_serializer_context(self):
        context = super().get_serializer_context()
        prompt = getattr(self, '_pte_prompt', None)
        if prompt is not None:
            context['pte_prompt'] = prompt
        return context

    def destroy(self, request, *args, **kwargs):
        """
        Deletes the project, never with tokens still reserved: while it may
        render or process, or with a creation or render reservation RESERVED,
        it is a 409 (views_clips.project_delete_decision). Nothing would settle
        that reservation once its project is gone. A project stuck for good
        (no worker, nothing for RENDER_STUCK_AFTER_SECONDS, nothing delivered)
        gets its open reservations returned, in the same transaction, and goes.
        Nothing charged comes back.
        """
        from .views_clips import project_delete_decision

        project = self.get_object()  # 404 outside the workspace; an Editor's own projects only
        with transaction.atomic():
            # Locked: an approval or a paid render that starts meanwhile waits for it.
            locked = VideoProject.objects.select_for_update().get(pk=project.pk)
            refusal, refunds = project_delete_decision(locked)
            if refusal is not None:
                return refusal
            try:
                for reservation_id in refunds:
                    wallet_service.rollback_reservation(reservation_id)
            except wallet_service.PaymentError:
                # Settled meanwhile (the pipeline or a sweep): decide again.
                transaction.set_rollback(True)
                return Response({
                    "error": "El proyecto tiene tokens reservados que todavía no se liquidaron. Probá de nuevo en unos minutos.",
                    "code": "reservation_open",
                }, status=409)
            self.perform_destroy(locked)
        if refunds:
            logger.warning("project.deleted_stuck project_id=%s refunded=%d", project.pk, len(refunds))
        return Response(status=status.HTTP_204_NO_CONTENT)

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
                expiration_seconds=7200,
                request=request,
            )
        
        from .serializers import VideoClipSerializer
        clips = project.clips.all()
        clips_serializer = VideoClipSerializer(clips, many=True, context={'request': request})

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
        """
        Approves the review (D2): every approved segment is rendered as its own
        clip (D3). The render of all of them is priced and reserved before
        anything is queued, and settled by the seconds delivered once the clips
        finish (finalize_project_render), like "Renderizar sin revisar".
        """
        from .tasks.ingestion import _is_whole_video

        project = self.get_object()
        
        if project.status != VideoProject.Status.AWAITING_APPROVAL:
            return Response({"error": f"El proyecto no está en espera de aprobación. Estado actual: {project.status}"}, status=400)
        if _is_whole_video(project):
            # Rendered whole right after ingestion: it has nothing to review.
            return Response({"error": "Un video completo se renderiza entero: no pasa por la revisión."}, status=400)

        approved_segments = request.data.get('approved_segments', [])
        if not approved_segments or not isinstance(approved_segments, list):
            return Response({"error": "Debe proporcionar una lista de 'approved_segments'."}, status=400)

        # Each segment renders as one clip: one too long to finish before the
        # clip's soft time limit is refused here, before anything is reserved.
        max_seconds = max_render_segment_seconds()
        try:
            approved_segments = validate_approved_segments(approved_segments)
            check_segment_lengths(approved_segments, max_seconds)
        except InvalidSegmentsError as e:
            if e.kind == "timestamps":
                return Response({"error": "Los timestamps deben ser valores numéricos (float)."}, status=400)
            if e.kind == "too_long":
                return Response({"error": (
                    "Los segmentos aprobados son inválidos: cada segmento puede durar hasta "
                    f"{int(max_seconds)} segundos. Dividí los más largos en varios."
                )}, status=400)
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

        from .services.ai.pipeline_state import PipelineStateError

        # The claim, the reservation and the approval are one transaction: a
        # request that dies before its commit (a killed worker, a dropped
        # connection) leaves nothing behind, because the database rolls it all
        # back. The project waits for the review again and nothing stays
        # reserved. The claim comes first: two concurrent approvals must not
        # both reserve and render. Its row lock makes the other one wait, and
        # only the request that moves the project out of AWAITING_APPROVAL
        # goes on.
        outcome = {}
        tx = None
        try:
            with transaction.atomic():
                claimed = VideoProject.objects.filter(
                    pk=project.pk, status=VideoProject.Status.AWAITING_APPROVAL,
                ).update(status=VideoProject.Status.RENDERING)
                if not claimed:
                    return Response({"error": "El proyecto ya se está renderizando."}, status=409)
                try:
                    # Centralización en wallet_service para integridad financiera
                    tx = wallet_service.reserve_funds(
                        wallet_id=workspace.wallet.id,
                        amount=final_cost,
                        user=request.user,
                        description=f"Render de los clips aprobados: {project.title}"[:255],
                        project=project,
                        category=Transaction.Category.RENDER,
                    )
                except (wallet_service.InsufficientFundsError, ValidationError) as e:
                    transaction.set_rollback(True)  # the claim goes back
                    return Response({"error": str(e), "required": final_cost}, status=402)
                except Exception as e:
                    transaction.set_rollback(True)
                    logger.error("render.approval_reserve_failed project_id=%s error=%s", project.pk, type(e).__name__)
                    return Response({"error": "Error procesando el pago."}, status=500)
                _approve_clips(project.pk, approved_segments, total_duration, tx.id, outcome)
        except Exception as exc:
            if outcome.get('committed'):
                # Raised after the approval was saved (its dispatch): it stays
                # approved and paid, and its clips are recovered if not queued.
                logger.error(
                    "render.approval_after_commit_failed project_id=%s error=%s", project.pk, type(exc).__name__,
                )
                return self._approved_response(project, final_cost, tx)
            # Rolled back: no claim, no reservation, no clip changed. The
            # project waits for the review again.
            logger.error("render.approval_failed project_id=%s error=%s", project.pk, type(exc).__name__)
            if isinstance(exc, PipelineStateError):
                # It left the review meanwhile (another worker moved its stage).
                return Response({"error": "El proyecto ya se está renderizando."}, status=409)
            raise

        return self._approved_response(project, final_cost, tx)

    @staticmethod
    def _approved_response(project, final_cost, tx):
        return Response({
            "message": "Edición aprobada. Iniciando renderizado de alta calidad...",
            "project_id": project.id,
            "final_cost": final_cost,
            "transaction_id": tx.id
        }, status=200)

    def create(self, request, *args, **kwargs):
        user = request.user
        workspace = self.active_workspace

        if not workspace:
            return Response({"error": NO_WORKSPACE_ERROR}, status=400)

        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            # A failure here leaves neither the row nor the upload (serializer.create).
            project = serializer.save(workspace=workspace, uploaded_by=user, status=VideoProject.Status.UPLOADED)
        except Exception as e:
            # Never the error's text: it can carry storage paths and database details.
            logger.error("project.save_failed error=%s", type(e).__name__)
            return Response({"error": SAVE_FAILED_ERROR}, status=500)

        from apps.payments.services.pricing_engine import PlanLimitExceededError, FeatureNotAllowedError
        from .services.render_pricing import NewProjectRefused, price_new_project

        cost_in_tokens = Decimal('0.0')
        duration_seconds = 0
        plan = workspace.subscription_plan
        use_seo = bool(project.metadata.get('use_seo'))

        # From here on, every refusal goes through _discard_new_project: the
        # row goes, and so does the upload it saved. The price is the one
        # POST pricing/estimate/ shows (price_new_project).
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

                cost_in_tokens = price_new_project(
                    plan=plan, source='file', resolution=_pricing_resolution(project),
                    duration_seconds=duration_seconds, use_seo=use_seo,
                    use_ai_thumbnail=bool(project.metadata.get('use_ai_thumbnail')),
                ).total
                # What was priced, kept apart from source_media (a PATCH of the
                # file replaces that): a whole-video edit never renders more.
                project.metadata = {**(project.metadata or {}), 'priced_duration_s': duration_seconds}

            except (PlanLimitExceededError, FeatureNotAllowedError) as e:
                _discard_new_project(project)
                return Response({"error": str(e)}, status=status.HTTP_403_FORBIDDEN)
            except NewProjectRefused as refused:
                _discard_new_project(project)
                return Response(refused.body, status=status.HTTP_400_BAD_REQUEST)
            except Exception as e:
                _discard_new_project(project)
                logger.error("Error calculando costo V3 project_id=%s error=%s", project.pk, type(e).__name__)
                return Response({"error": "Error interno al procesar el presupuesto del video."}, status=status.HTTP_400_BAD_REQUEST)

        elif project.video_url:
            # The duration of a URL video is unknown here: a flat price, only
            # in 720p, and only the SEO add-on on top (ADR-013).
            try:
                cost_in_tokens = price_new_project(
                    plan=plan, source='link', resolution=_pricing_resolution(project), use_seo=use_seo,
                ).total
            except NewProjectRefused as refused:
                _discard_new_project(project)
                return Response(refused.body, status=status.HTTP_400_BAD_REQUEST)
            except (PlanLimitExceededError, FeatureNotAllowedError) as e:
                _discard_new_project(project)
                return Response({"error": str(e)}, status=status.HTTP_403_FORBIDDEN)

        # The reservation and its link to the project (ADR-011 D-M5: the
        # pipeline refunds it from metadata['reservation_id']) are one
        # transaction, and the ingestion is queued once it commits: the worker
        # always finds both. outcome['not_queued'] says what happened if the
        # broker did not take it.
        outcome = {}
        try:
            with transaction.atomic():
                # Asegurar que el workspace tenga billetera (autocuración)
                from apps.payments.models import Wallet
                wallet, _ = Wallet.objects.get_or_create(workspace=workspace)

                tx = wallet_service.reserve_funds(
                    wallet_id=wallet.id,
                    amount=cost_in_tokens,
                    user=user,
                    description=f"Procesamiento: {project.title}"[:255],
                    project=project,
                    category=Transaction.Category.PROJECT,
                )
                project.metadata = {**(project.metadata or {}), 'reservation_id': str(tx.id)}
                VideoProject.objects.filter(pk=project.pk).update(metadata=project.metadata)
                transaction.on_commit(lambda: _queue_ingestion(project.pk, tx.pk, outcome))

        except (wallet_service.InsufficientFundsError, ValidationError) as e:
            _discard_new_project(project)
            return Response({
                "error": str(e),
                "required": cost_in_tokens
            }, status=status.HTTP_402_PAYMENT_REQUIRED)
            
        except Exception as e:
            _discard_new_project(project)
            logger.error("Error reserving funds on create project_id=%s error=%s", project.pk, type(e).__name__)
            return Response({"error": "Error procesando el pago."}, status=500)

        not_queued = outcome.get('not_queued')
        if not_queued is not None:
            # The broker did not take it (_queue_ingestion): the request fails
            # and can be retried. "Nothing was charged" only once the refund
            # is committed.
            refunded = not_queued == NOT_QUEUED_REFUNDED
            return Response({
                "error": INGESTION_NOT_QUEUED_ERROR if refunded else INGESTION_NOT_QUEUED_UNCONFIRMED_ERROR,
                "code": INGESTION_NOT_QUEUED,
                "project_id": project.id,
                "refunded": refunded,
            }, status=status.HTTP_503_SERVICE_UNAVAILABLE)

        response_data = serializer.data
        response_data['cost_details'] = {
            "tokens_reserved": cost_in_tokens,
            "transaction_id": tx.id
        }
        response_data['message'] = "Video subido. Iniciando ingesta multimodal..."

        return Response(response_data, status=status.HTTP_201_CREATED)


# pipeline_error_code of a project whose ingestion could not be queued at
# creation (the broker was down): front/src/lib/error-messages.ts translates it,
# and _run_ingestion never resumes it.
from .tasks.ingestion import INGESTION_NOT_QUEUED  # noqa: E402

INGESTION_NOT_QUEUED_ERROR = "No pudimos empezar a procesar el video y no se cobró nada. Probá de nuevo en unos minutos."
INGESTION_NOT_QUEUED_UNCONFIRMED_ERROR = (
    "No pudimos empezar a procesar el video. Probá de nuevo en unos minutos; "
    "si quedan tokens reservados por este proyecto, escribinos."
)
SAVE_FAILED_ERROR = "No pudimos guardar el proyecto. Probá de nuevo en unos minutos."
# What _fail_unqueued_project could do.
NOT_QUEUED_REFUNDED, NOT_QUEUED_UNCONFIRMED = 'refunded', 'unconfirmed'


def _discard_new_project(project):
    """A creation refused after the INSERT: neither the row nor its upload stays."""
    from .services.storage_service import delete_upload

    upload = project.source_file.name if project.source_file else None
    project_id = project.pk
    project.delete()
    delete_upload(upload, project_id)


def _queue_ingestion(project_id, reservation_id, outcome):
    """
    Queues the ingestion of a new project once its reservation is committed.
    Without the message nothing would ever pick the project up, and its tokens
    would stay reserved: ``_fail_unqueued_project`` fails it now, and
    ``outcome['not_queued']`` says how that went. Never raises: it runs after
    the commit, so an error here must not undo the creation.
    """
    try:
        process_initial_ingestion.delay(project_id)
    except Exception as exc:
        logger.error("project.ingestion_not_queued project_id=%s error=%s", project_id, type(exc).__name__)
        result = _fail_unqueued_project(project_id, reservation_id)
        if result is not None:
            outcome['not_queued'] = result


def _fail_unqueued_project(project_id, reservation_id):
    """
    Fails a new project whose ingestion was not queued, with
    INGESTION_NOT_QUEUED, and returns its reservation. Both in one
    transaction, under the project's row lock: no one ever sees it failed and
    not refunded, or refunded and still running. A late delivery of the
    message finds the code and does not run it (``_run_ingestion``).

    Returns NOT_QUEUED_REFUNDED when that is committed; None when the message
    got through after all and a worker already moved the project on (it stays
    the worker's, with its reservation); NOT_QUEUED_UNCONFIRMED when it could
    not be written: the project stays at ``uploaded`` with its reservation,
    logged as ``project.not_queued_not_recorded`` for a person. Never raises.
    """
    from apps.payments.services.wallet_service import rollback_reservation

    from .services.ai.pipeline_state import PipelineStage, mark_failed, record_error

    try:
        with transaction.atomic():
            if not mark_failed(project_id, expected_stage=PipelineStage.UPLOADED):
                return None
            record_error(project_id, INGESTION_NOT_QUEUED)
            rollback_reservation(reservation_id)
    except Exception as exc:
        logger.error("project.not_queued_not_recorded project_id=%s error=%s", project_id, type(exc).__name__)
        return NOT_QUEUED_UNCONFIRMED
    logger.info("project.not_queued_refunded project_id=%s", project_id)
    return NOT_QUEUED_REFUNDED


def _segment_clip_id(value):
    """The ``clip_id`` a segment sent, as the canonical id string, or None."""
    try:
        return str(uuid.UUID(str(value))) if value is not None else None
    except (TypeError, ValueError, AttributeError):
        return None


def _segment_title(segment):
    """The title a segment sent ('' when none): a non-empty string, cut to the column."""
    from .models import VideoClip

    title = segment.get('title')
    if not isinstance(title, str) or not title.strip():
        return ''
    return title.strip()[:VideoClip._meta.get_field('title').max_length]


def _approve_clips(project_id, segments, total_duration, reservation_id, outcome=None):
    """
    The approval, under the project row lock: one DRAFT clip per approved
    segment, mapped like the review screen does (the approve-paper-edit mock):

    - a segment whose ``clip_id`` is a DRAFT clip of the project updates it
      (start, end, and its title when one is sent); if its bounds change, its
      previews (rows and files) go: they show the old ones;
    - any other segment creates a DRAFT clip (its title, or "Clip N");
    - the DRAFT clips of the project that were not approved are deleted, with
      the files of their previews: the review is the decision point (the AI's
      reasoning stays in ``ai_rationale_log``). One with scheduled posts or
      layers is kept untouched instead (``render.approval_draft_kept``): the
      delete would cascade onto them.

    Clips in any other status are never touched. Then the project moves to
    render_dispatched (status rendering) with the reservation, the clips it
    pays for (``render_clip_ids``) and their seconds (``render_seconds``), and
    one render_clip_task per clip is queued once this commits. An auto-render
    reservation still RESERVED pays for nothing at clips_selected (nothing was
    queued): it is returned, in the same transaction. Its keys, and those of
    one already settled, are dropped. Raises PipelineStateError, with nothing
    written, when the project is no longer where an approval can start.
    ``outcome['committed']`` tells the caller the approval was saved: an
    error after that must not undo it.
    """
    from .models import ClipLayer, ClipPreview, ScheduledPost, VideoClip
    from .services.ai.pipeline_state import (
        STAGE_ORDER, PipelineConflictError, PipelineStage, advance_to,
    )
    from .services.clip_preview import delete_preview_files
    from .tasks.ingestion import (
        APPROVED_RENDER, AUTO_RENDER, _dispatch_approved_clips, _mark_render_dispatched,
        _refund_undispatched_auto_render,
    )

    outcome = outcome if outcome is not None else {}
    with transaction.atomic():
        project = VideoProject.objects.select_for_update().get(pk=project_id)
        if project.status != VideoProject.Status.RENDERING:
            raise PipelineConflictError(f"Project {project_id} is no longer claimed by this approval")
        stage = project.pipeline_stage
        if stage in STAGE_ORDER and STAGE_ORDER.index(stage) < STAGE_ORDER.index(PipelineStage.CLIPS_SELECTED):
            # A row from before the stage machine reached the review all the same.
            stage = advance_to(project_id, stage, PipelineStage.CLIPS_SELECTED)
        if stage != PipelineStage.CLIPS_SELECTED:
            raise PipelineConflictError(f"Project {project_id} is no longer at {PipelineStage.CLIPS_SELECTED}")
        # An auto-render reserved but never dispatched (the bypass was turned
        # off before its dispatch): returned, or the approval would pay twice.
        _refund_undispatched_auto_render(project_id)

        drafts = {
            str(clip.pk): clip
            for clip in VideoClip.objects.select_for_update().filter(
                project_id=project_id, status=VideoClip.Status.DRAFT,
            )
        }
        approved, moved = [], []
        for number, segment in enumerate(segments, start=1):
            # Each draft is approved once: a repeated clip_id creates a new clip.
            clip = drafts.pop(_segment_clip_id(segment.get('clip_id')), None)
            title = _segment_title(segment)
            if clip is None:
                clip = VideoClip.objects.create(
                    project_id=project_id, title=title or f"Clip {number}",
                    start_time=segment['start'], end_time=segment['end'],
                    status=VideoClip.Status.DRAFT,
                )
            else:
                if (clip.start_time, clip.end_time) != (segment['start'], segment['end']):
                    moved.append(str(clip.pk))  # its previews show the old bounds
                clip.start_time, clip.end_time = segment['start'], segment['end']
                fields = ['start_time', 'end_time']
                if title:
                    clip.title = title
                    fields.append('title')
                clip.save(update_fields=fields)
            approved.append(str(clip.pk))

        # A draft left out that has scheduled posts or layers (only the admin
        # creates them) is kept as it is: deleting it would cascade onto them.
        # It is not approved, so nothing renders or pays for it.
        kept = {
            str(pk) for pk in ScheduledPost.objects.filter(video_clip_id__in=list(drafts))
            .values_list('video_clip_id', flat=True)
        } | {
            str(pk) for pk in ClipLayer.objects.filter(clip_id__in=list(drafts)).values_list('clip_id', flat=True)
        }
        for clip_id in sorted(kept):
            logger.warning("render.approval_draft_kept project_id=%s clip_id=%s", project_id, clip_id)
        left_out = [clip_id for clip_id in drafts if clip_id not in kept]

        # The previews of the drafts left out, and of the drafts moved, go with
        # their rows now and their files once this commits. Read under the row
        # lock: a render_preview that saves its file meanwhile waits for this
        # commit, finds its row gone and deletes its own file; one that saved
        # before is in the list.
        previews = ClipPreview.objects.select_for_update().filter(clip_id__in=[*left_out, *moved])
        preview_files = [(pk, key) for pk, key in previews.values_list('pk', 'storage_key') if key]
        previews.delete()
        VideoClip.objects.filter(pk__in=left_out).delete()

        _mark_render_dispatched(project_id)
        metadata = {
            **(project.metadata or {}),
            'final_duration': total_duration,
            # Settled by the seconds the clips deliver (finalize_project_render).
            APPROVED_RENDER.reservation_key: str(reservation_id),
            APPROVED_RENDER.clip_ids_key: approved,
            APPROVED_RENDER.seconds_key: round(total_duration, 3),
        }
        # The notice of an auto-render that could not be paid is answered, and
        # the keys of a settled auto-render go: the approval pays now.
        settled_auto_render = (AUTO_RENDER.reservation_key, AUTO_RENDER.clip_ids_key, AUTO_RENDER.seconds_key)
        for key in ('auto_render_skipped', *settled_auto_render):
            metadata.pop(key, None)
        VideoProject.objects.filter(pk=project_id).update(approved_segments=segments, metadata=metadata)

        # First callback after the commit: from here on, the approval stands.
        transaction.on_commit(lambda: outcome.update(committed=True))
        transaction.on_commit(lambda: _dispatch_approved_clips(project_id, approved))
        if preview_files:
            transaction.on_commit(lambda: delete_preview_files(preview_files))
    logger.info(
        "render.approved project_id=%s clips=%d deleted=%d kept=%d",
        project_id, len(approved), len(left_out), len(kept),
    )
    return approved


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
    permission_classes = [permissions.IsAuthenticated, WorkspaceRolePermission]
    # The preview is free and is how any role watches a clip before its
    # render: the web app asks for one for a Lector too (ClipPreviewDialog).
    workspace_actions = {'GET': 'project.view', 'POST': 'project.view'}

    def _get_clip(self, request, clip_id):
        from django.http import Http404
        from django.shortcuts import get_object_or_404

        from .models import VideoClip

        workspace = _active_workspace(request)
        if workspace is None:
            raise Http404
        return get_object_or_404(
            VideoClip.objects.select_related('project'),
            pk=clip_id, project__workspace=workspace,
        )

    @staticmethod
    def _payload(preview, request=None):
        from django.conf import settings as django_settings

        from .services.clip_preview import preview_url

        return {
            "preview_id": preview.id,
            "clip_id": preview.clip_id,
            "status": preview.status,
            "url": preview_url(preview, request),
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
            self._payload(preview, request),
            status=status.HTTP_202_ACCEPTED if created else status.HTTP_200_OK,
        )

    def get(self, request, clip_id):
        from .services.clip_preview import current_preview

        preview = current_preview(self._get_clip(request, clip_id))
        if preview is None:
            return Response({"error": "No hay vista previa vigente."}, status=status.HTTP_404_NOT_FOUND)
        return Response(self._payload(preview, request))


class PipelineMetricsView(APIView):
    """
    INFRA-02: data of the operations dashboard, for staff only.

    ``GET /api/v1/ops/pipeline-metrics/?hours=24`` returns, for the projects
    created in the window, the p50 / p95 / max duration of each pipeline stage,
    the projects per status, the failure rate and the failures per error code.
    Counts, durations and static codes only (see services.pipeline_metrics).
    """
    permission_classes = [permissions.IsAdminUser]

    def get(self, request):
        from .services.pipeline_metrics import DEFAULT_WINDOW_HOURS, collect

        raw = request.query_params.get('hours', DEFAULT_WINDOW_HOURS)
        try:
            hours = int(raw)
        except (TypeError, ValueError):
            hours = 0
        if not 1 <= hours <= 24 * 90:
            return Response(
                {"hours": ["Debe ser un entero entre 1 y 2160."]},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(collect(hours=hours))
