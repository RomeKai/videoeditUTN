import os
import uuid
import logging
from django.conf import settings
from django.core.files import File
from moviepy import VideoFileClip, CompositeVideoClip
from apps.videos.models import VideoClip
from apps.videos.services.layouts import get_layout_strategy
from apps.videos.services.transcription_engine import TranscriptionEngine
from apps.videos.services.subtitle_engine import SubtitleEngine, StyleConfig
from apps.videos.services.watermark import apply_watermark, project_needs_watermark
from apps.videos.utils.ffmpeg_utils import FFmpegManager
from apps.videos.utils.moviepy_utils import RENDER_FPS, RENDER_CODEC, RENDER_AUDIO_CODEC, RENDER_PRESET, FFMPEG_PARAMS, ensure_even

logger = logging.getLogger(__name__)

class RenderEngine:
    """
    Core engine responsible for physical video rendering and assembly.
    Handles layout application, subtitle burning, and cloud-native S3 lifecycle.
    """
    # Frame height per resolution tier (PricingEngine / SubscriptionPlan values).
    RESOLUTION_HEIGHTS = {'720p': 720, '1080p': 1080, '4K': 2160}
    # A project without render_resolution is priced at 720p (views._pricing_resolution),
    # so it renders at 720p too: never deliver more than what was charged.
    DEFAULT_HEIGHT = 720

    @staticmethod
    def _get_target_resolution(aspect_ratio_str: str, resolution: str = None):
        """
        Calculates resolution based on aspect ratio, forcing even numbers for H.264.
        ``resolution`` is the project's ``metadata['render_resolution']`` tier
        (chosen at creation or set by Prompt-to-Edit, within the plan); without
        it the height is 720, the tier the project was charged for.
        """
        target_h = RenderEngine.RESOLUTION_HEIGHTS.get(resolution, RenderEngine.DEFAULT_HEIGHT)
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
    def compose_clip(project, clip_obj, source_path, target_w, target_h, use_facetracking=False,
                     source_height=None, static=False):
        """
        Builds the clip in memory, without writing it: the segment of the source,
        the project's layout at ``target_w`` x ``target_h`` and its subtitles.
        Shared by the final render and the preview (PTE-03). Returns
        ``(original_clip, final_clip)``; the caller closes both.

        ``source_height`` makes FFmpeg decode the source already scaled to that
        height, so the layout works on small frames (the preview uses it: at
        1080p the layout and blur cost several times the whole preview budget).
        ``static`` skips every face and speaker tracking, also in the 'active'
        layout (the preview uses it).
        """
        # 1. Load Video (MoviePy 2.0 syntax)
        decode_size = (None, int(source_height)) if source_height else None
        original_clip = VideoFileClip(source_path, target_resolution=decode_size).subclipped(
            clip_obj.start_time, clip_obj.end_time,
        )
        try:
            # 2. Apply Layout Strategy (GoF Strategy Pattern)
            gp_pos = getattr(project, 'gameplay_position', 'center')
            cam_sel_pos = getattr(project, 'camera_selection_position', 'center')
            manual_coords = { 'x': project.manual_camera_x, 'y': project.manual_camera_y, 'zoom': project.manual_camera_zoom }
            layout_strategy = get_layout_strategy(project.render_layout, target_w, target_h, use_facetracking=use_facetracking, gameplay_pos=gp_pos, camera_selection_pos=cam_sel_pos, manual_camera_coords=manual_coords, editing_style=getattr(project, 'editing_style', None), static=static, layout_options=project.layout_options())
            video_layout_processed = layout_strategy.apply(original_clip)

            # 3. Subtitle Processing
            if not getattr(project, 'add_subtitles', True):
                return original_clip, video_layout_processed

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
                # Every pipeline project is transcribed before it has clips.
                # Transcribing again here called the provider with no capacity
                # reservation and no AIUsageRecord (a cost nobody saw), so a
                # clip without a transcript renders without subtitles instead.
                logger.warning(
                    "render.subtitles_skipped clip_id=%s reason=no_transcript", clip_obj.id,
                )
                segments = []

            if segments:
                return original_clip, subtitler.add_subtitles(video_layout_processed, segments)
            return original_clip, video_layout_processed
        except Exception:
            original_clip.close()
            raise

    @staticmethod
    def render_clip(clip_id, claim_token=None):
        """
        Full rendering pipeline for a single VideoClip.
        Includes layout processing, subtitle generation, S3 upload, and local cleanup.

        ``claim_token`` is the owner token render_clip_task wrote when it
        claimed the clip (None: a RENDERING clip with no owner). The result is
        saved only while that claim still holds: a redelivered copy of the
        task may have taken the clip over meanwhile. Returns True when the
        clip was saved COMPLETED; False when it was no longer this delivery's,
        after deleting the upload that nobody will use.
        """
        clip_obj, original_clip, final_clip, export_clip = None, None, None, None
        output_path, temp_audio_path = None, None
        s3_key, thumbnail_key, saved = None, None, False

        try:
            clip_obj = VideoClip.objects.get(id=clip_id)
            project = clip_obj.project
            # No save here: render_clip_task's claim already left the clip
            # RENDERING, and a full save would write back a stale owner token.

            original_path = project.source_file.path
            # One file per delivery: a redelivered copy may render the same
            # clip at the same time on this host.
            filename = f"clip_{clip_obj.id}_{uuid.uuid4().hex}.mp4"
            temp_dir = os.path.join(settings.MEDIA_ROOT, 'videos', 'clips', 'temp')
            os.makedirs(temp_dir, exist_ok=True)
            output_path = os.path.join(temp_dir, filename)

            # 1-3. Load the segment, apply the layout and burn the subtitles
            target_w, target_h = RenderEngine._get_target_resolution(
                project.aspect_ratio, (project.metadata or {}).get('render_resolution'),
            )
            original_clip, final_clip = RenderEngine.compose_clip(
                project, clip_obj, original_path, target_w, target_h,
                use_facetracking=getattr(project, 'use_facetracking', False),
            )

            # The plan's watermark (PAY-01): decided here, never by the request. If it
            # cannot be applied the render fails; the clip is not delivered unmarked.
            export_clip = apply_watermark(final_clip) if project_needs_watermark(project) else final_clip

            # 4. Physical Rendering
            export_clip.write_videofile(
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
            
            # Its thumbnail, from the file still on this disk. Best effort:
            # None when it cannot be made, and the clip is delivered all the same.
            from apps.videos.services.clip_media import make_clip_thumbnail

            thumbnail_key = make_clip_thumbnail(
                clip_id, output_path, s3_key, clip_obj.end_time - clip_obj.start_time,
            )

            # 6. Persistence & Status Update, only while this delivery owns
            # the clip. COMPLETED is final: the claim and its start count go.
            saved = VideoClip.objects.filter(
                pk=clip_id, status=VideoClip.Status.RENDERING, render_claim_token=claim_token,
            ).update(
                s3_object_key=s3_key,
                thumbnail_object_key=thumbnail_key,
                status=VideoClip.Status.COMPLETED,
                render_claim_token=None,
                render_unfinished_starts=0,
            ) == 1
            if not saved:
                logger.warning("render.claim_lost clip_id=%s", clip_id)
                return False

            logger.info(f"âœ… Render and Cloud Upload successful: {clip_id}")
            return True

        except Exception as e:
            logger.error(f"âŒ Render Engine failed for clip {clip_id}: {type(e).__name__}")
            # The clip stays RENDERING: render_clip_task holds the claim and
            # decides (hand it back for a retry, or FAILED via on_failure).
            raise e
            
        finally:
            if s3_key and not saved:
                RenderEngine._discard_unsaved_upload(clip_id, s3_key, thumbnail_key)
            # 7. GARBAGE COLLECTION (Local Disk Cleanup)
            logger.info("ðŸ§¹ Cleaning up local rendering files...")
            try:
                if original_clip: original_clip.close()
                if final_clip and final_clip != original_clip: final_clip.close()
                if export_clip and export_clip is not final_clip: export_clip.close()
                
                if output_path and os.path.exists(output_path): 
                    os.remove(output_path)
                if temp_audio_path and os.path.exists(temp_audio_path): 
                    os.remove(temp_audio_path)
            except Exception as cleanup_err:
                logger.warning(f"âš ï¸ Cleanup error: {type(cleanup_err).__name__}")

    @staticmethod
    def _discard_unsaved_upload(clip_id, s3_key, thumbnail_key=None):
        """
        Deletes an upload this delivery did not save (the clip was taken over,
        or the save failed), so it is not left orphaned in R2. Kept if the clip
        points to it after all (a save that committed before its error) or if
        that cannot be checked.
        """
        from apps.videos.services.storage_service import CloudflareR2Manager

        try:
            if VideoClip.objects.filter(pk=clip_id, s3_object_key=s3_key).exists():
                return
            CloudflareR2Manager.delete_object(s3_key)
            if thumbnail_key:
                CloudflareR2Manager.delete_object(thumbnail_key)
        except Exception as exc:
            logger.warning("render.orphan_not_deleted clip_id=%s error=%s", clip_id, type(exc).__name__)
