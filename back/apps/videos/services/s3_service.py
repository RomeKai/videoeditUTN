import boto3
import logging
import os
from django.conf import settings
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)

class S3StorageManager:
    """
    Service to manage video storage in AWS S3.
    Ensures secure uploads and dynamic presigned URL generation.
    """
    
    _s3_client = None

    @classmethod
    def get_client(cls):
        """Returns a singleton boto3 client."""
        if cls._s3_client is None:
            cls._s3_client = boto3.client(
                's3',
                aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
                aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
                region_name=settings.AWS_S3_REGION_NAME
            )
        return cls._s3_client

    @classmethod
    def upload_video(cls, local_file_path: str, user_id: str, project_id: str) -> str:
        """
        Uploads a local video to a structured S3 path.
        Returns the object key (path in S3).
        """
        filename = os.path.basename(local_file_path)
        s3_key = f"users/{user_id}/projects/{project_id}/{filename}"
        
        client = cls.get_client()
        try:
            logger.info(f"📤 Uploading {local_file_path} to S3 bucket {settings.AWS_STORAGE_BUCKET_NAME}...")
            client.upload_file(
                local_file_path, 
                settings.AWS_STORAGE_BUCKET_NAME, 
                s3_key,
                ExtraArgs={'ACL': 'private', 'ContentType': 'video/mp4'}
            )
            logger.info(f"✅ Successfully uploaded to: {s3_key}")
            return s3_key
        except ClientError as e:
            logger.error(f"❌ Failed to upload to S3: {e}")
            raise e

    @classmethod
    def generate_presigned_url(cls, s3_object_key: str, expiration_seconds: int = 3600) -> str:
        """
        Generates a temporary secure URL for viewing/downloading the video.
        """
        client = cls.get_client()
        try:
            url = client.generate_presigned_url(
                'get_object',
                Params={
                    'Bucket': settings.AWS_STORAGE_BUCKET_NAME,
                    'Key': s3_object_key
                },
                ExpiresIn=expiration_seconds
            )
            return url
        except ClientError as e:
            logger.error(f"❌ Failed to generate presigned URL: {e}")
            return ""
