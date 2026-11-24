"""
Clip previews (PTE-03): a fast, free, temporary render of a clip.

The user sees the clip with the project's layout and subtitles before paying
for the final render. To stay fast and cheap it is simplified:

- Height PREVIEW_HEIGHT (360p), same aspect ratio as the final render.
- No face tracking and no silence removal (the static layout crop is used).
- The source is decoded at the preview height, at 15 fps, encoded with 'ultrafast'.

It never touches the wallet and calls no AI provider. A preview expires
PREVIEW_TTL_SECONDS after it is ready: the API stops serving it at once and
``cleanup_expired_previews`` deletes its row and file (Celery Beat, and on
every new request so storage does not grow without Beat).

Only static codes are logged, never titles, transcripts or paths of user files.
"""

import logging
import math
import os
import shutil
import tempfile
from datetime import timedelta
from typing import Optional

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.videos.models import ClipPreview, VideoClip

logger = logging.getLogger(__name__)

PREVIEW_PRESET = 'ultrafast'
# Enough to judge framing and subtitles; every frame goes through the layout,
# so fewer frames is the cheapest speed-up left after the 360p decode.
PREVIEW_FPS = 15
# Cleanup on request is bounded so a request never pays for a big backlog.
_CLEANUP_BATCH = 50


class PreviewLimitExceeded(Exception):
    """The workspace already has PREVIEW_MAX_ACTIVE_PER_WORKSPACE previews in progress."""


def _ttl() -> timedelta:
    return timedelta(seconds=getattr(settings, 'PREVIEW_TTL_SECONDS', 3600))


def preview_dimensions(aspect_ratio: str):
    """Target (width, height) of a preview: the final render's aspect ratio at PREVIEW_HEIGHT."""
    from apps.videos.services.render_engine import RenderEngine
    from apps.videos.utils.moviepy_utils import ensure_even

    height = getattr(settings, 'PREVIEW_HEIGHT', 360)
    final_w, final_h = RenderEngine._get_target_resolution(aspect_ratio, '720p')
    return ensure_even(final_w * height / final_h), ensure_even(height)


def _decode_height(project, target_w: int, target_h: int) -> int:
    """
    Smallest source height that still covers the preview in both dimensions,
    from the resolution stored at ingestion (``metadata['resolution']``). A
    portrait source rendered square or landscape needs more than ``target_h``
    so the layout does not upscale. Never above the source's own height.
    """
    try:
        source_w, source_h = (project.metadata or {}).get('resolution') or (None, None)
        source_w, source_h = float(source_w), float(source_h)
    except (TypeError, ValueError):
        return target_h
    if source_w <= 0 or source_h <= 0:
        return target_h
    needed = max(target_h, target_w * source_h / source_w)
    return int(min(source_h, math.ceil(needed / 2) * 2))


def current_preview(clip: VideoClip) -> Optional[ClipPreview]:
    """The newest preview of the clip that is still in progress or not expired."""
    now = timezone.now()
    return (
        ClipPreview.objects.filter(clip=clip, expires_at__gt=now)
        .order_by('-created_at')
        .first()
    )


def request_preview(clip: VideoClip, user) -> tuple[ClipPreview, bool]:
    """
    Returns ``(preview, created)``. A preview already in progress for the clip
    is reused instead of rendering twice. Otherwise a new one is queued, unless
    the workspace is at its limit (PreviewLimitExceeded).
    """
    from apps.videos.tasks import render_clip_preview_task

    cleanup_expired_previews_now(limit=_CLEANUP_BATCH)

    with transaction.atomic():
        # Serializes requests of the same clip so two clicks queue one render.
        VideoClip.objects.select_for_update().filter(pk=clip.pk).first()
        active = ClipPreview.objects.filter(
            clip=clip, status__in=ClipPreview.ACTIVE_STATUSES, expires_at__gt=timezone.now(),
        ).first()
        if active is not None:
            return active, False

        limit = getattr(settings, 'PREVIEW_MAX_ACTIVE_PER_WORKSPACE', 3)
        in_progress = ClipPreview.objects.filter(
            clip__project__workspace_id=clip.project.workspace_id,
            status__in=ClipPreview.ACTIVE_STATUSES,
            expires_at__gt=timezone.now(),
        ).count()
        if limit and in_progress >= limit:
            raise PreviewLimitExceeded()

        # Until it is ready, expires_at bounds how long a stuck render is shown
        # as pending; it is moved to ready_at + TTL when the render finishes.
        preview = ClipPreview.objects.create(
            clip=clip, requested_by=user, expires_at=timezone.now() + _ttl(),
        )
        transaction.on_commit(lambda: render_clip_preview_task.delay(str(preview.id)))
    logger.info("preview.requested clip_id=%s preview_id=%s", clip.pk, preview.pk)
    return preview, True


def render_preview(preview_id) -> Optional[ClipPreview]:
    """Renders and stores a preview. Never raises: a failure is stored as ``failed``."""
    from apps.videos.services.render_engine import RenderEngine
    from apps.videos.utils.moviepy_utils import FFMPEG_PARAMS, RENDER_AUDIO_CODEC, RENDER_CODEC

    claimed = ClipPreview.objects.filter(
        pk=preview_id, status=ClipPreview.Status.PENDING,
    ).update(status=ClipPreview.Status.RENDERING)
    if not claimed:
        # Already rendered, failed, expired and deleted, or taken by another worker.
        return None

    preview = ClipPreview.objects.select_related('clip__project__uploaded_by').get(pk=preview_id)
    clip_obj = preview.clip
    project = clip_obj.project
    work_dir = tempfile.mkdtemp(prefix='clip_preview_')
    output_path = os.path.join(work_dir, f'{preview.id}.mp4')
    original_clip = final_clip = None
    try:
        target_w, target_h = preview_dimensions(project.aspect_ratio)
        # Decoding the source straight at the preview height keeps the layout
        # (crop, blur, composition) on small frames: the main cost at 1080p.
        original_clip, final_clip = RenderEngine.compose_clip(
            project, clip_obj, project.source_file.path, target_w, target_h,
            use_facetracking=False, source_height=_decode_height(project, target_w, target_h),
        )
        final_clip.write_videofile(
            output_path,
            codec=RENDER_CODEC,
            audio_codec=RENDER_AUDIO_CODEC,
            fps=PREVIEW_FPS,
            preset=PREVIEW_PRESET,
            ffmpeg_params=FFMPEG_PARAMS,
            logger=None,
        )
        key = _store(output_path, preview)
        now = timezone.now()
        ClipPreview.objects.filter(pk=preview.pk).update(
            status=ClipPreview.Status.READY, storage_key=key,
            ready_at=now, expires_at=now + _ttl(),
        )
        logger.info("preview.ready clip_id=%s preview_id=%s", clip_obj.pk, preview.pk)
    except Exception as exc:
        ClipPreview.objects.filter(pk=preview.pk).update(
            status=ClipPreview.Status.FAILED, error_code=type(exc).__name__[:64],
        )
        logger.error(
            "preview.failed clip_id=%s preview_id=%s error=%s",
            clip_obj.pk, preview.pk, type(exc).__name__,
        )
    finally:
        for moviepy_clip in (final_clip, original_clip):
            if moviepy_clip is not None:
                try:
                    moviepy_clip.close()
                except Exception:  # pragma: no cover - best effort
                    pass
        shutil.rmtree(work_dir, ignore_errors=True)
    return ClipPreview.objects.filter(pk=preview_id).first()


def _store(local_path: str, preview: ClipPreview) -> str:
    """
    Moves the rendered file to its permanent place and returns its key:
    MEDIA_ROOT/previews/<clip>/<preview>.mp4 locally, or the same relative key
    in R2. A fixed ``previews/`` prefix keeps them apart from final renders.
    """
    from apps.videos.services.storage_service import CloudflareR2Manager, _is_local

    relative_key = f'previews/{preview.clip_id}/{preview.id}.mp4'
    if _is_local():
        destination = os.path.join(str(settings.MEDIA_ROOT), *relative_key.split('/'))
        os.makedirs(os.path.dirname(destination), exist_ok=True)
        shutil.move(local_path, destination)
        return destination
    CloudflareR2Manager.get_client().upload_file(
        local_path, settings.CLOUDFLARE_R2_BUCKET_NAME, relative_key,
    )
    return relative_key


def preview_url(preview: ClipPreview) -> Optional[str]:
    """Temporary URL of a ready preview, valid no longer than the preview itself."""
    from apps.videos.services.storage_service import CloudflareR2Manager

    if preview.status != ClipPreview.Status.READY or not preview.storage_key:
        return None
    remaining = int((preview.expires_at - timezone.now()).total_seconds())
    if remaining <= 0:
        return None
    return CloudflareR2Manager.generate_presigned_url(
        preview.storage_key, expiration_seconds=max(60, remaining),
    )


def cleanup_expired_previews_now(limit: Optional[int] = None) -> int:
    """
    Deletes expired previews (row and file). Returns how many rows were deleted.
    A file that cannot be deleted is logged and its row kept for the next run.
    """
    from apps.videos.services.storage_service import CloudflareR2Manager

    expired = ClipPreview.objects.filter(expires_at__lte=timezone.now()).order_by('expires_at')
    if limit:
        expired = expired[:limit]
    deleted = 0
    for preview in list(expired):
        if preview.storage_key:
            try:
                CloudflareR2Manager.delete_object(preview.storage_key)
            except Exception as exc:
                logger.warning(
                    "preview.cleanup_failed preview_id=%s error=%s", preview.pk, type(exc).__name__,
                )
                continue
        preview.delete()
        deleted += 1
    if deleted:
        logger.info("preview.cleanup deleted=%d", deleted)
    return deleted
