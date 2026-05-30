# ==========================================
# FILE: ingestion/models.py
# ==========================================
import os
from datetime import datetime
from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, select, func
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()

class User(Base):
    """
    User model representing account details and financial balance.
    Aligned with Gentle-AI SDD Protocol and Engram specifications.
    """
    __tablename__ = 'users'
    
    id = Column(Integer, primary_key=True, index=True)
    credits = Column(Integer, default=100, nullable=False)
    last_active_at = Column(DateTime, nullable=True, onupdate=func.now())
    
    # Relationship
    videos = relationship("Video", back_populates="user", cascade="all, delete-orphan")


class Video(Base):
    """
    Video model representing uploaded media metadata and processing status.
    Aligned with Gentle-AI SDD Protocol and Engram specifications.
    """
    __tablename__ = 'videos'
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    status = Column(String(50), default="pending", nullable=False)  # pending, processing, completed, failed
    original_url = Column(String(1024), nullable=False)
    proxy_url = Column(String(1024), nullable=True)
    duration = Column(Integer, nullable=True)
    width = Column(Integer, nullable=True)
    height = Column(Integer, nullable=True)
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    
    # Relationship
    user = relationship("User", back_populates="videos")


# ==========================================
# FILE: ingestion/utils.py
# ==========================================
import os
import logging
import boto3
from botocore.client import Config
from moviepy.video.io.VideoFileClip import VideoFileClip

logger = logging.getLogger("GentleAI.IngestionWorker.Utils")

def get_r2_client():
    """
    Returns configured boto3 client representing the Cloudflare R2 migration.
    Replaces AWS S3 with Cloudflare R2 storage interactions.
    """
    endpoint_url = os.getenv("R2_ENDPOINT_URL")
    access_key_id = os.getenv("R2_ACCESS_KEY_ID")
    secret_access_key = os.getenv("R2_SECRET_ACCESS_KEY")
    
    if not all([endpoint_url, access_key_id, secret_access_key]):
        logger.warning("R2 configurations are incomplete in the environment variables.")
        
    return boto3.client(
        's3',
        endpoint_url=endpoint_url,
        aws_access_key_id=access_key_id,
        aws_secret_access_key=secret_access_key,
        config=Config(signature_version='s3v4')
    )


def upload_to_r2(local_path: str, r2_key: str) -> str:
    """
    Uploads file to Cloudflare R2 bucket and returns the public domain resource URL.
    """
    bucket_name = os.getenv("R2_BUCKET_NAME", "media-bucket")
    public_domain = os.getenv("R2_PUBLIC_DOMAIN", "https://pub-r2.example.com")
    
    client = get_r2_client()
    client.upload_file(local_path, bucket_name, r2_key)
    
    # Return formatted public CDN URL
    return f"{public_domain.rstrip('/')}/{r2_key}"


# ==========================================
# FILE: ingestion/worker.py
# ==========================================
import os
import uuid
import logging
import requests
from procrastinate import App, PsycopgConnector
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session

# Import models & utils from this ingestion package
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/postgres")
engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

logger = logging.getLogger("GentleAI.IngestionWorker.Tasks")

# Procrastinate App using PostgreSQL connector
# Strategic decision to migrate from Celery to Procrastinate for transaction safety (FOR UPDATE SKIP LOCKED)
app = App(connector=PsycopgConnector())

@app.task(queue="video_ingestion")
def ingest_video_task(video_id: int):
    """
    Worker task:
    1. Locks User row with select_for_update to enforce credit policies and avoid race conditions.
    2. Downloads raw video.
    3. Leverages MoviePy 2.0+ for metadata generation and proxy resolution reduction.
    4. Uploads proxy video to Cloudflare R2 storage.
    5. Clean rolls back and refunds user credits in case of critical error.
    """
    logger.info(f"[Gentle-AI Protocol] Ingesting video ID {video_id}...")
    db_session: Session = SessionLocal()
    
    local_input = None
    local_proxy = None
    clip = None
    proxy_clip = None
    credits_deducted = False
    video_user_id = None
    
    try:
        # Fetch Video metadata record
        video = db_session.get(Video, video_id)
        if not video:
            logger.error(f"[Gentle-AI Protocol] Video with ID {video_id} was not found.")
            return
            
        video_user_id = video.user_id
        
        # 1. Financial lock on User row (select_for_update equivalent)
        user = db_session.execute(
            select(User).filter(User.id == video.user_id).with_for_update()
        ).scalar_one_or_none()
        
        if not user:
            logger.error(f"[Gentle-AI Protocol] User {video.user_id} associated with video {video_id} does not exist.")
            video.status = "failed"
            db_session.commit()
            return
            
        # Cost validation logic
        ingestion_cost = 5
        if user.credits < ingestion_cost:
            logger.warning(f"[Gentle-AI Protocol] User {user.id} has insufficient credits ({user.credits}). Task rejected.")
            video.status = "failed"
            db_session.commit()
            return
            
        # Deduct user credits atomically before processing
        user.credits -= ingestion_cost
        video.status = "processing"
        db_session.commit()
        credits_deducted = True
        logger.info(f"[Gentle-AI Protocol] Successfully locked and deducted 5 credits from User {user.id}.")
        
        # 2. Safely Download Original Video file locally
        local_input = f"/tmp/input_{uuid.uuid4()}.mp4"
        logger.info(f"Downloading original media from {video.original_url} to {local_input}")
        
        if video.original_url.startswith("http://") or video.original_url.startswith("https://"):
            response = requests.get(video.original_url, stream=True, timeout=60)
            response.raise_for_status()
            with open(local_input, "wb") as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)
        else:
            if os.path.exists(video.original_url):
                import shutil
                shutil.copy(video.original_url, local_input)
            else:
                raise FileNotFoundError(f"Original source file not reachable: {video.original_url}")
                
        # 3. Analyze and Process with MoviePy 2.0+
        clip = VideoFileClip(local_input)
        
        video.duration = int(clip.duration)
        video.width = int(clip.size[0])
        video.height = int(clip.size[1])
        
        local_proxy = f"/tmp/proxy_{uuid.uuid4()}.mp4"
        
        # MoviePy 2.0+ resize handler
        if hasattr(clip, "resized"):
            proxy_clip = clip.resized(width=640)
        else:
            proxy_clip = clip.resize(width=640)
            
        logger.info(f"Writing scaled proxy down to 640px width...")
        proxy_clip.write_videofile(
            local_proxy,
            codec="libx264",
            audio_codec="aac",
            temp_audiofile=f"/tmp/temp_audio_{uuid.uuid4()}.m4a",
            remove_temp=True
        )
        
        # 4. Upload Proxy output to Cloudflare R2
        r2_key = f"proxies/{video.user_id}/{uuid.uuid4()}.mp4"
        proxy_url = upload_to_r2(local_proxy, r2_key)
        
        # Save complete parameters back to video
        video.proxy_url = proxy_url
        video.status = "completed"
        db_session.commit()
        logger.info(f"[Gentle-AI Protocol] Video processing complete. Proxy URL: {proxy_url}")
        
    except Exception as exc:
        logger.error(f"[Gentle-AI Protocol] Crucial exception encountered: {str(exc)}. Triggering rollback...")
        db_session.rollback()
        
        # Safe transactional refund logic using User table SELECT FOR UPDATE lock
        if credits_deducted and video_user_id is not None:
            refund_session = SessionLocal()
            try:
                ref_user = refund_session.execute(
                    select(User).filter(User.id == video_user_id).with_for_update()
                ).scalar_one_or_none()
                
                if ref_user:
                    ref_user.credits += 5
                    logger.info(f"[Gentle-AI Protocol] Refunded 5 credits to User {video_user_id}.")
                    
                ref_video = refund_session.get(Video, video_id)
                if ref_video:
                    ref_video.status = "failed"
                    
                refund_session.commit()
            except Exception as refund_err:
                logger.error(f"[Gentle-AI Protocol] Refund transaction failed: {str(refund_err)}")
                refund_session.rollback()
            finally:
                refund_session.close()
        else:
            # Mark video processing failed if user validation/deduction failed initially
            fail_session = SessionLocal()
            try:
                fail_video = fail_session.get(Video, video_id)
                if fail_video:
                    fail_video.status = "failed"
                    fail_session.commit()
            except Exception:
                fail_session.rollback()
            finally:
                fail_session.close()
        raise exc
        
    finally:
        # 5. Clean up open Video clips and close handles to avoid memory leaks
        if clip:
            try:
                clip.close()
            except Exception:
                pass
        if proxy_clip:
            try:
                proxy_clip.close()
            except Exception:
                pass
                
        # Safe deletion of local workspace files
        for temp_path in [local_input, local_proxy]:
            if temp_path and os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                    logger.info(f"Successfully deleted temporal working file: {temp_path}")
                except Exception as clean_exc:
                    logger.warning(f"Could not delete temp file {temp_path}: {str(clean_exc)}")
                    
        db_session.close()