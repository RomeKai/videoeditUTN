import boto3
import logging
import os
from django.conf import settings
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)

class CloudflareR2Manager:
    """
    Service to manage video storage in Cloudflare R2 (S3 Compatible).
    Ensures secure uploads, zero egress fees, and dynamic presigned URL generation.
    Supports a Local Fallback for development without R2 keys.
    """
    
    _r2_client = None

    @classmethod
    def is_mock_mode(cls):
        """Checks if we should use local mock instead of real R2."""
        return not all([
            getattr(settings, 'CLOUDFLARE_R2_ACCESS_KEY_ID', None),
            getattr(settings, 'CLOUDFLARE_R2_SECRET_ACCESS_KEY', None),
            getattr(settings, 'CLOUDFLARE_R2_BUCKET_NAME', None),
            getattr(settings, 'CLOUDFLARE_R2_ENDPOINT_URL', None)
        ])

    @classmethod
    def get_client(cls):
        """Returns a singleton boto3 client configured for Cloudflare R2."""
        if cls.is_mock_mode():
            return None
            
        if cls._r2_client is None:
            # R2 requires the endpoint_url to point to the account-specific R2 URL
            cls._r2_client = boto3.client(
                's3',
                aws_access_key_id=settings.CLOUDFLARE_R2_ACCESS_KEY_ID,
                aws_secret_access_key=settings.CLOUDFLARE_R2_SECRET_ACCESS_KEY,
                region_name=settings.CLOUDFLARE_R2_REGION,
                endpoint_url=settings.CLOUDFLARE_R2_ENDPOINT_URL
            )
        return cls._r2_client

    @classmethod
    def upload_video(cls, local_file_path: str, user_id: str, project_id: str) -> str:
        """
        Uploads a local video to a structured R2 path (or local mock).
        Returns the object key.
        """
        filename = os.path.basename(local_file_path)
        r2_key = f"users/{user_id}/projects/{project_id}/{filename}"
        
        if cls.is_mock_mode():
            # LOCAL MOCK LOGIC (Keep for Dev)
            import shutil
            mock_dest = os.path.join(settings.MEDIA_ROOT, 'r2_mock', r2_key)
            os.makedirs(os.path.dirname(mock_dest), exist_ok=True)
            shutil.copy2(local_file_path, mock_dest)
            logger.info(f"ðŸ“ [R2 MOCK] Saved file locally: {mock_dest}")
            return r2_key

        client = cls.get_client()
        try:
            logger.info(f"ðŸ“¤ Uploading {local_file_path} to Cloudflare R2 bucket {settings.CLOUDFLARE_R2_BUCKET_NAME}...")
            client.upload_file(
                local_file_path, 
                settings.CLOUDFLARE_R2_BUCKET_NAME, 
                r2_key,
                ExtraArgs={'ContentType': 'video/mp4'} # R2 handles ACLs via bucket settings usually
            )
            logger.info(f"âœ… Successfully uploaded to R2: {r2_key}")
            return r2_key
        except ClientError as e:
            logger.error(f"âŒ Failed to upload to R2: {e}")
            raise e

    @classmethod
    def generate_presigned_url(cls, r2_object_key: str, expiration_seconds: int = 3600) -> str:
        """
        Generates a temporary secure URL for viewing/downloading from R2.
        """
        if cls.is_mock_mode():
            return f"{settings.MEDIA_URL}r2_mock/{r2_object_key}"

        client = cls.get_client()
        try:
            # Presigned URLs in R2 work exactly like S3
            url = client.generate_presigned_url(
                'get_object',
                Params={
                    'Bucket': settings.CLOUDFLARE_R2_BUCKET_NAME,
                    'Key': r2_object_key
                },
                ExpiresIn=expiration_seconds
            )
            return url
        except ClientError as e:
            logger.error(f"âŒ Failed to generate presigned URL from R2: {e}")
            return ""


    @classmethod
    def delete_object(cls, r2_object_key: str) -> bool:
        """
        Deletes an object from Cloudflare R2 (or local mock).
        Returns True if successful.
        """
        if cls.is_mock_mode():
            mock_path = os.path.join(settings.MEDIA_ROOT, 'r2_mock', r2_object_key)
            if os.path.exists(mock_path):
                os.remove(mock_path)
                logger.info(f"🗑️ [R2 MOCK] Deleted file: {mock_path}")
                return True
            return False

        client = cls.get_client()
        try:
            logger.info(f"🗑️ Deleting {r2_object_key} from R2...")
            client.delete_object(
                Bucket=settings.CLOUDFLARE_R2_BUCKET_NAME,
                Key=r2_object_key
            )
            return True
        except ClientError as e:
            logger.error(f"❌ Failed to delete from R2: {e}")
            return False
