import os
import logging
from django.conf import settings
from django.core.files import File
from moviepy import VideoFileClip, CompositeVideoClip
from apps.videos.models import VideoClip
from apps.videos.services.layouts import get_layout_strategy
from apps.videos.services.transcription_engine import TranscriptionEngine
from apps.videos.services.subtitle_engine import SubtitleEngine, StyleConfig
from apps.videos.utils.ffmpeg_utils import FFmpegManager
from apps.videos.utils.moviepy_utils import RENDER_FPS, RENDER_CODEC, RENDER_AUDIO_CODEC, RENDER_PRESET, FFMPEG_PARAMS, ensure_even

logger = logging.getLogger(__name__)

class RenderEngine:
    """
    Core engine responsible for physical video rendering and assembly.
    Handles layout application, subtitle burning, and cloud-native S3 lifecycle.
    """
    @staticmethod
    def _get_target_resolution(aspect_ratio_str: str):
        """Calculates resolution based on aspect ratio, forcing even numbers for H.264."""
        target_h = 1080  
        if aspect_ratio_str == '9:16': target_w = int(target_h * (9/16)) 
        elif aspect_ratio_str == '1:1': target_w = target_h 
        elif aspect_ratio_str == '16:9': target_w = int(target_h * (16/9)) 
        else: target_w = int(target_h * (9/16)) 
        
        # Ensure dimensions are even to prevent H.264 codec crashes
        target_w = ensure_even(target_w)
        target_h = ensure_even(target_h)
        return target_w, target_h

    @staticmethod
    def _slice_segments(global_segments, clip_start, clip_end):
        """Extracts transcription segments that fall within a specific time range."""
        clip_segments = []
        for seg in global_segments:
            if seg['end'] > clip_start and seg['start'] < clip_end:
                new_start = max(0.0, seg['start'] - clip_start)
                new_end = min(clip_end - clip_start, seg['end'] - clip_start)
                if new_end > new_start:
                    clip_segments.append({"text": seg['text'], "start": new_start, "end": new_end})
        return clip_segments

    @staticmethod
    def render_clip(clip_id):
        """
        Full rendering pipeline for a single VideoClip.
        Includes layout processing, subtitle generation, S3 upload, and local cleanup.
        """
        clip_obj, original_clip, final_clip = None, None, None
        output_path, temp_audio_path = None, None

        try:
            clip_obj = VideoClip.objects.get(id=clip_id)
            project = clip_obj.project
            clip_obj.status = VideoClip.Status.RENDERING
            clip_obj.save()

            original_path = project.source_file.path
            filename = f"clip_{clip_obj.id}.mp4"
            temp_dir = os.path.join(settings.MEDIA_ROOT, 'videos', 'clips', 'temp')
            os.makedirs(temp_dir, exist_ok=True)
            output_path = os.path.join(temp_dir, filename)

            # 1. Load Video (MoviePy 2.0 syntax)
            original_clip = VideoFileClip(original_path).subclipped(clip_obj.start_time, clip_obj.end_time)
            
            # 2. Apply Layout Strategy (GoF Strategy Pattern)
            target_w, target_h = RenderEngine._get_target_resolution(project.aspect_ratio)
            use_ft = getattr(project, 'use_facetracking', False)
            gp_pos = getattr(project, 'gameplay_position', 'center')
            cam_sel_pos = getattr(project, 'camera_selection_position', 'center')
            manual_coords = { 'x': project.manual_camera_x, 'y': project.manual_camera_y, 'zoom': project.manual_camera_zoom }
            layout_strategy = get_layout_strategy(project.render_layout, target_w, target_h, use_facetracking=use_ft, gameplay_pos=gp_pos, camera_selection_pos=cam_sel_pos, manual_camera_coords=manual_coords)
            video_layout_processed = layout_strategy.apply(original_clip)
            
            # 3. Subtitle Processing
            if getattr(project, 'add_subtitles', True):
                existing_transcription = project.transcript_data
                
                # Configuration mapping from project defaults
                size_map = {"small": 0.04, "medium": 0.06, "large": 0.09}
                pos_map = {"top": 0.20, "center": 0.50, "bottom": 0.85}
                
                config = StyleConfig(
                    font_path=os.path.join(settings.FONTS_DIR, 'Montserrat-Bold.ttf'),
                    font_size_percent=size_map.get(project.subtitle_size, 0.06),
                    primary_color=project.subtitle_color,
                    y_position_percent=pos_map.get(project.subtitle_position, 0.85)
                )
                
                subtitler = SubtitleEngine(style_config=config)
                max_w = project.subtitle_words_per_segment

                if existing_transcription:
                    raw_segments = RenderEngine._slice_segments(existing_transcription, clip_obj.start_time, clip_obj.end_time)
                    segments = TranscriptionEngine.group_words(raw_segments, max_words=max_w)
                else:
                    # Fallback transcription
                    temp_audio_path = os.path.join(temp_dir, f"audio_{clip_obj.id}.mp3")
                    original_clip.audio.write_audiofile(temp_audio_path, codec='mp3', logger=None)
                    transcriber = TranscriptionEngine(model_size="tiny") 
                    raw_segments = transcriber.transcribe(temp_audio_path, word_timestamps=True)
                    segments = TranscriptionEngine.group_words(raw_segments, max_words=max_w)

                if segments:
                    final_clip = subtitler.add_subtitles(video_layout_processed, segments)
                else:
                    final_clip = video_layout_processed
            else:
                final_clip = video_layout_processed

            # 4. Physical Rendering
            final_clip.write_videofile(
                output_path,
                codec=RENDER_CODEC,
                audio_codec=RENDER_AUDIO_CODEC,
                fps=RENDER_FPS,
                preset=RENDER_PRESET,
                threads=4,
                ffmpeg_params=FFMPEG_PARAMS,
                logger=None
            )

            # --- OPTIONAL: Silence Removal (Jump Cuts) ---
            # IMPORTANT: Must happen BEFORE upload to R2
            if project.remove_silences:
                logger.info(f"âœ‚ï¸ Applying Silence Removal (Jump Cuts) to clip {clip_obj.id}")
                processed_path = output_path.replace('.mp4', '_processed.mp4')
                FFmpegManager.remove_silences_from_video(output_path, processed_path)
                if os.path.exists(processed_path):
                    os.remove(output_path)
                    os.rename(processed_path, output_path)
                    logger.info(f"âœ… Silence removal applied successfully.")
            
            # 5. S3 STORAGE PIPELINE
            from apps.videos.services.storage_service import CloudflareR2Manager
            
            user_id = str(project.uploaded_by.id) if project.uploaded_by else "system"
            s3_key = CloudflareR2Manager.upload_video(
                local_file_path=output_path,
                user_id=user_id,
                project_id=str(project.id)
            )
            
            # 6. Persistence & Status Update
            clip_obj.s3_object_key = s3_key
            clip_obj.status = VideoClip.Status.COMPLETED
            clip_obj.save()
            
            logger.info(f"âœ… Render and Cloud Upload successful: {clip_id}")
            return True

        except Exception as e:
            logger.error(f"âŒ Render Engine failed for clip {clip_id}: {e}", exc_info=True)
            if clip_obj:
                clip_obj.status = VideoClip.Status.DRAFT
                clip_obj.save()
            raise e
            
        finally:
            # 7. GARBAGE COLLECTION (Local Disk Cleanup)
            logger.info("ðŸ§¹ Cleaning up local rendering files...")
            try:
                if original_clip: original_clip.close()
                if final_clip and final_clip != original_clip: final_clip.close()
                
                if output_path and os.path.exists(output_path): 
                    os.remove(output_path)
                if temp_audio_path and os.path.exists(temp_audio_path): 
                    os.remove(temp_audio_path)
            except Exception as cleanup_err:
                logger.warning(f"âš ï¸ Cleanup error: {cleanup_err}")
