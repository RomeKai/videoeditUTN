"""
What the web app shows of a rendered clip: the URL that plays it
(``preview_url``) and its thumbnail (``thumbnail_url``).

The thumbnail is one frame of the rendered file, a JPEG stored next to it
(the same key with ``.jpg``), made at the end of the render. It is best
effort: whatever fails is logged and the clip is delivered without it.

The URLs are temporary. In R2 they are presigned GETs (signed here, with no
network call) that last ``clip_url_ttl_seconds`` like a download link, but
without ``Content-Disposition``, so the browser plays the file. Locally
(USE_R2=False) they are the absolute /media/ URL, as ``proxy_url``.

Logs carry ids and exception class names only.
"""
import logging
import os
import shutil
import subprocess

from django.conf import settings

logger = logging.getLogger(__name__)

THUMBNAIL_LONG_SIDE = 360
# The frame at this second, or the middle of a clip shorter than twice it.
THUMBNAIL_AT_SECONDS = 1.0
# Longer on the long side only: the other one follows the picture (even, for the encoder).
_SCALE = (
    f"scale='if(gte(iw,ih),{THUMBNAIL_LONG_SIDE},-2)':'if(gte(iw,ih),-2,{THUMBNAIL_LONG_SIDE})'"
)
PLAYABLE_STATUSES = ('completed', 'published')


def thumbnail_timeout_seconds():
    return getattr(settings, 'CLIP_THUMBNAIL_TIMEOUT_SECONDS', 8)


def thumbnail_second(duration):
    duration = max(0.0, float(duration or 0))
    return THUMBNAIL_AT_SECONDS if duration >= 2 * THUMBNAIL_AT_SECONDS else duration / 2


def thumbnail_ffmpeg_args(video_path, image_path, duration):
    """The ffmpeg arguments (after the binary) that write one frame of ``video_path`` as a JPEG."""
    return [
        '-ss', f'{thumbnail_second(duration):.3f}', '-i', video_path,
        '-frames:v', '1', '-vf', _SCALE, '-q:v', '4', image_path,
    ]


def thumbnail_key_for(clip_key):
    """The thumbnail's storage key: next to the clip file, with ``.jpg``."""
    return f"{os.path.splitext(clip_key)[0]}.jpg"


def extract_frame(video_path, image_path, duration):
    """Runs ffmpeg, at most ``thumbnail_timeout_seconds``. Raises when it fails or writes nothing."""
    subprocess.run(
        ['ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error', '-y',
         *thumbnail_ffmpeg_args(video_path, image_path, duration)],
        check=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        timeout=thumbnail_timeout_seconds(),
    )
    if not os.path.isfile(image_path) or os.path.getsize(image_path) == 0:
        raise RuntimeError('thumbnail_not_written')


def _upload_client():
    """An R2 client that gives up in seconds: the render never waits long for a thumbnail."""
    from botocore.config import Config

    from .storage_service import _get_r2_client

    return _get_r2_client(Config(connect_timeout=3, read_timeout=5, retries={'max_attempts': 1}))


def make_clip_thumbnail(clip_id, video_path, clip_key, duration):
    """
    Makes the thumbnail of a rendered clip from its local file and stores it
    next to ``clip_key``. Returns its storage key, or None when anything
    fails: the render goes on without a thumbnail.
    """
    from .storage_service import _is_local

    image_path = f"{os.path.splitext(video_path)[0]}_thumb.jpg"
    try:
        if not os.path.isfile(video_path):
            raise FileNotFoundError(video_path)
        extract_frame(video_path, image_path, duration)
        key = thumbnail_key_for(clip_key)
        if _is_local():
            shutil.copyfile(image_path, key)
        else:
            _upload_client().upload_file(
                image_path, settings.CLOUDFLARE_R2_BUCKET_NAME, key, ExtraArgs={'ContentType': 'image/jpeg'},
            )
        return key
    except Exception as exc:
        logger.warning("thumbnail.failed clip_id=%s error=%s", clip_id, type(exc).__name__)
        return None
    finally:
        try:
            if os.path.exists(image_path):
                os.remove(image_path)
        except OSError:
            pass


def delete_thumbnail(clip_id, key):
    """Deletes a thumbnail whose clip file went (re-render, delete). Best effort."""
    if not key:
        return
    from .storage_service import CloudflareR2Manager

    try:
        CloudflareR2Manager.delete_object(key)
    except Exception as exc:
        logger.warning("thumbnail.not_deleted clip_id=%s error=%s", clip_id, type(exc).__name__)


# --- URLs ---------------------------------------------------------------------

_CLIENT_KEY = '_clip_media_r2_client'


def _media_url(clip_id, key, context):
    """
    A temporary URL that shows ``key``, or None when it cannot be signed.
    ``context`` is the serializer's: it carries the request (local URLs) and
    keeps one R2 client for every clip of the response.
    """
    from .clip_download import clip_url_ttl_seconds
    from .storage_service import CloudflareR2Manager, _get_r2_client, _is_local

    try:
        if _is_local():
            return CloudflareR2Manager.generate_presigned_url(key, request=context.get('request'))
        client = context.get(_CLIENT_KEY)
        if client is None:
            client = context[_CLIENT_KEY] = _get_r2_client()
        return client.generate_presigned_url(
            'get_object',
            Params={'Bucket': settings.CLOUDFLARE_R2_BUCKET_NAME, 'Key': key},
            ExpiresIn=clip_url_ttl_seconds(),
        )
    except Exception as exc:
        logger.warning("clip_media.url_failed clip_id=%s error=%s", clip_id, type(exc).__name__)
        return None


def preview_url(clip, context):
    """Plays the rendered file of a completed or published clip; None for any other."""
    if clip.status not in PLAYABLE_STATUSES or not clip.s3_object_key:
        return None
    return _media_url(clip.pk, clip.s3_object_key, context)


def thumbnail_url(clip, context):
    if not clip.thumbnail_object_key:
        return None
    return _media_url(clip.pk, clip.thumbnail_object_key, context)
