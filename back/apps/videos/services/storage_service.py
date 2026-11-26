import logging
import os
import shutil
import uuid
import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from django.conf import settings

logger = logging.getLogger(__name__)


def _is_local() -> bool:
    """Returns True when USE_R2=False — all operations work on the local filesystem."""
    return not getattr(settings, 'USE_R2', False)


def delete_upload(name, project_id=None) -> None:
    """
    Deletes the upload (``source_file``) of a project that was never created.
    The request saves it through Django's storage, under ``MEDIA_ROOT``, with
    USE_R2 too: only the ingestion copies it to R2, so there is no R2 object
    yet. Best effort, like ``delete_preview_files``: a file that cannot be
    deleted is logged and left.
    """
    if not name:
        return
    from apps.videos.models import VideoProject

    storage = VideoProject._meta.get_field('source_file').storage
    try:
        storage.delete(name)
    except Exception as exc:
        logger.warning("upload.cleanup_failed project_id=%s error=%s", project_id, type(exc).__name__)
        return
    logger.info("upload.deleted project_id=%s", project_id)


# Deletes are small requests that some callers make while holding a row lock
# (the preview cleanup): a hung connection must give up in seconds, not after
# boto's defaults. Uploads and downloads of whole videos keep the defaults.
DELETE_CLIENT_CONFIG = Config(connect_timeout=5, read_timeout=15, retries={'max_attempts': 2, 'mode': 'standard'})


def _get_r2_client(config=None):
    """Creates and returns a boto3 S3 client configured for Cloudflare R2."""
    return boto3.client(
        's3',
        endpoint_url=settings.CLOUDFLARE_R2_ENDPOINT_URL,
        aws_access_key_id=settings.CLOUDFLARE_R2_ACCESS_KEY_ID,
        aws_secret_access_key=settings.CLOUDFLARE_R2_SECRET_ACCESS_KEY,
        region_name=settings.CLOUDFLARE_R2_REGION,
        config=config,
    )


def absolute_media_url(path: str, request=None) -> str:
    """
    A local /media/ path as a URL the browser can open from another origin.
    The web app is not served by Django, so a relative path would be asked to
    the web app's origin, which has no such file.

    With the request, the origin is the one the client used (its ``Host``,
    also behind a proxy that forwards it). Without one (a Celery task),
    ``MEDIA_PUBLIC_BASE_URL`` if it is set. Otherwise the path stays relative.
    A URL that already has a scheme is returned as it is.
    """
    if '://' in path:
        return path
    if request is not None:
        return request.build_absolute_uri(path)
    base = (getattr(settings, 'MEDIA_PUBLIC_BASE_URL', '') or '').rstrip('/')
    return f"{base}/{path.lstrip('/')}" if base else path


class CloudflareR2Manager:
    """
    Static manager for all Cloudflare R2 operations.
    Uses the CLOUDFLARE_R2_* settings defined in base.py.
    """

    @staticmethod
    def get_client():
        return _get_r2_client()

    @staticmethod
    def upload_video(local_path: str = None, user_id: str = "system", folder: str = "", **kwargs) -> str:
        """
        Uploads a file to R2 and returns its object key.
        Key format: videos/{user_id}/{folder}/{uuid}.ext

        When USE_R2=False, skips R2 and returns the local path as the key.
        Supports both (local_path, folder) and (local_file_path, project_id) parameter conventions.
        """
        path = local_path or kwargs.get("local_file_path")
        if not path:
            raise ValueError("upload_video requires a local file path.")

        target_folder = folder or kwargs.get("project_id", "")

        if _is_local():
            if "temp" in path:
                ext = os.path.splitext(path)[-1] or ".mp4"
                filename = f"{uuid.uuid4()}{ext}"
                dest_dir = os.path.join(settings.MEDIA_ROOT, "videos", str(user_id), str(target_folder))
                os.makedirs(dest_dir, exist_ok=True)
                dest_path = os.path.join(dest_dir, filename)
                shutil.copy2(path, dest_path)
                logger.info(f"[R2 LOCAL] USE_R2=False — saved temp video to permanent storage: {dest_path}")
                return dest_path
            logger.info(f"[R2 LOCAL] USE_R2=False — skipping upload, using local path: {path}")
            return path

        ext = os.path.splitext(path)[-1] or ".mp4"
        filename = f"{uuid.uuid4()}{ext}"
        parts = ["videos", str(user_id)]
        if target_folder:
            parts.append(str(target_folder))
        parts.append(filename)
        object_key = "/".join(parts)

        client = _get_r2_client()
        bucket = settings.CLOUDFLARE_R2_BUCKET_NAME

        logger.info(f"☁️  [R2] Uploading {path} → {bucket}/{object_key}")
        client.upload_file(path, bucket, object_key)
        logger.info(f"✅ [R2] Upload complete: {object_key}")
        return object_key

    @staticmethod
    def generate_presigned_url(object_key: str, expiration_seconds: int = 3600, request=None) -> str:
        """
        Generates a temporary presigned URL for a given R2 object key.

        When USE_R2=False, the object_key is the local absolute path and the
        URL is its /media/ URL, made absolute by ``absolute_media_url``:
        ``request`` is the request being answered, when there is one. An R2
        URL is already absolute and ignores it.
        """
        if _is_local():
            media_root = str(getattr(settings, 'MEDIA_ROOT', ''))
            if object_key.startswith(media_root):
                relative = object_key[len(media_root):].replace('\\', '/')
            else:
                relative = object_key.replace('\\', '/')
            media_url = getattr(settings, 'MEDIA_URL', '/media/')
            return absolute_media_url(f"{media_url.rstrip('/')}/{relative.lstrip('/')}", request)

        client = _get_r2_client()
        bucket = settings.CLOUDFLARE_R2_BUCKET_NAME

        url = client.generate_presigned_url(
            'get_object',
            Params={'Bucket': bucket, 'Key': object_key},
            ExpiresIn=expiration_seconds,
        )
        return url

    @staticmethod
    def download_file(bucket: str, object_key: str, local_path: str) -> None:
        """
        Downloads an R2 object to a local path.

        When USE_R2=False, object_key is already a local path — just copies it.
        """
        if _is_local():
            logger.info(f"[R2 LOCAL] USE_R2=False — copying {object_key} → {local_path}")
            if object_key != local_path:
                shutil.copy2(object_key, local_path)
            return

        client = _get_r2_client()
        logger.info(f"⬇️  [R2] Downloading {bucket}/{object_key} → {local_path}")
        client.download_file(bucket, object_key, local_path)
        logger.info(f"✅ [R2] Download complete: {local_path}")

    @staticmethod
    def delete_object(object_key: str) -> None:
        """Deletes an object from R2."""
        if _is_local():
            logger.info(f"[R2 LOCAL] Deleting local object: {object_key}")
            if os.path.exists(object_key):
                os.remove(object_key)
            return

        client = _get_r2_client(DELETE_CLIENT_CONFIG)
        bucket = settings.CLOUDFLARE_R2_BUCKET_NAME
        try:
            client.delete_object(Bucket=bucket, Key=object_key)
            logger.info(f"🗑️  [R2] Deleted: {object_key}")
        except ClientError as e:
            logger.error(f"❌ [R2] Delete failed for {object_key}: {type(e).__name__}")
            raise
