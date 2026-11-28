"""Prepare a rendered clip for a direct platform API and publish it."""
import math
import subprocess
import tempfile
import time
from contextlib import closing, contextmanager
from dataclasses import dataclass
from pathlib import Path

import requests
from django.conf import settings
from django.core.cache import caches
from django.core.exceptions import SuspiciousFileOperation
from django.utils._os import safe_join

from apps.integrations.social.base import INSTAGRAM_REELS, TIKTOK, YOUTUBE_SHORTS
from apps.integrations.social.direct import DIRECT, UPLOAD_BUDGET_SECONDS, DirectHTTP, get_grant, publisher_for
from apps.integrations.social.errors import CLIP_TOO_LONG, PermanentError, RetryableError

MB = 1_000_000
# Caps agreed in DIST-02 for direct uploads. The legacy provider limits exposed
# by /social-accounts/ remain in scheduled_posts.MAX_DURATION_SECONDS.
MEDIA_LIMITS = {
    TIKTOK: (0, 600, 287 * MB),
    YOUTUBE_SHORTS: (0, 60, 256 * MB),
    INSTAGRAM_REELS: (3, 90, 1_000 * MB),
}


@dataclass(frozen=True)
class PublishMedia:
    path: Path
    size: int
    duration: float


def _inspect(path, platform):
    # Reuse the MP4-only, local-only probe used for uploaded files. ffprobe
    # stderr can contain private data and must never reach a publishing error.
    from .media_validation import InvalidUploadError, _duration, _probe, _size

    try:
        probe = _probe(str(path), 'mov')
        streams = probe.get('streams') or []
        picture = next(stream for stream in streams
                       if stream.get('codec_type') == 'video' and (stream.get('disposition') or {}).get('attached_pic') != 1)
        duration = _duration(probe, picture)
        width, height = _size(picture)
    except (InvalidUploadError, StopIteration):
        raise PermanentError(detail='unreadable rendered video') from None
    minimum, maximum, _ = MEDIA_LIMITS[platform]
    if not math.isfinite(duration) or duration < minimum or width < 1 or height < 1:
        raise PermanentError(detail='invalid rendered video duration or dimensions')
    if duration > maximum:
        raise PermanentError(CLIP_TOO_LONG, 'rendered clip exceeds the direct platform limit')
    ready = (
        width * 16 == height * 9 and width <= 1080 and picture.get('codec_name') == 'h264'
        and picture.get('sample_aspect_ratio') in (None, '1:1')
        and not any(side.get('rotation') for side in picture.get('side_data_list') or [])
        and all(stream.get('codec_name') == 'aac' for stream in streams if stream.get('codec_type') == 'audio')
    )
    return duration, ready


def _encode(source, target, duration, limit, deadline):
    # Leave room for audio and MP4 overhead. Pad to preserve the rendered
    # image, including subtitles and overlays. DIST-04 owns content trimming.
    bitrate = min(8_000_000, int(limit * 8 * 0.90 / duration) - 128_000)
    try:
        subprocess.run(
            [
                'ffmpeg', '-nostdin', '-v', 'error', '-protocol_whitelist', 'file', '-f', 'mov',
                '-i', str(source), '-map', '0:v:0', '-map', '0:a:0?',
                '-vf', 'scale=1080:1920:force_original_aspect_ratio=decrease:force_divisible_by=2,'
                       'pad=1080:1920:(ow-iw)/2:(oh-ih)/2,setsar=1',
                '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-preset', 'veryfast', '-b:v', str(bitrate),
                '-maxrate', str(bitrate), '-bufsize', str(bitrate * 2), '-c:a', 'aac',
                '-b:a', '128k', '-ar', '48000', '-movflags', '+faststart', str(target),
            ],
            check=True, capture_output=True, timeout=max(1, min(100, deadline - time.monotonic())),
        )
    except (OSError, subprocess.SubprocessError):
        raise PermanentError(detail='could not encode video for direct publishing') from None


def _download(key, target, max_bytes, deadline):
    from botocore.config import Config
    from botocore.exceptions import BotoCoreError, ClientError

    from .storage_service import _get_r2_client

    config = Config(connect_timeout=5, read_timeout=15, retries={'total_max_attempts': 1})
    try:
        with _get_r2_client(config) as client:
            obj = client.get_object(Bucket=settings.CLOUDFLARE_R2_BUCKET_NAME, Key=key)
            with closing(obj['Body']) as body, target.open('wb') as output:
                if obj['ContentLength'] > max_bytes:
                    raise PermanentError(detail='rendered video exceeds the preparation size limit')
                size = 0
                for chunk in body.iter_chunks(chunk_size=1024 * 1024):
                    size += len(chunk)
                    if size > max_bytes:
                        raise PermanentError(detail='rendered video exceeds the preparation size limit')
                    if time.monotonic() >= deadline:
                        raise RetryableError(detail='rendered video download deadline')
                    output.write(chunk)
    except (BotoCoreError, ClientError, OSError):
        raise RetryableError(detail='rendered video storage unavailable') from None


@contextmanager
def prepare_media(key, platform, deadline):
    if not key:
        raise PermanentError(detail='rendered video is missing')
    limit = MEDIA_LIMITS[platform][2]
    with tempfile.TemporaryDirectory(prefix='direct-publish-') as work:
        folder = Path(work)
        try:
            if settings.USE_R2:
                source = folder / 'source.mp4'
                _download(key, source, 2 * limit, min(deadline, time.monotonic() + 60))
            else:
                source = Path(safe_join(settings.MEDIA_ROOT, key)).resolve()
                if not source.is_relative_to(Path(settings.MEDIA_ROOT).resolve()):
                    raise PermanentError(detail='invalid local rendered video')
            size = source.stat().st_size
            duration, ready = _inspect(source, platform)
            if not ready or size > limit:
                target = folder / 'prepared.mp4'
                _encode(source, target, duration, limit, deadline)
                source = target
                size = source.stat().st_size
                duration, ready = _inspect(source, platform)
            if not ready or not 0 < size <= limit:
                raise PermanentError(detail='video does not meet direct platform format or size limits')
        except SuspiciousFileOperation:
            raise PermanentError(detail='invalid local rendered video') from None
        except OSError:
            raise RetryableError(detail='rendered video unavailable') from None
        yield PublishMedia(source, size, duration)


class PublishingEngine:
    name = DIRECT
    display_name = 'Direct API'

    def __init__(self, workspace_id, post_id, tiktok=None):
        self.workspace_id = workspace_id
        self.post_id = post_id
        self.tiktok = tiktok

    def publish(self, clip_id, platform, caption, hashtags):
        from apps.videos.models import VideoClip

        from .seo_engine import compose_caption

        clip = VideoClip.objects.select_related('project').filter(
            pk=clip_id, project__workspace_id=self.workspace_id,
            status__in=(VideoClip.Status.COMPLETED, VideoClip.Status.PUBLISHED),
        ).first()
        if clip is None:
            raise PermanentError(detail='rendered video is missing')
        cooldown = caches['default']
        cooldown_key = f'publish-rate-limit:{self.workspace_id}:{platform}'
        if cooldown.get(cooldown_key):
            raise RetryableError(detail='direct platform rate limit')
        grant = get_grant(self.workspace_id, platform, self.post_id)
        deadline = time.monotonic() + UPLOAD_BUDGET_SECONDS
        try:
            with prepare_media(clip.s3_object_key, platform, deadline) as media, requests.Session() as session:
                publisher = publisher_for(platform)(DirectHTTP(session, deadline), grant)
                return publisher.publish(media, clip.title or clip.project.title, compose_caption(caption, hashtags), self.tiktok)
        except RetryableError as exc:
            if exc.retry_after:
                cooldown.set(cooldown_key, True, timeout=exc.retry_after)
            raise
