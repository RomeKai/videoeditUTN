"""
Downloads of rendered clips (D3 › Descargar, Descargar todo).

One clip: a signed, temporary URL of its file in storage that asks the
browser to save it (``Content-Disposition: attachment``) under a readable
name (``clip_filename``). Locally (USE_R2=False) the file is under MEDIA_ROOT
and the URL is its absolute /media/ URL.

All the ready clips of a project: a .zip built while the request waits (the
web app gives it SLOW_REQUEST_TIMEOUT_MS):

- the sizes are read first (HEAD in R2, stat locally): more than
  CLIPS_ZIP_MAX_BYTES, or more than CLIPS_ZIP_MAX_CLIPS clips, is refused
  before anything is downloaded;
- each clip is streamed from storage into the .zip on disk, in chunks
  (``CHUNK_BYTES``): nothing is held whole in memory. The .zip stores them
  as they are (MP4 does not compress);
- the build stops at CLIPS_ZIP_MAX_SECONDS, or when the bytes go over the
  cap;
- the .zip is stored under ``exports/zips/<hour>/`` and signed for
  CLIPS_ZIP_URL_TTL_SECONDS. The temporary folder is always deleted, and
  every request first deletes the stored .zips whose links already expired
  (the bucket should also expire ``exports/`` with a lifecycle rule).

Logs carry ids, counts and exception class names only.
"""
import logging
import os
import re
import shutil
import tempfile
import time
import uuid
import zipfile
from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.utils import timezone
from django.utils.http import content_disposition_header

logger = logging.getLogger(__name__)

CHUNK_BYTES = 1024 * 1024
EXPORT_PREFIX = 'exports/zips/'
MAX_FILENAME_STEM = 80
# Free disk kept beside the .zip being built.
DISK_MARGIN_BYTES = 64 * 1024 * 1024
_CLEANUP_BATCH = 50

READY_STATUSES = ('completed', 'published')


def _setting(name, default):
    return getattr(settings, name, default)


def clip_url_ttl_seconds():
    return _setting('CLIP_DOWNLOAD_URL_TTL_SECONDS', 3600)


def zip_url_ttl_seconds():
    return _setting('CLIPS_ZIP_URL_TTL_SECONDS', 3600)


def zip_max_bytes():
    return _setting('CLIPS_ZIP_MAX_BYTES', 1024 * 1024 * 1024)


def zip_max_clips():
    return _setting('CLIPS_ZIP_MAX_CLIPS', 50)


def zip_max_seconds():
    return _setting('CLIPS_ZIP_MAX_SECONDS', 100)


class StorageUnavailable(Exception):
    """Storage could not be read or written (the cause is logged by class name)."""


class ZipTooLarge(Exception):
    def __init__(self, code, limit):
        super().__init__(code)
        self.code = code
        self.limit = limit


class ZipTimeout(Exception):
    """The .zip took longer than CLIPS_ZIP_MAX_SECONDS."""


def slugify_filename(title, fallback):
    """
    The name the web app gives a download: letters and digits, the rest as
    single dashes, lowercase (``ClipsScreen`` and the mocks do the same).
    """
    stem = re.sub(r'[\W_]+', '-', title or '').strip('-').lower()[:MAX_FILENAME_STEM].strip('-')
    return stem or fallback


def clip_filename(clip):
    return f"{slugify_filename(clip.title, 'clip')}.mp4"


def zip_filename(project):
    return f"{slugify_filename(project.title, 'clips')}.zip"


def is_ready(clip):
    return clip.status in READY_STATUSES and bool(clip.s3_object_key)


def _is_local():
    from .storage_service import _is_local as storage_is_local

    return storage_is_local()


def _client():
    """
    The R2 client of storage_service (same settings), with short timeouts: a
    stalled read must not hold the request past CLIPS_ZIP_MAX_SECONDS.
    """
    import boto3
    from botocore.config import Config

    return boto3.client(
        's3',
        endpoint_url=settings.CLOUDFLARE_R2_ENDPOINT_URL,
        aws_access_key_id=settings.CLOUDFLARE_R2_ACCESS_KEY_ID,
        aws_secret_access_key=settings.CLOUDFLARE_R2_SECRET_ACCESS_KEY,
        region_name=settings.CLOUDFLARE_R2_REGION,
        config=Config(connect_timeout=5, read_timeout=20, retries={'max_attempts': 2, 'mode': 'standard'}),
    )


def _bucket():
    return settings.CLOUDFLARE_R2_BUCKET_NAME


def local_path(key):
    """
    The file of a local storage key, only if it is inside MEDIA_ROOT: a key
    is never a way to read another file of the server.
    """
    root = os.path.realpath(str(settings.MEDIA_ROOT))
    path = os.path.realpath(key if os.path.isabs(key) else os.path.join(root, key))
    if os.path.commonpath([root, path]) != root or not os.path.isfile(path):
        raise StorageUnavailable('local_file_missing')
    return path


def _local_url(path, request):
    """Absolute /media/ URL of a file under MEDIA_ROOT (``local_path`` checked it)."""
    from urllib.parse import quote

    relative = os.path.relpath(path, os.path.realpath(str(settings.MEDIA_ROOT))).replace(os.sep, '/')
    media_url = getattr(settings, 'MEDIA_URL', '/media/').rstrip('/')
    return request.build_absolute_uri(f"{media_url}/{quote(relative)}")


def signed_url(key, filename, content_type, ttl_seconds, request, client=None):
    """A temporary URL that downloads ``key`` as ``filename``."""
    if _is_local():
        return _local_url(local_path(key), request)
    try:
        return (client or _client()).generate_presigned_url(
            'get_object',
            Params={
                'Bucket': _bucket(),
                'Key': key,
                'ResponseContentDisposition': content_disposition_header(True, filename),
                'ResponseContentType': content_type,
            },
            ExpiresIn=ttl_seconds,
        )
    except Exception as exc:
        raise StorageUnavailable(type(exc).__name__) from exc


@dataclass(frozen=True)
class SignedDownload:
    url: str
    filename: str
    expires_at: object  # datetime


def clip_download(clip, request):
    """Signed URL of a ready clip. Raises StorageUnavailable."""
    ttl = clip_url_ttl_seconds()
    filename = clip_filename(clip)
    url = signed_url(clip.s3_object_key, filename, 'video/mp4', ttl, request)
    return SignedDownload(url=url, filename=filename, expires_at=timezone.now() + timedelta(seconds=ttl))


# --- .zip of a project -------------------------------------------------------

def _object_size(key, client):
    if client is None:
        return os.path.getsize(local_path(key))
    head = client.head_object(Bucket=_bucket(), Key=key)
    return int(head['ContentLength'])


def _open_chunks(key, client):
    """Chunks of a stored object, read as they arrive."""
    if client is None:
        with open(local_path(key), 'rb') as source:
            while chunk := source.read(CHUNK_BYTES):
                yield chunk
        return
    body = client.get_object(Bucket=_bucket(), Key=key)['Body']
    try:
        yield from body.iter_chunks(CHUNK_BYTES)
    finally:
        body.close()


def _arcnames(clips):
    """One name per clip inside the .zip; repeated titles get -2, -3..."""
    used, names = set(), []
    for clip in clips:
        stem = slugify_filename(clip.title, 'clip')
        name, number = f"{stem}.mp4", 1
        while name in used:
            number += 1
            name = f"{stem}-{number}.mp4"
        used.add(name)
        names.append(name)
    return names


def _check_sizes(clips, client):
    if len(clips) > zip_max_clips():
        raise ZipTooLarge('zip_too_many_clips', zip_max_clips())
    total = 0
    for clip in clips:
        try:
            total += _object_size(clip.s3_object_key, client)
        except Exception as exc:
            logger.error("clips_zip.size_failed clip_id=%s error=%s", clip.pk, type(exc).__name__)
            if isinstance(exc, StorageUnavailable):
                raise
            raise StorageUnavailable(type(exc).__name__) from exc
        if total > zip_max_bytes():
            raise ZipTooLarge('zip_too_large', zip_max_bytes())
    return total


def _write_zip(path, clips, deadline, client):
    written = 0
    with zipfile.ZipFile(path, 'w', compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
        for clip, name in zip(clips, _arcnames(clips)):
            entry = zipfile.ZipInfo(name, date_time=timezone.localtime(clip.created_at).timetuple()[:6])
            entry.compress_type = zipfile.ZIP_STORED
            entry.external_attr = 0o644 << 16
            try:
                with archive.open(entry, 'w', force_zip64=True) as target:
                    for chunk in _open_chunks(clip.s3_object_key, client):
                        written += len(chunk)
                        if written > zip_max_bytes():
                            raise ZipTooLarge('zip_too_large', zip_max_bytes())
                        if time.monotonic() > deadline:
                            raise ZipTimeout()
                        target.write(chunk)
            except (ZipTooLarge, ZipTimeout, StorageUnavailable):
                raise
            except Exception as exc:
                logger.error("clips_zip.read_failed clip_id=%s error=%s", clip.pk, type(exc).__name__)
                raise StorageUnavailable(type(exc).__name__) from exc
    return written


def _export_key(project):
    # The hour first: keys sort by age, so the cleanup lists the oldest first.
    return f"{EXPORT_PREFIX}{timezone.now():%Y%m%d%H}/{project.pk}-{uuid.uuid4().hex}.zip"


def _store_zip(path, key, filename, client):
    if client is None:
        destination = os.path.join(str(settings.MEDIA_ROOT), *key.split('/'))
        os.makedirs(os.path.dirname(destination), exist_ok=True)
        shutil.move(path, destination)
        return destination
    client.upload_file(
        path, _bucket(), key,
        ExtraArgs={'ContentType': 'application/zip', 'ContentDisposition': content_disposition_header(True, filename)},
    )
    return key


def _delete_stored(stored, client):
    try:
        if client is None:
            os.remove(stored)
        else:
            client.delete_object(Bucket=_bucket(), Key=stored)
    except Exception as exc:
        logger.warning("clips_zip.delete_failed error=%s", type(exc).__name__)


def build_project_zip(project, clips, request):
    """
    Builds, stores and signs the .zip of ``clips`` (ready clips of
    ``project``). Raises ZipTooLarge, ZipTimeout or StorageUnavailable; the
    temporary folder is deleted either way.
    """
    started = time.monotonic()
    deadline = started + zip_max_seconds()
    try:
        # None: local storage (USE_R2=False).
        client = None if _is_local() else _client()
    except Exception as exc:
        logger.error("clips_zip.client_failed project_id=%s error=%s", project.pk, type(exc).__name__)
        raise StorageUnavailable(type(exc).__name__) from exc
    cleanup_expired_exports(client)
    total = _check_sizes(clips, client)

    tmp_root = _setting('CLIPS_ZIP_TMP_DIR', None) or tempfile.gettempdir()
    if shutil.disk_usage(tmp_root).free < total + DISK_MARGIN_BYTES:
        logger.error("clips_zip.no_disk project_id=%s bytes=%d", project.pk, total)
        raise StorageUnavailable('no_disk')

    workdir = tempfile.mkdtemp(prefix='clips-zip-', dir=tmp_root)
    stored = None
    try:
        path = os.path.join(workdir, 'clips.zip')
        written = _write_zip(path, clips, deadline, client)
        filename = zip_filename(project)
        key = _export_key(project)
        try:
            stored = _store_zip(path, key, filename, client)
        except Exception as exc:
            logger.error("clips_zip.store_failed project_id=%s error=%s", project.pk, type(exc).__name__)
            raise StorageUnavailable(type(exc).__name__) from exc
        ttl = zip_url_ttl_seconds()
        url = signed_url(stored, filename, 'application/zip', ttl, request, client)
    except BaseException:
        if stored is not None:
            _delete_stored(stored, client)
        raise
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    logger.info(
        "clips_zip.built project_id=%s clips=%d bytes=%d duration_s=%.3f",
        project.pk, len(clips), written, time.monotonic() - started,
    )
    return SignedDownload(url=url, filename=filename, expires_at=timezone.now() + timedelta(seconds=ttl))


def cleanup_expired_exports(client, limit=_CLEANUP_BATCH):
    """
    Deletes stored .zips whose links expired (older than their TTL). Best
    effort and bounded: a failure is logged and the request goes on.
    ``client`` is None for local storage.
    """
    cutoff = timezone.now() - timedelta(seconds=zip_url_ttl_seconds())
    deleted = 0
    try:
        if client is None:
            root = os.path.join(str(settings.MEDIA_ROOT), *EXPORT_PREFIX.strip('/').split('/'))
            if not os.path.isdir(root):
                return 0
            for folder in sorted(os.listdir(root)):
                directory = os.path.join(root, folder)
                if not os.path.isdir(directory):
                    continue
                for name in sorted(os.listdir(directory)):
                    path = os.path.join(directory, name)
                    if deleted >= limit:
                        return deleted
                    if os.path.getmtime(path) < cutoff.timestamp():
                        os.remove(path)
                        deleted += 1
                if not os.listdir(directory):
                    os.rmdir(directory)
            return deleted
        listing = client.list_objects_v2(Bucket=_bucket(), Prefix=EXPORT_PREFIX, MaxKeys=limit)
        expired = [
            {'Key': item['Key']} for item in listing.get('Contents', [])
            if item.get('LastModified') and item['LastModified'] < cutoff
        ]
        if expired:
            client.delete_objects(Bucket=_bucket(), Delete={'Objects': expired, 'Quiet': True})
        deleted = len(expired)
    except Exception as exc:
        logger.warning("clips_zip.cleanup_failed error=%s", type(exc).__name__)
    return deleted
