from django.conf import settings
import boto3

class CloudflareR2Storage:
    def __init__(self):
        self.client = boto3.client(
            's3',
            endpoint_url=f'https://{settings.CLOUDFLARE_ACCOUNT_ID}.r2.cloudflarestorage.com',
            aws_access_key_id=settings.CLOUDFLARE_ACCESS_KEY_ID,
            aws_secret_access_key=settings.CLOUDFLARE_SECRET_ACCESS_KEY
        )

    def upload_video(self, file_path, object_name):
        self.client.upload_file(file_path, settings.BUCKET_NAME, object_name)
        return f'{settings.PUBLIC_URL}/{object_name}'
