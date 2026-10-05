import logging
import os
import shutil
import uuid
import boto3
from botocore.exceptions import ClientError
from django.conf import settings

logger = logging.getLogger(__name__)


def _is_local() -> bool:
    """Returns True when USE_S3=False — all operations work on the local filesystem."""
    return not getattr(settings, 'USE_S3', False)


def _get_r2_client():
    """Creates and returns a boto3 S3 client configured for Cloudflare R2."""
    return boto3.client(
        's3',
        endpoint_url=settings.CLOUDFLARE_R2_ENDPOINT_URL,
        aws_access_key_id=settings.CLOUDFLARE_R2_ACCESS_KEY_ID,
        aws_secret_access_key=settings.CLOUDFLARE_R2_SECRET_ACCESS_KEY,
        region_name=settings.CLOUDFLARE_R2_REGION,
    )


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

        When USE_S3=False, skips R2 and returns the local path as the key.
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
                logger.info(f"[R2 LOCAL] USE_S3=False — saved temp video to permanent storage: {dest_path}")
                return dest_path
            logger.info(f"[R2 LOCAL] USE_S3=False — skipping upload, using local path: {path}")
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
    def generate_presigned_url(object_key: str, expiration_seconds: int = 3600) -> str:
        """
        Generates a temporary presigned URL for a given R2 object key.

        When USE_S3=False, the object_key is the local absolute path;
        returns a /media/ URL relative to MEDIA_URL.
        """
        if _is_local():
            media_root = str(getattr(settings, 'MEDIA_ROOT', ''))
            if object_key.startswith(media_root):
                relative = object_key[len(media_root):].replace('\\', '/')
            else:
                relative = object_key.replace('\\', '/')
            media_url = getattr(settings, 'MEDIA_URL', '/media/')
            return f"{media_url.rstrip('/')}/{relative.lstrip('/')}"

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

        When USE_S3=False, object_key is already a local path — just copies it.
        """
        if _is_local():
            logger.info(f"[R2 LOCAL] USE_S3=False — copying {object_key} → {local_path}")
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
        client = _get_r2_client()
        bucket = settings.CLOUDFLARE_R2_BUCKET_NAME
        try:
            client.delete_object(Bucket=bucket, Key=object_key)
            logger.info(f"🗑️  [R2] Deleted: {object_key}")
        except ClientError as e:
            logger.error(f"❌ [R2] Delete failed for {object_key}: {e}")
            raise
