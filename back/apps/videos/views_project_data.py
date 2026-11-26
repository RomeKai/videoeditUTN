"""
Reads and downloads of a project and its clips, apart from the viewsets of
views.py:

- ``GET  /api/v1/projects/<id>/activity/``: who did what and the token
  movements (services/project_activity.py).
- ``POST /api/v1/projects/<id>/clips/download/``: a .zip of the ready clips,
  built while the request waits, as a temporary URL (services/clip_download.py).
- ``GET  /api/v1/clips/<id>/download/``: a temporary URL of one rendered clip.
- ``POST /api/v1/projects/probe-link/``: site, title and duration of a link
  before creating a project from it (services/link_probe.py).

Each one acts in the active workspace (X-Workspace-Id): a project or clip of
another workspace is a 404. Roles follow ROLE_MATRIX (apps/core/permissions.py).
"""
import logging
import uuid

from django.core.cache import cache
from django.http import Http404
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import VideoClip, VideoProject
from .views import NO_WORKSPACE_ERROR, ActiveWorkspaceMixin

logger = logging.getLogger(__name__)

STORAGE_ERROR = "No pudimos leer los clips del almacenamiento. Probá de nuevo en unos minutos."
CLIP_NOT_READY = "El clip todavía no está renderizado."
NO_CLIP_READY = "Ningún clip está listo todavía."
ZIP_BUSY = "Ya estamos armando un .zip en este espacio. Probá de nuevo en unos segundos."
ZIP_TIMEOUT = "Armar el .zip tardó demasiado. Elegí menos clips o descargalos de a uno."
PROBE_RATE_LIMITED = "Revisaste muchos links seguidos. Probá de nuevo en un minuto."

# One .zip at a time per workspace: each one holds a web worker for up to
# CLIPS_ZIP_MAX_SECONDS and its size in disk.
ZIP_LOCK_PREFIX = 'clips-zip-lock:'
# probe-link starts a process per link: bounded per user.
PROBE_RATE_PREFIX = 'probe-link-rate:'
PROBE_MAX_PER_MINUTE = 20


class _WorkspaceView(ActiveWorkspaceMixin, APIView):
    def project(self, project_id):
        workspace = self.active_workspace
        if workspace is None:
            raise Http404
        return get_object_or_404(
            VideoProject.objects.select_related('uploaded_by'), pk=project_id, workspace=workspace,
        )


class ProjectActivityView(_WorkspaceView):
    """D1 › Actividad. Every role reads it, token movements included (like the project's cost)."""

    workspace_actions = {'GET': 'project.view'}

    def get(self, request, project_id):
        from .services.project_activity import build_activity

        return Response(build_activity(self.project(project_id)))


def _gb(value):
    return f"{value / (1024 ** 3):.1f}".replace('.', ',').replace(',0', '')


class ProjectClipsDownloadView(_WorkspaceView):
    """
    D3 › Descargar todo: the ready clips (all, or those of ``clip_ids``) in one
    .zip. 409 when none is ready, 422 over the size or count cap, 429 while
    another .zip of the workspace is being built, 503 on a storage error or
    when it takes longer than CLIPS_ZIP_MAX_SECONDS.
    """

    # A download for every role, Lector included (map 1.7).
    workspace_actions = {'POST': 'clip.download'}

    @staticmethod
    def _clip_ids(data):
        raw = data.get('clip_ids') if hasattr(data, 'get') else None
        if raw is None:
            return None
        if not isinstance(raw, list):
            return False
        try:
            return {uuid.UUID(str(value)) for value in raw}
        except (TypeError, ValueError):
            return False

    def post(self, request, project_id):
        from .services import clip_download

        project = self.project(project_id)
        clip_ids = self._clip_ids(request.data)
        if clip_ids is False:
            return Response({"clip_ids": ["Mandá una lista de ids de clips."]}, status=status.HTTP_400_BAD_REQUEST)
        clips = VideoClip.objects.filter(
            project=project, status__in=clip_download.READY_STATUSES,
        ).exclude(s3_object_key__isnull=True).exclude(s3_object_key='').order_by('created_at', 'pk')
        if clip_ids is not None:
            clips = clips.filter(pk__in=clip_ids)
        clips = list(clips)
        if not clips:
            return Response({"error": NO_CLIP_READY}, status=status.HTTP_409_CONFLICT)

        lock = f"{ZIP_LOCK_PREFIX}{project.workspace_id}"
        if not cache.add(lock, 1, clip_download.zip_max_seconds() + 60):
            return Response({"error": ZIP_BUSY}, status=status.HTTP_429_TOO_MANY_REQUESTS)
        try:
            result = clip_download.build_project_zip(project, clips, request)
        except clip_download.ZipTooLarge as exc:
            if exc.code == 'zip_too_many_clips':
                message = f"Son demasiados clips para un solo .zip (hasta {exc.limit}). Elegí menos."
            else:
                message = (
                    f"Los clips pesan más de lo que entra en un .zip (hasta {_gb(exc.limit)} GB). "
                    "Elegí menos o descargalos de a uno."
                )
            return Response(
                {"error": message, "code": exc.code, "limit": exc.limit},
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )
        except clip_download.ZipTimeout:
            logger.warning("clips_zip.timeout project_id=%s clips=%d", project.pk, len(clips))
            return Response({"error": ZIP_TIMEOUT, "code": "zip_timeout"}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        except clip_download.StorageUnavailable as exc:
            # Our own code or an exception class name, never the provider's text.
            logger.error("clips_zip.storage_failed project_id=%s error=%s", project.pk, exc)
            return Response({"error": STORAGE_ERROR, "code": "storage_unavailable"}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        finally:
            cache.delete(lock)
        return Response({"url": result.url, "expires_at": result.expires_at})


class ClipDownloadView(_WorkspaceView):
    """D3 / D4 › Descargar: a temporary URL that saves the rendered clip as ``filename``."""

    workspace_actions = {'GET': 'clip.download'}

    def get(self, request, clip_id):
        from .services import clip_download

        workspace = self.active_workspace
        if workspace is None:
            raise Http404
        clip = get_object_or_404(VideoClip.objects.only('id', 'title', 'status', 's3_object_key'),
                                 pk=clip_id, project__workspace=workspace)
        if not clip_download.is_ready(clip):
            return Response({"error": CLIP_NOT_READY}, status=status.HTTP_409_CONFLICT)
        try:
            result = clip_download.clip_download(clip, request)
        except clip_download.StorageUnavailable as exc:
            logger.error("clip_download.failed clip_id=%s error=%s", clip.pk, exc)
            return Response(
                {"error": "No pudimos preparar la descarga. Probá de nuevo en unos minutos.", "code": "storage_unavailable"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        return Response({"url": result.url, "filename": result.filename, "expires_at": result.expires_at})


class ProbeLinkView(_WorkspaceView):
    """
    C1 › Pegar link. Part of creating a project: Owner, Admin and Editor
    (``project.create``). 400 ``unsupported``/``unavailable``/``invalid``,
    403 ``duration`` over the plan, 429 too many probes, 502/503/504 when the
    site or the tool does not answer.
    """

    workspace_actions = {'POST': 'project.create'}

    def _rate_limited(self, request):
        key = f"{PROBE_RATE_PREFIX}{request.user.pk}"
        try:
            if cache.add(key, 1, 60):
                return False
            return cache.incr(key) > PROBE_MAX_PER_MINUTE
        except Exception:  # a cache outage must not block the probe (or count it)
            return False

    def post(self, request):
        from .services.link_probe import LinkProbeError, probe_link
        from .services.link_validation import MAX_LINK_LENGTH, LinkRejected

        workspace = self.active_workspace
        if workspace is None:
            return Response({"error": NO_WORKSPACE_ERROR}, status=status.HTTP_400_BAD_REQUEST)
        raw = request.data.get('video_url') if hasattr(request.data, 'get') else None
        if not isinstance(raw, str) or not raw.strip():
            return Response({"video_url": ["Este campo es obligatorio."]}, status=status.HTTP_400_BAD_REQUEST)
        if len(raw.strip()) > MAX_LINK_LENGTH:
            return Response(
                {"video_url": [f"Asegúrese de que este campo no tenga más de {MAX_LINK_LENGTH} caracteres."]},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if self._rate_limited(request):
            return Response({"error": PROBE_RATE_LIMITED}, status=status.HTTP_429_TOO_MANY_REQUESTS)
        try:
            info = probe_link(raw, log_context={'user_id': request.user.pk})
        except LinkRejected as exc:
            return Response({"error": exc.message, "code": exc.code}, status=status.HTTP_400_BAD_REQUEST)
        except LinkProbeError as exc:
            return Response({"error": exc.message, "code": exc.code}, status=exc.status)

        plan = workspace.subscription_plan
        if plan is not None and info.duration_seconds > plan.max_video_duration_seconds:
            from .services.media_validation import _format_duration

            limit = plan.max_video_duration_seconds
            return Response({
                "error": f"El video dura {_format_duration(info.duration_seconds)} y tu plan permite hasta {_format_duration(limit)}.",
                "code": "duration",
                "limit": limit,
            }, status=status.HTTP_403_FORBIDDEN)
        return Response(info.as_dict())
