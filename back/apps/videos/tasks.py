import os
import uuid
import logging
from celery import shared_task
from django.conf import settings
from django.core.files import File

# Importamos Modelos
from apps.videos.models import VideoProject, VideoClip
from apps.payments.models import Transaction

# Importamos Servicios (SelectionEngine suele ser seguro, TranscriptionEngine lo cargaremos lazy por si acaso)
from apps.videos.services.selection_engine import SelectionEngine

logger = logging.getLogger(__name__)

def download_from_youtube(url, output_folder):
    import yt_dlp
    
    filename = f"{uuid.uuid4()}.mp4"
    output_path = os.path.join(output_folder, filename)
    
    ydl_opts = {
        'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
        'outtmpl': output_path,
        'quiet': True,
        'no_warnings': True,
        'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'referer': 'https://www.google.com/',
    }
    
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])
        
    return output_path

import time
import random
from datetime import timedelta
from django.utils import timezone
from django.db import transaction
from django.core.cache import cache
from apps.videos.models import VideoProject, VideoClip, ScheduledPost

from backend.celery import app as celery_app

logger = logging.getLogger(__name__)

class ThirdPartyAPIError(Exception):
    """Exception raised when a social media API fails."""
    pass

@celery_app.task
def dispatch_scheduled_posts_batch():
    """
    Orchestrator: Dispatches posts scheduled for the next hour.
    Runs every hour via Celery Beat.
    """
    now = timezone.now()
    window_end = now + timedelta(hours=1)
    
    # Batch query for scheduled posts in the next hour
    scheduled_posts = ScheduledPost.objects.filter(
        status=ScheduledPost.Status.SCHEDULED,
        publish_at__range=(now, window_end)
    )
    
    count = 0
    for post in scheduled_posts:
        with transaction.atomic():
            # Lock the row for update and check status again to prevent race conditions
            post_to_queue = ScheduledPost.objects.select_for_update().get(id=post.id)
            if post_to_queue.status == ScheduledPost.Status.SCHEDULED:
                post_to_queue.status = ScheduledPost.Status.QUEUED
                post_to_queue.save()
                
                # Send to worker with specific ETA
                upload_to_social_network.apply_async(
                    args=[post_to_queue.id],
                    eta=post_to_queue.publish_at
                )
                count += 1
    
    logger.info(f"🚀 [DISPATCHER] Queued {count} posts for distribution.")
    return f"Queued {count} posts."

@celery_app.task(bind=True, max_retries=3)
def upload_to_social_network(self, post_id):
    """
    Worker: Uploads a video clip via Ayrshare API.
    Lifecycle:
    1. Lock verification (Redis).
    2. S3 Presigned URL generation (Short-lived).
    3. Ayrshare delegation with User Profile Key.
    4. Exponential Backoff on failure.
    """
    from apps.integrations.ayrshare_api import AyrshareClient, AyrshareAPIError
    from apps.videos.services.storage_service import CloudflareR2Manager

    lock_id = f"lock_post_publish_{post_id}"
    if not cache.add(lock_id, "locked", 300):
        logger.warning(f"🔒 [WORKER] Aborting post {post_id}: Already running.")
        return "Locked"

    try:
        with transaction.atomic():
            post = ScheduledPost.objects.select_for_update().get(id=post_id)
            if post.status == ScheduledPost.Status.PUBLISHED:
                return "Already Published"
            
            # Validation: Does the workspace have an Ayrshare Key?
            workspace = post.video_clip.project.workspace
            profile_key = workspace.ayrshare_profile_key
            
            if not profile_key:
                logger.error(f"❌ [WORKER] Post {post_id} FAILED: No ayrshare_profile_key for workspace {workspace.id}")
                post.status = ScheduledPost.Status.FAILED
                post.error_log = "Error: Ayrshare Profile Key not configured in workspace."
                post.save()
                return "Configuration Missing"

            post.status = ScheduledPost.Status.PROCESSING
            post.save()

        # 1. Generate S3 Presigned URL (Valid for 1 hour)
        # This URL is what Ayrshare will use to download and re-upload the video.
        s3_key = post.video_clip.s3_object_key
        if not s3_key:
            raise Exception("No s3_object_key found for the video clip.")

        media_url = CloudflareR2Manager.generate_presigned_url(s3_key, expiration_seconds=3600)
        
        # 2. Call Ayrshare API
        platform_map = {
            'TIKTOK': 'tiktok',
            'INSTAGRAM_REELS': 'instagram',
            'YOUTUBE_SHORTS': 'youtube'
        }
        target_platform = platform_map.get(post.platform, 'tiktok')
        
        # Priority: 1. AI Generated Caption, 2. Project Metadata, 3. Default
        caption = post.generated_caption or post.video_clip.project.metadata.get('caption')
        if not caption:
            caption = f"Check this out! #Viral #{target_platform}"

        response = AyrshareClient.send_post(
            profile_key=profile_key,
            s3_media_url=media_url,
            caption=caption,
            platforms=[target_platform]
        )

        # 3. Success state
        post.status = ScheduledPost.Status.PUBLISHED
        post.error_log = f"Ayrshare ID: {response.get('id')}"
        post.save()
        logger.info(f"✅ [WORKER] Post {post_id} published via Ayrshare.")
        
        return f"Published via Ayrshare: {target_platform}"

    except AyrshareAPIError as exc:
        # Resilience: Exponential Backoff for 3rd party instability
        logger.error(f"⚠️ [WORKER] Ayrshare API Error: {exc}")
        post.retry_count += 1
        post.error_log = f"Retry {post.retry_count}: {str(exc)}"
        post.save()
        
        # Backoff: 60s, 360s, 1200s...
        raise self.retry(exc=exc, countdown=60 * (2 ** self.request.retries))

    except Exception as e:
        logger.error(f"❌ [WORKER] Critical failure: {e}")
        post.status = ScheduledPost.Status.FAILED
        post.error_log = f"Critical: {str(e)}"
        post.save()
        return "Failed"

    finally:
        cache.delete(lock_id)

@celery_app.task(bind=True, max_retries=3)
def process_video_seo(self, post_id):
    """
    Task: Generates viral SEO metadata for a scheduled post.
    1. Security Shield check.
    2. Extracts transcript.
    3. Calls SEOOptimizationService.
    """
    from apps.core.security import AI_Security_Shield, UnsafeContentError
    from apps.videos.services.seo_engine import SEOOptimizationService
    
    try:
        post = ScheduledPost.objects.select_related('video_clip__project').get(id=post_id)
        project = post.video_clip.project
        
        transcript = project.metadata.get('full_text', '')
        if not transcript:
            segments = project.metadata.get('transcription', [])
            transcript = " ".join([s.get('text', '') for s in segments])

        if not transcript:
            logger.warning(f"⚠️ [SEO] No transcript found for post {post_id}. Aborting.")
            return "No Transcript"

        # --- REQUERIMIENTO: SECURITY SHIELD (MODERATION) ---
        # First line of defense
        try:
            AI_Security_Shield.check_content_safety(transcript)
        except UnsafeContentError as safety_exc:
            logger.error(f"🛡️ [SEO SECURITY] Safety violation for post {post_id}: {safety_exc}")
            post.status = ScheduledPost.Status.FAILED
            post.error_log = f"Violación de seguridad: {str(safety_exc)}"
            post.save()
            return "Security Abort"

        # 1. Generate Metadata via AI
        seo_service = SEOOptimizationService()
        metadata = seo_service.generate_metadata(
            transcript_text=transcript,
            target_niche=project.metadata.get('niche', 'general')
        )

        # 2. Format Caption (Body + Hashtags)
        formatted_hashtags = " ".join([f"#{h.strip()}" for h in metadata.hashtags])
        final_caption = f"{metadata.description_body}\n\n.\n.\n{formatted_hashtags}"

        # 3. Save to ScheduledPost
        post.generated_caption = final_caption
        post.generated_hashtags = metadata.hashtags
        
        # Save additional tweaks in error_log or a dedicated field if exists
        # For now, let's keep it in the main caption
        post.save()

        logger.info(f"✅ [SEO] Metadata generated for post {post_id}")
        return f"SEO Success: {metadata.viral_title}"

    except Exception as e:
        logger.error(f"❌ [SEO] Failed for post {post_id}: {e}")
        # Retry for OpenAI timeouts or network issues
        raise self.retry(exc=e, countdown=60)

@celery_app.task
def render_clip_task(clip_id):
    """
    Task wrapper for the RenderEngine.render_clip method.
    Allows for asynchronous rendering of individual clips.
    """
    logger.info(f"🎬 [RENDER TASK START] Rendering Clip {clip_id}")
    from apps.videos.services.render_engine import RenderEngine
    try:
        RenderEngine.render_clip(clip_id)
        return f"Clip {clip_id} rendered successfully"
    except Exception as e:
        logger.error(f"❌ [RENDER TASK FAILED] {clip_id}: {e}")
        return f"Failed {clip_id}: {e}"

@celery_app.task(bind=True, max_retries=2)
def render_video_segments(self, project_id):
    """
    Task: High-Quality Non-Linear Rendering (Paper Edit Phase 2)
    1. Download Original from S3.
    2. Extract and concatenate approved segments (Float precision).
    3. Apply Layout & Subtitles.
    4. S3 Upload & Final Cleanup.
    """
    logger.info(f"🎬 [FINAL RENDER] Starting for project {project_id}")
    from apps.videos.services.storage_service import CloudflareR2Manager
    from apps.videos.services.render_engine import RenderEngine
    from moviepy import VideoFileClip, concatenate_videoclips
    import tempfile

    try:
        project = VideoProject.objects.get(id=project_id)
        approved_segments = project.metadata.get('approved_segments', [])
        
        if not approved_segments:
            raise Exception("No segments approved for rendering.")

        # 1. DOWNLOAD ORIGINAL FROM S3 (Temporary)
        s3_key = project.original_s3_key
        if not s3_key: raise Exception("No original_s3_key found.")
        
        # Use tempfile to ensure cleanup
        with tempfile.NamedTemporaryFile(suffix='.mp4', delete=False) as tmp_high_res:
            CloudflareR2Manager.get_client().download_file(
                settings.AWS_STORAGE_BUCKET_NAME, 
                s3_key, 
                tmp_high_res.name
            )
            local_high_res_path = tmp_high_res.name

        # 2. NON-LINEAR EDITING (Cuts)
        with VideoFileClip(local_high_res_path) as original_clip:
            subclips = []
            for seg in approved_segments:
                start = float(seg['start'])
                end = float(seg['end'])
                # MoviePy 2.0+ subclipped method
                subclips.append(original_clip.subclipped(start, end))

            # Concatenate all approved parts
            edited_clip = concatenate_videoclips(subclips)
            
            # 3. APPLY LAYOUT & SUBTITLES (Reuse RenderEngine Logic)
            target_w, target_h = RenderEngine._get_target_resolution(project.aspect_ratio)
            use_ft = project.use_facetracking
            gp_pos = project.gameplay_position
            
            from apps.videos.services.layouts import get_layout_strategy
            layout_strategy = get_layout_strategy(project.render_layout, target_w, target_h, use_facetracking=use_ft, gameplay_pos=gp_pos)
            video_layout_processed = layout_strategy.apply(edited_clip)

            # Burn Subtitles if requested
            if project.add_subtitles:
                from apps.videos.services.subtitle_engine import SubtitleEngine, StyleConfig
                # Mapping settings
                size_map = {"small": 0.04, "medium": 0.06, "large": 0.09}
                pos_map = {"top": 0.20, "center": 0.50, "bottom": 0.85}
                
                sub_config = StyleConfig(
                    font_path='Montserrat-Bold.ttf',
                    font_size_percent=size_map.get(project.subtitle_size, 0.06),
                    primary_color=project.subtitle_color,
                    y_position_percent=pos_map.get(project.subtitle_position, 0.85)
                )
                
                subtitler = SubtitleEngine(style_config=sub_config)
                
                # IMPORTANT: In Paper Edit, the transcription timings must be RE-CALCULATED
                # because the final video is shorter. For simplicity, we use the original text
                # from the segments provided by the user.
                final_segments = []
                curr_t = 0.0
                for seg in approved_segments:
                    dur = float(seg['end']) - float(seg['start'])
                    final_segments.append({"text": seg.get('text', ''), "start": curr_t, "end": curr_t + dur})
                    curr_t += dur
                
                final_clip = subtitler.add_subtitles(video_layout_processed, final_segments)
            else:
                final_clip = video_layout_processed

            # 4. EXPORT & S3 UPLOAD
            output_filename = f"final_{project.id}.mp4"
            output_dir = os.path.join(settings.MEDIA_ROOT, 'videos', 'final')
            os.makedirs(output_dir, exist_ok=True)
            output_path = os.path.join(output_dir, output_filename)

            final_clip.write_videofile(
                output_path,
                codec='libx264',
                audio_codec='aac',
                fps=24,
                preset='slow', # High quality for final
                threads=4,
                logger=None
            )

            user_id = str(project.uploaded_by.id) if project.uploaded_by else "system"
            final_s3_key = CloudflareR2Manager.upload_video(output_path, user_id, f"{project.id}/final")

            # 5. FINAL PERSISTENCE
            # We create a final VideoClip record to represent the full edited video
            final_clip_obj = VideoClip.objects.create(
                project=project,
                title="VIDEO FINAL EDITADO",
                start_time=0,
                end_time=edited_clip.duration,
                s3_object_key=final_s3_key,
                status=VideoClip.Status.COMPLETED
            )

            project.status = VideoProject.Status.COMPLETED
            project.save()

            # Clean up local output
            if os.path.exists(output_path): os.remove(output_path)
            if os.path.exists(local_high_res_path): os.remove(local_high_res_path)

            logger.info(f"✅ [FINAL RENDER] Success for project {project_id}")
            return f"Render Success: {final_s3_key}"

    except Exception as e:
        logger.error(f"❌ [FINAL RENDER ERROR] {e}", exc_info=True)
        if 'project' in locals():
            project.status = VideoProject.Status.FAILED
            project.save()
        raise self.retry(exc=e, countdown=60)

@celery_app.task(bind=True, max_retries=2)
def process_initial_ingestion(self, project_id):
    """
    Multimodal Ingestion Pipeline (Paper Edit V1 + Video Sync)
    1. Download/Obtain Source
    2. Parallel: Transcription (Whisper) & Proxy Generation (FFmpeg)
    3. AI Selection & Rationale
    4. S3 Offloading
    5. State Bifurcation (Bypass vs Awaiting Approval)
    """
    logger.info(f"🚀 [INGESTION] Starting project {project_id}")
    from apps.videos.services.storage_service import CloudflareR2Manager
    from apps.videos.services.transcription_engine import TranscriptionEngine
    import subprocess

    try:
        project = VideoProject.objects.get(id=project_id)
        project.status = VideoProject.Status.INGESTING
        project.save()

        # --- 1. OBTAIN SOURCE ---
        if not project.source_file and project.video_url:
            logger.info(f"⬇️ Downloading from URL: {project.video_url}")
            download_dir = os.path.join(settings.MEDIA_ROOT, 'videos', 'raw', 'downloads')
            os.makedirs(download_dir, exist_ok=True)
            local_path = download_from_youtube(project.video_url, download_dir)
            with open(local_path, 'rb') as f:
                project.source_file.save(os.path.basename(local_path), File(f), save=True)
            if os.path.exists(local_path): os.remove(local_path)

        source_path = project.source_file.path
        temp_dir = os.path.dirname(source_path)

        # --- 2. UPLOAD ORIGINAL TO S3 ---
        user_id = str(project.uploaded_by.id) if project.uploaded_by else "system"
        orig_s3_key = CloudflareR2Manager.upload_video(source_path, user_id, str(project.id))
        project.original_s3_key = orig_s3_key
        project.save()

        # --- 3. PROXY GENERATION (FFmpeg) ---
        proxy_filename = f"proxy_{project.id}.mp4"
        proxy_local_path = os.path.join(temp_dir, proxy_filename)

        logger.info(f"🎞️ Generating Web Proxy: {proxy_local_path}")
        ffmpeg_cmd = [
            'ffmpeg', '-y', '-i', source_path,
            '-vf', 'scale=-2:480',
            '-vcodec', 'libx264', '-profile:v', 'main', '-crf', '28',
            '-acodec', 'aac', '-b:a', '96k',
            proxy_local_path
        ]

        try:
            subprocess.run(ffmpeg_cmd, check=True, capture_output=True)
            # Upload Proxy to S3
            proxy_s3_key = CloudflareR2Manager.upload_video(proxy_local_path, user_id, str(project.id))
            project.proxy_s3_key = proxy_s3_key
            logger.info(f"✅ Proxy uploaded: {proxy_s3_key}")
        except subprocess.CalledProcessError as e:
            logger.error(f"❌ FFmpeg Proxy failed: {e.stderr.decode()}")
            # We don't abort the whole ingestion if proxy fails, but it's bad for UX
        finally:
            if os.path.exists(proxy_local_path): os.remove(proxy_local_path)

        # --- 4. TRANSCRIPTION ---
        logger.info("🧠 Transcribing...")
        transcriber = TranscriptionEngine(model_size="base")
        segments = transcriber.transcribe(source_path, word_timestamps=True)
        full_text = " ".join([seg['text'] for seg in segments])

        project.metadata['full_text'] = full_text
        project.metadata['transcription'] = segments

        # --- 5. AI SELECTION (RATIONALE) ---
        from moviepy import VideoFileClip
        with VideoFileClip(source_path) as clip:
            duration = clip.duration
            project.metadata['duration'] = duration
            project.metadata['resolution'] = list(clip.size)

        ai_suggestions = SelectionEngine.select_viral_clips(
            transcription_data=project.metadata,
            project_title=project.title,
            duration=duration,
            intelligence_level=project.intelligence_level
        )

        project.ai_rationale_log = {"suggestions_count": len(ai_suggestions), "raw_ai_output": ai_suggestions}

        created_clips = []
        for clip_data in ai_suggestions:
            clip_obj = VideoClip.objects.create(
                project=project,
                title=clip_data.get('title', 'Clip sugerido'),
                start_time=clip_data.get('start', 0.0),
                end_time=clip_data.get('end', 10.0),
                virality_score=clip_data.get('virality_score', 0),
                ai_reasoning=clip_data.get('reasoning', ''),
                status='draft'
            )
            created_clips.append(clip_obj)

        # --- 6. STATE BIFURCATION ---
        if project.auto_render_bypass:
            logger.info("⏩ Auto-render bypass active. Rendering clips...")
            project.status = VideoProject.Status.RENDERING
            project.save()
            from apps.videos.services.render_engine import RenderEngine
            for clip in created_clips:
                try:
                    RenderEngine.render_clip(clip.id)
                except Exception as e:
                    logger.error(f"⚠️ Render failed for clip {clip.id}: {e}")
            project.status = VideoProject.Status.COMPLETED
        else:
            logger.info("⏳ Ingestion complete. Awaiting user approval.")
            project.status = VideoProject.Status.AWAITING_APPROVAL

        project.save()
        return f"Ingestion Success {project_id}"

    except Exception as e:
        logger.error(f"❌ [INGESTION ERROR] {e}", exc_info=True)
        if 'project' in locals():
            project.status = VideoProject.Status.FAILED
            project.save()
        raise self.retry(exc=e, countdown=60)