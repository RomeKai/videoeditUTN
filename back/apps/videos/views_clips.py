"""
Clip and render actions (gap 11 of documents/producto/mapa-de-pantallas.md;
front/src/api/contracts/clips.ts and projects.ts):

- ``GET``, ``PATCH`` and ``DELETE /api/v1/clips/{id}/``: one clip, its title
  and trim, and deleting it (ClipDetailView).
- ``POST /api/v1/clips/{id}/render/``: renders the clip again with its trim
  and the project's settings, from the editor (D4). Paid (ClipRenderView).
- ``POST /api/v1/clips/{id}/retry/``: renders a failed clip again (D3). Paid
  too: its share of the render was returned when it failed (ClipRetryView).
- ``POST /api/v1/projects/{id}/rerender/``: renders every clip again with a
  new style, the whole video of a single_edit included (D5). Paid
  (ProjectRerenderView).
- ``POST /api/v1/projects/{id}/redispatch/``: resumes a failed pipeline from
  its last finished stage (D1). Owner and admin (ProjectRedispatchView).

Money (documents/ia/06-render-caras-y-hablante.md): the three paid renders
reserve ``render_cost`` of their seconds before anything changes, under the
project row lock, and put the project back to render_dispatched with the
reservation, its clips and its seconds (``CLIP_RENDER`` in tasks/ingestion.py).
``finalize_project_render`` settles it by the seconds delivered, like an
approval; recover_stuck_renders and settle_stale_reservations know it. Only a
project that ended (completed, partial or failed) with no render reservation
still RESERVED can start one: a double click, a retry while a render runs or
a rerender while an approval is open get a 409, never a second charge.
"""

import logging
import math

from django.db import transaction
from django.http import Http404
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import permissions, serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.permissions import WorkspaceRolePermission
from apps.core.workspace import resolve_active_workspace
from apps.payments.models import Transaction, Wallet
from apps.payments.services import wallet_service

from .models import ClipPreview, ScheduledPost, VideoClip, VideoProject
from .serializers import ProjectRenderingError, VideoClipSerializer, VideoProjectSerializer

logger = logging.getLogger(__name__)

Status = VideoProject.Status
ClipStatus = VideoClip.Status

# Where a project can start a render the user pays for: it ended.
_ENDED = (Status.COMPLETED, Status.PARTIAL, Status.FAILED)
_ENDED_STAGES = (
    VideoProject.PipelineStage.COMPLETED, VideoProject.PipelineStage.PARTIAL, VideoProject.PipelineStage.FAILED,
)
# The pipeline is choosing or rendering what the clips are.
_PROCESSING = (Status.UPLOADED, Status.INGESTING)
_DELIVERED = (ClipStatus.COMPLETED, ClipStatus.PUBLISHED)

PROJECT_RENDERING = ProjectRenderingError.default_detail
PROJECT_PROCESSING = "El proyecto se está procesando: esperá a que termine."
CLIP_RENDERING = "El clip ya se está renderizando."
RENDER_UNSETTLED = "El render anterior de este proyecto todavía se está liquidando. Probá de nuevo en unos minutos."
INSUFFICIENT_FUNDS = "Saldo insuficiente para reservar los tokens."

# The fields of RerenderRequest (front/src/api/contracts/projects.ts): the
# style. The resolution was priced at creation and never changes.
RERENDER_FIELDS = (
    'editing_style', 'render_layout', 'aspect_ratio', 'brand_kit', 'add_subtitles', 'subtitle_color',
    'subtitle_size', 'subtitle_position', 'subtitle_words_per_segment', 'use_facetracking', 'remove_silences',
)
# A project whose content moderation blocked is re-run by an override in the
# admin (documents/ia/03), never from here: moderating it again blocks it
# again. ModerationBlockedError is not one: it is a bad key or an exhausted
# quota, and the project resumes once operations fix it.
MODERATION_CODES = frozenset({'UnsafeContentError'})
# Failed and refunded because the broker never took its ingestion (the create
# view): the pipeline never resumes it, so it is created again.
INGESTION_NOT_QUEUED = 'IngestionNotQueued'
# A link longer than the app processes with any plan (tasks/shared.py): a
# resume downloads it and fails the same way. LinkTooLongError (over the plan)
# is not one: the plan may have changed since.
LINK_TOO_LONG_FOR_APP = 'LinkTooLongForAppError'
# Where a refunded re-dispatch keeps the error code it replaced.
REDISPATCH_PREVIOUS_CODE_KEY = 'redispatch_previous_error_code'
# Posts that may still publish the clip's file: a render replaces that file
# and deletes the old one.
_PENDING_POSTS = (ScheduledPost.Status.SCHEDULED, ScheduledPost.Status.QUEUED, ScheduledPost.Status.PROCESSING)
# Recovery counters of the previous render: a new paid render starts its own.
_RECOVERY_KEYS = ('render_recovery_requeues', 'render_recovery_requeues_total', 'render_recovery_requeued_at')
# Reservations of the project that a new one replaced in its key: listed so
# every movement of the project stays linked to it.
REPLACED_RESERVATIONS_KEY = 'replaced_reservation_ids'


def _error(code_status, message, code=None, **extra):
    body = {"error": message, **extra}
    if code:
        body["code"] = code
    return Response(body, status=code_status)


class _Refused(Exception):
    """Ends the request inside a transaction: rolls it back and answers ``response``."""

    def __init__(self, response):
        super().__init__(response.status_code)
        self.response = response


class _WorkspaceScoped(APIView):
    permission_classes = [permissions.IsAuthenticated, WorkspaceRolePermission]

    @staticmethod
    def _workspace(request):
        active = resolve_active_workspace(request)
        if active is None:
            raise Http404
        return active.workspace

    def get_clip(self, request, clip_id):
        """The clip, only in the active workspace (another workspace's is a 404)."""
        return get_object_or_404(
            VideoClip.objects.select_related('project'), pk=clip_id, project__workspace=self._workspace(request),
        )

    def get_project(self, request, pk):
        return get_object_or_404(VideoProject, pk=pk, workspace=self._workspace(request))


# --- Shared rules ------------------------------------------------------------

def _render_reservation_open(project):
    """
    A reservation that pays a render of the project is still RESERVED: the
    approval (also one from before), the auto-render, a render asked for
    later and, in a whole video, the creation. While it is, nothing new is
    reserved: its settle would count the same clips again.
    """
    from .tasks.ingestion import PAID_CLIP_RENDERS, _is_whole_video

    metadata = project.metadata or {}
    keys = [paid.reservation_key for paid in PAID_CLIP_RENDERS]
    if _is_whole_video(project):
        keys.append('reservation_id')
    ids = [str(metadata[key]) for key in keys if metadata.get(key)]
    return bool(ids) and Transaction.objects.filter(pk__in=ids, status=Transaction.Status.RESERVED).exists()


def _clip_in_render(project, clip):
    """The clip is being rendered, or waits (DRAFT) for a render that pays for it."""
    from .tasks.ingestion import _paid_clip_renders, _pays_for

    if clip.status == ClipStatus.RENDERING:
        return True
    if clip.status != ClipStatus.DRAFT or project.status != Status.RENDERING:
        return False
    metadata = project.metadata or {}
    return any(_pays_for(metadata, paid, clip.pk) for paid in _paid_clip_renders(metadata))


def _render_refusal(project, clips):
    """
    The 409 of a project that cannot start a paid render now, or None. Read
    under the project row lock.
    """
    if any(_clip_in_render(project, clip) for clip in clips):
        return _error(409, CLIP_RENDERING, 'clip_rendering')
    if project.status == Status.AWAITING_APPROVAL:
        # Approving the review renders (and pays) its clips: rendering one
        # here would charge it now and again at the approval.
        return _error(409, "Los clips de un proyecto en revisión se renderizan al aprobarla.", 'awaiting_approval')
    if project.status == Status.RENDERING:
        return _error(409, PROJECT_RENDERING, 'project_rendering')
    if project.status in _PROCESSING or project.status not in _ENDED or project.pipeline_stage not in _ENDED_STAGES:
        return _error(409, PROJECT_PROCESSING, 'project_processing')
    if _render_reservation_open(project):
        # The fan-out closed and its settle did not run yet (or failed):
        # settle_stale_reservations or the next finalize settles it.
        return _error(409, RENDER_UNSETTLED, 'render_unsettled')
    return None


def _posts_refusal(clips):
    """
    The 409 of a clip with a file that a post may still publish (scheduled,
    queued or uploading): its render would replace that file and delete the
    old one under the post. The post is cancelled or published first.
    """
    with_file = [clip.pk for clip in clips if clip.s3_object_key or clip.status in _DELIVERED]
    if with_file and ScheduledPost.objects.filter(video_clip_id__in=with_file, status__in=_PENDING_POSTS).exists():
        return _error(409, (
            "Este clip tiene publicaciones programadas o en curso: cancelalas o esperá a que se publiquen "
            "antes de renderizarlo de nuevo."
        ), 'posts_pending')
    return None


def project_delete_decision(project):
    """
    What VideoProjectViewSet.destroy may do, read under the project's row lock,
    as ``(refusal, refund_ids)``:

    - Nothing open (no creation or render reservation RESERVED, and it neither
      renders nor processes): ``(None, [])``, deleted.
    - Ended (completed, partial or failed) with a reservation still RESERVED:
      409 ``reservation_open``. The pipeline, recover_stuck_renders or
      settle_stale_reservations settle it soon; nothing would once its project
      is gone (the sweep only reports a reservation without one as unlinked).
    - Uploaded, ingesting or rendering: 409 ``project_processing`` while it may
      be in progress. Once it is stuck (``_stuck``: no worker holds its lock and
      nothing happened for RENDER_STUCK_AFTER_SECONDS) with nothing delivered
      under its open reservations (``_delivered_under``), those reservations
      are returned and it is deleted: ``(None, ids)``. A message lost for good
      would otherwise keep it, and its tokens, forever.
    """
    from .tasks.ingestion import PAID_CLIP_RENDERS

    metadata = project.metadata or {}
    keys = ['reservation_id', *(paid.reservation_key for paid in PAID_CLIP_RENDERS)]
    linked = {key: str(metadata[key]) for key in keys if metadata.get(key)}
    open_rows = dict(
        Transaction.objects.filter(pk__in=list(linked.values()), status=Transaction.Status.RESERVED)
        .values_list('pk', 'created_at')
    ) if linked else {}
    open_keys = [key for key, txn_id in linked.items() if any(str(pk) == txn_id for pk in open_rows)]
    busy = project.status in (Status.RENDERING, *_PROCESSING)
    if not busy and not open_keys:
        return None, []
    if not busy:
        return _error(409, (
            "El proyecto tiene tokens reservados que todavía no se liquidaron. Probá de nuevo en unos minutos."
        ), 'reservation_open'), []
    if _stuck(project, open_rows.values()) and not _delivered_under(project, open_keys):
        return None, [linked[key] for key in open_keys]
    return _error(409, "El proyecto se está procesando: esperá a que termine para borrarlo.", 'project_processing'), []


def _stuck(project, reserved_at):
    """
    No worker holds the project's lock and nothing happened for
    RENDER_STUCK_AFTER_SECONDS (longer than any task can live): no creation,
    reservation, recorded error, capacity wait, render start or re-queue.
    """
    from datetime import timedelta

    from django.conf import settings
    from django.utils import timezone

    from .services.ai.project_lock import project_lock_held
    from .tasks.render_recovery import _when

    try:
        if project_lock_held(project.pk):
            return False
    except Exception:
        return False  # Redis down: it may be running
    metadata = project.metadata or {}
    moments = [project.created_at, project.pipeline_error_at, *reserved_at]
    for key in ('render_clip_started_at', 'render_started_at', 'render_recovery_requeued_at'):
        moments.append(_when(metadata.get(key)))
    moments.append(_when((metadata.get('capacity_wait') or {}).get('since')))
    last = max(moment for moment in moments if moment)
    return timezone.now() - last >= timedelta(seconds=settings.RENDER_STUCK_AFTER_SECONDS)


def _delivered_under(project, open_keys):
    """
    Something the open reservations (``open_keys``, metadata keys) pay for was
    delivered: returning them would give it for free. The rules of their
    settles: the clips of an ingestion, the video of a whole video, the clips
    of a clip-by-clip render (a new file for a render asked for later), the
    export of a paper edit of before.
    """
    from .services.ai.pipeline_state import STAGE_ORDER, PipelineStage
    from .tasks.ingestion import CLIP_RENDER, CLIP_RENDER_PREVIOUS_KEY, _is_whole_video, _paid_clip_renders

    metadata = project.metadata or {}
    clips = VideoClip.objects.filter(project_id=project.pk)
    by_key = {paid.reservation_key: paid for paid in _paid_clip_renders(metadata)}
    for key in open_keys:
        if key == 'reservation_id':
            if _is_whole_video(project):
                if clips.filter(status__in=_DELIVERED).exists():
                    return True
            elif clips.exists() or (
                project.pipeline_stage in STAGE_ORDER
                and STAGE_ORDER.index(project.pipeline_stage) >= STAGE_ORDER.index(PipelineStage.CLIPS_SELECTED)
            ):
                return True
            continue
        paid = by_key.get(key)
        if paid is None:
            # A paper edit of before (no clip ids): its export.
            if project.final_export_r2_key:
                return True
            continue
        ids = metadata.get(paid.clip_ids_key)
        paid_clips = clips if ids is None else clips.filter(pk__in=ids)
        previous = (metadata.get(CLIP_RENDER_PREVIOUS_KEY) or {}) if paid is CLIP_RENDER else {}
        for pk, key_now in paid_clips.filter(status__in=_DELIVERED).values_list('pk', 's3_object_key'):
            if not (str(pk) in previous and key_now == previous[str(pk)].get('key')):
                return True
    return False


def _length_refusal(clips):
    """A 400 for a clip longer than a render finishes within its soft time limit (check_segment_lengths)."""
    from .services.segments import max_render_segment_seconds

    max_seconds = max_render_segment_seconds()
    if any(float(clip.end_time) - float(clip.start_time) > max_seconds for clip in clips):
        return _error(400, (
            f"Cada clip puede durar hasta {int(max_seconds)} segundos para renderizarse. "
            "Acortalo en el editor."
        ))
    return None


def _price(project, seconds):
    """
    ``(cost, None)``, or ``(None, response)`` with the 403 of the plan limit
    (``code``: ``resolution`` or ``duration``, the codes the web app reads).
    """
    from apps.payments.services.pricing_engine import FeatureNotAllowedError, PlanLimitExceededError

    from .services.render_pricing import pricing_resolution, render_cost

    try:
        return render_cost(project, seconds), None
    except (PlanLimitExceededError, FeatureNotAllowedError):
        plan = project.workspace.subscription_plan
        resolution = pricing_resolution(project)
        rank = {'720p': 1, '1080p': 2, '4K': 3}
        if plan is not None and rank.get(resolution, 1) > rank.get(plan.max_resolution, 1):
            return None, _error(403, (
                f"Tu plan {plan.name} renderiza hasta {plan.max_resolution} y este proyecto es {resolution}."
            ), 'resolution')
        limit = plan.max_video_duration_seconds if plan is not None else None
        return None, _error(403, (
            f"Tu plan permite renderizar hasta {limit} segundos por vez. Acortá los clips o cambiá de plan."
        ), 'duration', limit=limit)


def _reserve(project, amount, user, description, category=Transaction.Category.RENDER):
    """
    The reservation, linked to the project with its ``category``, or
    ``_Refused`` with the 402 (the caller's transaction rolls back).
    """
    wallet, _ = Wallet.objects.get_or_create(workspace=project.workspace)
    try:
        return wallet_service.reserve_funds(
            wallet_id=wallet.id, amount=amount, user=user, description=description[:255],
            project=project, category=category,
        )
    except wallet_service.InsufficientFundsError:
        raise _Refused(_error(402, INSUFFICIENT_FUNDS, required=amount))


def _replaced(metadata, key):
    """The ids already replaced, plus the one in ``key`` (a settled reservation a new one replaces)."""
    replaced = list(metadata.get(REPLACED_RESERVATIONS_KEY) or [])
    if metadata.get(key) and str(metadata[key]) not in replaced:
        replaced.append(str(metadata[key]))
    return replaced


def _start_paid_render(request, project_id, pick, description, style=None):
    """
    A render the user pays for, of the clips ``pick(project)`` returns (or a
    Response that refuses it), in one transaction under the project row lock:

    1. the project ended (completed, partial or failed) with no render
       reservation still RESERVED, and no clip is rendering;
    2. each clip fits a render (``check_segment_lengths``) and the plan allows
       their seconds and the resolution (``render_cost``: 403 otherwise);
    3. ``reserve_funds`` (402 without balance: nothing was written);
    4. the style (``rerender``), the clips back to DRAFT, the project back to
       render_dispatched (status rendering) and the CLIP_RENDER keys;
    5. one render_clip_task per clip once it commits (``_dispatch_approved_clips``:
       a clip that cannot be queued is queued by a resume of the pipeline, or by
       recover_stuck_renders).

    Returns ``(project, clips, reservation)`` or the Response that refused it.
    """
    from .services.ai.pipeline_state import PipelineStage, advance
    from .tasks import finalize_project_render
    from .tasks.ingestion import CLIP_RENDER, CLIP_RENDER_PREVIOUS_KEY, _dispatch_approved_clips

    # A fan-out that closed and whose settle did not run (a worker that died
    # in between) is settled first: idempotent, the pipeline's own rule.
    finalize_project_render(project_id)
    try:
        with transaction.atomic():
            project = VideoProject.objects.select_for_update().select_related('workspace').get(pk=project_id)
            picked = pick(project)
            if isinstance(picked, Response):
                raise _Refused(picked)
            ids = [clip.pk for clip in picked]
            clips = list(VideoClip.objects.select_for_update().filter(pk__in=ids, project_id=project_id))
            if len(clips) != len(ids):
                raise _Refused(_error(409, "El clip ya no existe.", 'clip_gone'))
            refusal = _render_refusal(project, clips) or _posts_refusal(clips) or _length_refusal(clips)
            if refusal is not None:
                raise _Refused(refusal)
            seconds = round(sum(max(0.0, float(c.end_time) - float(c.start_time)) for c in clips), 3)
            cost, refusal = _price(project, seconds)
            if refusal is not None:
                raise _Refused(refusal)
            reservation = _reserve(project, cost, request.user, description(project, clips))

            if style:
                VideoProject.objects.filter(pk=project_id).update(**style)
            # The render reads the clip's bounds when it starts: they cannot
            # change while the project renders (ClipDetailView refuses it).
            # Compare-and-set over the locked rows: never a clip someone claimed.
            reset = VideoClip.objects.filter(
                pk__in=ids, status__in=[ClipStatus.DRAFT, ClipStatus.FAILED, *_DELIVERED],
            ).update(status=ClipStatus.DRAFT, render_claim_token=None, render_unfinished_starts=0)
            if reset != len(ids):
                raise _Refused(_error(409, CLIP_RENDERING, 'clip_rendering'))
            advance(project_id, project.pipeline_stage, PipelineStage.RENDER_DISPATCHED)
            metadata = {key: value for key, value in (project.metadata or {}).items() if key not in _RECOVERY_KEYS}
            metadata[REPLACED_RESERVATIONS_KEY] = _replaced(metadata, CLIP_RENDER.reservation_key)
            metadata.update({
                CLIP_RENDER.reservation_key: str(reservation.id),
                CLIP_RENDER.clip_ids_key: [str(pk) for pk in ids],
                CLIP_RENDER.seconds_key: seconds,
                # What the user had: given back if the new render fails, and
                # never counted as delivered by this render.
                CLIP_RENDER_PREVIOUS_KEY: {
                    str(clip.pk): {"status": clip.status, "key": clip.s3_object_key}
                    for clip in clips if clip.status in _DELIVERED and clip.s3_object_key
                },
            })
            VideoProject.objects.filter(pk=project_id).update(
                status=Status.RENDERING, metadata=metadata, pipeline_error_code='', pipeline_error_at=None,
            )
            transaction.on_commit(lambda: _dispatch_approved_clips(project_id, ids))
    except _Refused as refused:
        return refused.response
    logger.info(
        "render.clip_render_reserved project_id=%s clips=%d seconds=%s", project_id, len(ids), seconds,
    )
    project.refresh_from_db()
    return project, clips, reservation


def _paid_response(reservation, body=None):
    return Response({
        **(body or {"message": "Render encolado."}),
        "final_cost": reservation.amount,
        "transaction_id": reservation.id,
    }, status=status.HTTP_202_ACCEPTED)


# --- Clip ---------------------------------------------------------------------

class ClipUpdateSerializer(serializers.Serializer):
    """Body of ``PATCH clips/{id}/`` (UpdateClipRequest): title and trim, each optional."""

    title = serializers.CharField(
        max_length=255, required=False,
        error_messages={
            'blank': "Poné un título para el clip.",
            'null': "Poné un título para el clip.",
            'max_length': "Máximo 255 caracteres.",
            'invalid': "Tiene que ser un texto.",
        },
    )
    start_time = serializers.FloatField(
        required=False, min_value=0,
        error_messages={
            'invalid': "Tiene que ser un número.", 'null': "Tiene que ser un número.",
            'min_value': "El inicio no puede ser negativo.",
        },
    )
    end_time = serializers.FloatField(
        required=False, error_messages={'invalid': "Tiene que ser un número.", 'null': "Tiene que ser un número."},
    )

    def validate(self, data):
        for name in ('start_time', 'end_time'):
            if name in data and not math.isfinite(data[name]):
                raise serializers.ValidationError({name: ["Tiene que ser un número."]})
        return data


class RetryClipResponseSerializer(VideoClipSerializer):
    """Body of ``POST clips/{id}/retry/``, for the OpenAPI schema: the clip (a queued DRAFT) and its reservation."""

    final_cost = serializers.DecimalField(max_digits=20, decimal_places=10, coerce_to_string=False, read_only=True)
    transaction_id = serializers.UUIDField(read_only=True)


def _source_duration(project):
    """Seconds of the source video, when the pipeline or the upload measured them."""
    metadata = project.metadata or {}
    for value in (metadata.get('duration'), (metadata.get('source_media') or {}).get('duration_s'),
                  metadata.get('priced_duration_s')):
        try:
            if value is not None and float(value) > 0:
                return float(value)
        except (TypeError, ValueError):
            continue
    return None


class ClipDetailView(_WorkspaceScoped):
    """
    ``GET clips/{id}/``: the clip as the project sends it (VideoClipSerializer).

    ``PATCH``: title and trim. It never renders (``render/`` does). The title
    can always change: the render does not read it. The trim cannot while the
    project renders or processes (409, ``code: project_rendering`` /
    ``project_processing``), nor while a render reservation that pays for the
    clip is open: what the render reads does not change under it, the rule of
    VideoProjectSerializer.update. 400 for an end at or before the start, past
    the end of the video or longer than a render allows. A new trim deletes
    the clip's previews (rows now, files after the commit): they show the old one.

    ``DELETE``: the clip with its scheduled posts, layers and previews, and its
    files after the commit. 409 while the project renders or processes (a
    delivered clip deleted mid-render would leave the settle short), or while a
    post of it is being uploaded.
    """

    workspace_actions = {'GET': 'project.view', 'PATCH': 'clip.edit', 'DELETE': 'clip.edit'}

    @extend_schema(responses={
        200: VideoClipSerializer, 404: OpenApiResponse(description='No existe o es de otro workspace.'),
    })
    def get(self, request, clip_id):
        return Response(VideoClipSerializer(self.get_clip(request, clip_id)).data)

    @extend_schema(request=ClipUpdateSerializer, responses={
        200: VideoClipSerializer,
        400: OpenApiResponse(description='Por campo: recorte inválido, fuera del video o más largo que un render.'),
        409: OpenApiResponse(description=(
            'El recorte no cambia mientras el proyecto renderiza (`project_rendering`) o se procesa '
            '(`project_processing`).'
        )),
    })
    def patch(self, request, clip_id):
        found = self.get_clip(request, clip_id)
        body = ClipUpdateSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        data = body.validated_data
        preview_files = []
        try:
            with transaction.atomic():
                # The project first, as the approval and the paid renders lock it.
                project = VideoProject.objects.select_for_update().get(pk=found.project_id)
                clip = VideoClip.objects.select_for_update().get(pk=found.pk)
                start = data.get('start_time', clip.start_time)
                end = data.get('end_time', clip.end_time)
                moved = (start, end) != (clip.start_time, clip.end_time)
                if moved:
                    self._check_trim(project, clip, start, end)
                fields = []
                if 'title' in data:
                    clip.title = data['title'].strip() or clip.title
                    fields.append('title')
                if moved:
                    clip.start_time, clip.end_time = start, end
                    fields += ['start_time', 'end_time']
                    previews = ClipPreview.objects.select_for_update().filter(clip_id=clip.pk)
                    preview_files = [(pk, key) for pk, key in previews.values_list('pk', 'storage_key') if key]
                    previews.delete()
                if fields:
                    clip.save(update_fields=fields)
                if preview_files:
                    from .services.clip_preview import delete_preview_files

                    files = list(preview_files)
                    transaction.on_commit(lambda: delete_preview_files(files))
        except _Refused as refused:
            return refused.response
        clip.refresh_from_db()
        return Response(VideoClipSerializer(clip).data)

    @staticmethod
    def _check_trim(project, clip, start, end):
        from .services.segments import max_render_segment_seconds

        if (
            project.status == Status.RENDERING or clip.status == ClipStatus.RENDERING
            or _render_reservation_open(project)
        ):
            raise _Refused(_error(409, PROJECT_RENDERING, 'project_rendering'))
        if project.status in _PROCESSING:
            # An auto-render may have priced these bounds already.
            raise _Refused(_error(409, PROJECT_PROCESSING, 'project_processing'))
        if end <= start:
            raise _Refused(Response({"end_time": ["El fin tiene que ser mayor que el inicio."]}, status=400))
        duration = _source_duration(project)
        if duration is not None and end > duration + 0.5:
            raise _Refused(Response({"end_time": ["El recorte se pasa del final del video."]}, status=400))
        max_seconds = max_render_segment_seconds()
        if end - start > max_seconds:
            raise _Refused(Response(
                {"end_time": [f"El clip puede durar hasta {int(max_seconds)} segundos."]}, status=400,
            ))

    @extend_schema(responses={
        204: None,
        409: OpenApiResponse(description=(
            'El proyecto renderiza o se procesa (`project_rendering`), o un post del clip se sube (`post_processing`).'
        )),
    })
    def delete(self, request, clip_id):
        from .services.clip_preview import delete_preview_files
        from .services.storage_service import CloudflareR2Manager

        found = self.get_clip(request, clip_id)
        with transaction.atomic():
            project = VideoProject.objects.select_for_update().get(pk=found.project_id)
            clip = VideoClip.objects.select_for_update().filter(pk=found.pk).first()
            if clip is None:
                raise Http404
            if (
                project.status in (Status.RENDERING, *_PROCESSING) or clip.status == ClipStatus.RENDERING
                or _render_reservation_open(project)
            ):
                return _error(409, "El proyecto se está renderizando: esperá a que termine para borrar el clip.",
                              'project_rendering')
            if ScheduledPost.objects.filter(video_clip=clip, status=ScheduledPost.Status.PROCESSING).exists():
                return _error(409, "Una publicación de este clip se está subiendo: esperá a que termine para borrarlo.",
                              'post_processing')
            previews = ClipPreview.objects.select_for_update().filter(clip_id=clip.pk)
            preview_files = [(pk, key) for pk, key in previews.values_list('pk', 'storage_key') if key]
            render_key = clip.s3_object_key
            clip.delete()  # its posts, layers and previews go with it

            def delete_files():
                delete_preview_files(preview_files)
                if render_key:
                    try:
                        CloudflareR2Manager.delete_object(render_key)
                    except Exception as exc:
                        logger.warning("clip.file_not_deleted clip_id=%s error=%s", found.pk, type(exc).__name__)

            transaction.on_commit(delete_files)
        logger.info("clip.deleted clip_id=%s project_id=%s", found.pk, found.project_id)
        return Response(status=status.HTTP_204_NO_CONTENT)


class ClipRenderView(_WorkspaceScoped):
    """
    ``POST clips/{id}/render/``: renders the clip again with its trim and the
    project's settings (D4), paid by ``render_cost`` of its seconds. 202 with
    ``{message, final_cost, transaction_id}``; 402 ``{error, required}``; 403
    ``{error, code}`` over the plan; 409 while the clip, its project or a render
    reservation of it is open, or while the project waits for its review.
    """

    workspace_actions = {'POST': 'clip.edit'}

    def post(self, request, clip_id):
        clip = self.get_clip(request, clip_id)
        started = _start_paid_render(
            request, clip.project_id, lambda project: [clip],
            lambda project, clips: f"Render de clip: {clips[0].title}",
        )
        if isinstance(started, Response):
            return started
        _project, _clips, reservation = started
        return _paid_response(reservation)


class ClipRetryView(_WorkspaceScoped):
    """
    ``POST clips/{id}/retry/``: renders a failed clip again (D3). Its share of
    the render was returned when it failed (the settle charges only what was
    delivered), so the retry reserves its render like ``render/``: the clip is
    paid once in all. 202 with the clip plus ``final_cost`` and
    ``transaction_id``; 409 when it is not failed or its project is busy. A
    clip that a render already took or waits for (the second click of a
    double click) is ``clip_rendering``, never ``not_failed``.
    """

    workspace_actions = {'POST': 'clip.edit'}

    @extend_schema(request=None, responses={
        202: RetryClipResponseSerializer,
        400: OpenApiResponse(description='El clip dura más de lo que un render termina: `{ error }`.'),
        402: OpenApiResponse(description='Saldo insuficiente: `{ error, required }`.'),
        403: OpenApiResponse(description='El plan no permite la resolución o la duración: `{ error, code }`.'),
        409: OpenApiResponse(description=(
            '`{ error, code }`: `clip_rendering` (el clip ya está en un render, como el segundo click de un doble '
            'click), `not_failed`, `project_rendering`, `project_processing`, `awaiting_approval`, '
            '`render_unsettled` o `posts_pending`.'
        )),
    })
    def post(self, request, clip_id):
        from .tasks.ingestion import render_queued_rule

        clip = self.get_clip(request, clip_id)

        def pick(project):
            current = VideoClip.objects.filter(pk=clip.pk).values_list('status', flat=True).first()
            if current != ClipStatus.FAILED:
                # Before "not failed": the first click put it back to DRAFT
                # for its render, which may have taken it already.
                if current == ClipStatus.RENDERING or (
                    current == ClipStatus.DRAFT and render_queued_rule(project)(clip.pk)
                ):
                    return _error(409, CLIP_RENDERING, 'clip_rendering')
                return _error(409, "El clip no está en error.", 'not_failed')
            return [clip]

        started = _start_paid_render(
            request, clip.project_id, pick, lambda project, clips: f"Reintento de clip: {clips[0].title}",
        )
        if isinstance(started, Response):
            return started
        _project, _clips, reservation = started
        fresh = VideoClip.objects.get(pk=clip.pk)
        return _paid_response(reservation, VideoClipSerializer(fresh).data)


# --- Project ------------------------------------------------------------------

class ProjectRerenderView(_WorkspaceScoped):
    """
    ``POST projects/{id}/rerender/``: renders every clip of the project again
    with the style in the body (RerenderRequest), paid by ``render_cost`` of
    their seconds. A whole video (single_edit) is its one clip; a clips
    project, the clips it delivered or failed (never a suggested DRAFT). The
    style is validated like the project's PATCH and saved in the same
    transaction as the reservation. 202 ``{message, final_cost,
    transaction_id}``; 402, 403 and 409 as ``clips/{id}/render/``.
    """

    workspace_actions = {'POST': 'project.review'}

    def post(self, request, pk):
        from .tasks.ingestion import _is_whole_video

        project = self.get_project(request, pk)
        raw = request.data if isinstance(request.data, dict) else {}
        style_data = {key: raw[key] for key in RERENDER_FIELDS if key in raw}
        validator = VideoProjectSerializer(
            project, data=style_data, partial=True, context={'workspace': project.workspace, 'request': request},
        )
        validator.is_valid(raise_exception=True)
        style = {key: value for key, value in validator.validated_data.items() if key in RERENDER_FIELDS}

        def pick(locked):
            from .tasks.ingestion import _paid_clip_renders, _pays_for

            metadata = locked.metadata or {}
            paid_renders = _paid_clip_renders(metadata)
            clips = list(VideoClip.objects.filter(project_id=locked.pk).order_by('start_time', 'created_at'))
            if not _is_whole_video(locked):
                # Never a suggestion of a review; a draft a render paid for and
                # never delivered (its render was settled) is one of the clips.
                clips = [
                    clip for clip in clips
                    if clip.status != ClipStatus.DRAFT
                    or any(_pays_for(metadata, paid, clip.pk) for paid in paid_renders)
                ]
            if not clips:
                return _error(409, "El proyecto no tiene clips para renderizar.", 'no_clips')
            return clips

        started = _start_paid_render(
            request, project.pk, pick, lambda locked, clips: f"Render con otro estilo: {locked.title}", style=style,
        )
        if isinstance(started, Response):
            return started
        _project, _clips, reservation = started
        return _paid_response(reservation)


class ProjectRedispatchView(_WorkspaceScoped):
    """
    ``POST projects/{id}/redispatch/``: queues the pipeline of a failed project
    again; it resumes from its last finished stage (``_run_ingestion``, the
    same as the admin's re-dispatch). Owner and admin only.

    Money: what a failed project paid for was returned when it failed. If the
    work left needs it again (a stage before the clips are selected, or a whole
    video, whose creation pays its render), the same amount is reserved again
    in ``metadata['reservation_id']`` before queueing (402 without balance), and
    the pipeline settles it as at creation. A reservation still RESERVED is
    reused. A project with its clips selected resumes free: its render pays
    itself (the auto-render reserves again; a review waits for its approval).
    409 when it is not failed, its content was blocked by moderation
    (UnsafeContentError), its ingestion was never queued at creation, its link
    is longer than the app processes (LinkTooLongForAppError), its clips
    render one by one (their render was settled: each is retried from Clips)
    or its whole video was rendered already (``rerender/``). If the broker
    refuses the message, what was reserved goes back with REDISPATCH_NOT_QUEUED
    (503), which the pipeline never resumes until another re-dispatch pays.
    """

    workspace_actions = {'POST': 'members.manage'}

    def post(self, request, pk):
        from .tasks import process_initial_ingestion
        from .tasks.ingestion import REDISPATCH_NOT_QUEUED

        project = self.get_project(request, pk)
        reservation = None
        try:
            with transaction.atomic():
                project = VideoProject.objects.select_for_update().select_related('workspace').get(pk=project.pk)
                if project.status != Status.FAILED or project.pipeline_stage != VideoProject.PipelineStage.FAILED:
                    raise _Refused(_error(409, "El proyecto ya está en curso o terminó.", 'not_failed'))
                amount = self._amount_to_reserve(project)
                metadata = dict(project.metadata or {})
                updates = {}
                if project.pipeline_error_code == REDISPATCH_NOT_QUEUED:
                    # A re-dispatch refunded before: this one queues it, so
                    # the pipeline must not skip it any more.
                    updates['pipeline_error_code'] = metadata.pop(REDISPATCH_PREVIOUS_CODE_KEY, '') or ''
                if amount is not None:
                    reservation = _reserve(
                        project, amount, request.user, f"Procesamiento: {project.title}", Transaction.Category.PROJECT,
                    )
                    metadata[REPLACED_RESERVATIONS_KEY] = _replaced(metadata, 'reservation_id')
                    metadata['reservation_id'] = str(reservation.id)
                if updates or amount is not None:
                    VideoProject.objects.filter(pk=project.pk).update(metadata=metadata, **updates)
        except _Refused as refused:
            return refused.response

        try:
            process_initial_ingestion.delay(str(project.pk))
        except Exception as exc:
            logger.error("pipeline.redispatch_not_queued project_id=%s error=%s", project.pk, type(exc).__name__)
            if reservation is None or self._give_back(project.pk, reservation):
                return _error(503, "No pudimos volver a procesar el proyecto. Probá de nuevo en unos minutos.")
            # The message got through after all: the pipeline already took
            # the project, and the reservation pays for that run.
        logger.info("pipeline.redispatch_requested project_id=%s reserved=%s", project.pk, reservation is not None)
        project.refresh_from_db()
        data = VideoProjectSerializer(project, context={'request': request}).data
        if reservation is not None:
            data['cost_details'] = {"tokens_reserved": reservation.amount, "transaction_id": reservation.id}
        return Response(data, status=status.HTTP_202_ACCEPTED)

    @staticmethod
    def _amount_to_reserve(project):
        """
        The amount to reserve again (the creation's), None when nothing is
        needed, or ``_Refused`` with the 409 of a project that cannot resume.
        """
        from .services.ai.pipeline_state import STAGE_ORDER, PipelineStage, last_completed_stage
        from .tasks.ingestion import APPROVED_RENDER, CLIP_RENDER, _is_whole_video, _paid_clip_renders

        if project.pipeline_error_code in MODERATION_CODES:
            raise _Refused(_error(
                409, "El contenido no pasó la moderación: no se puede volver a procesar. Si creés que es un error, escribinos.",
                'moderation_blocked',
            ))
        if project.pipeline_error_code == INGESTION_NOT_QUEUED:
            # Refunded at creation and never resumed by the pipeline.
            raise _Refused(_error(
                409, "Este proyecto no llegó a procesarse y se devolvió lo reservado: creá uno nuevo.", 'not_queued',
            ))
        if project.pipeline_error_code == LINK_TOO_LONG_FOR_APP:
            # Reserving again would only fail and refund it again.
            raise _Refused(_error(409, (
                "El video del link dura más de lo que podemos procesar, con cualquier plan: no se puede volver "
                "a procesar. Creá un proyecto con un video más corto."
            ), 'duration_app'))
        metadata = project.metadata or {}
        resume = last_completed_stage(project)
        whole = _is_whole_video(project)
        before_clips = STAGE_ORDER.index(resume) < STAGE_ORDER.index(PipelineStage.CLIPS_SELECTED)
        if not whole and not before_clips:
            per_clip = [paid for paid in _paid_clip_renders(metadata) if paid in (APPROVED_RENDER, CLIP_RENDER)]
            reserved = Transaction.objects.filter(
                pk__in=[metadata[paid.reservation_key] for paid in per_clip], status=Transaction.Status.RESERVED,
            ).exists()
            if resume == PipelineStage.RENDER_DISPATCHED and per_clip and not reserved:
                # A resume would only fail its clips as unpaid.
                raise _Refused(_error(
                    409, "El render de estos clips ya se liquidó: reintentá cada clip desde Clips.", 'render_settled',
                ))
            return None  # the ingestion was paid; the render pays itself
        if whole and VideoClip.objects.filter(
            project_id=project.pk, status__in=[*_DELIVERED, ClipStatus.FAILED],
        ).exists():
            raise _Refused(_error(
                409, "Este video ya se renderizó: para hacerlo de nuevo, usá «Volver a renderizar».", 'use_rerender',
            ))
        creation = None
        if metadata.get('reservation_id'):
            creation = Transaction.objects.filter(pk=metadata['reservation_id']).first()
        if creation is None:
            raise _Refused(_error(409, "Este proyecto no se puede volver a procesar: creá uno nuevo.", 'no_reservation'))
        if creation.status != Transaction.Status.FAILED:
            return None  # still reserved (it pays the resume) or already charged
        return creation.amount

    @staticmethod
    def _give_back(project_id, reservation):
        """
        The pipeline could not be queued: its new reservation goes back, in one
        transaction with REDISPATCH_NOT_QUEUED (the error code it replaces kept
        in metadata), so a late copy of the message never runs it for free
        (``_run_ingestion``). Under the row lock that the pipeline's reopen
        takes: if the message got through and the pipeline already reopened the
        project, the reservation pays for that run and stays. Returns whether
        it gave it back. A refund that fails leaves it RESERVED for the
        pipeline or settle_stale_reservations.
        """
        from .tasks.ingestion import REDISPATCH_NOT_QUEUED

        try:
            with transaction.atomic():
                project = VideoProject.objects.select_for_update().get(pk=project_id)
                if project.pipeline_stage != VideoProject.PipelineStage.FAILED:
                    logger.warning("pipeline.redispatch_refund_skipped project_id=%s reason=resumed", project_id)
                    return False
                wallet_service.rollback_reservation(reservation.id)
                metadata = dict(project.metadata or {})
                if project.pipeline_error_code != REDISPATCH_NOT_QUEUED:
                    metadata[REDISPATCH_PREVIOUS_CODE_KEY] = project.pipeline_error_code
                VideoProject.objects.filter(pk=project_id).update(
                    pipeline_error_code=REDISPATCH_NOT_QUEUED, metadata=metadata,
                )
        except Exception as exc:
            logger.error(
                "pipeline.redispatch_refund_failed transaction_id=%s error=%s", reservation.id, type(exc).__name__,
            )
        return True
