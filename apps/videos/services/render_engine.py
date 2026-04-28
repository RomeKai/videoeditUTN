import os
import logging
from django.conf import settings
from django.core.files import File
from moviepy import VideoFileClip
from apps.videos.models import VideoClip
from apps.videos.services.layouts import get_layout_strategy
from apps.videos.services.transcription_engine import TranscriptionEngine
from apps.videos.services.subtitle_engine import SubtitleEngine

logger = logging.getLogger(__name__)

class RenderEngine:
    @staticmethod
    def _get_target_resolution(aspect_ratio_str):
        target_h = 1080  
        if aspect_ratio_str == '9:16': target_w = int(target_h * (9/16)) 
        elif aspect_ratio_str == '1:1': target_w = target_h 
        elif aspect_ratio_str == '16:9': target_w = int(target_h * (16/9)) 
        else: target_w = int(target_h * (9/16)) 
        
        # 🛡️ OBLIGATORIO: Forzar pares para evitar el crash del Códec H.264
        target_w = target_w if target_w % 2 == 0 else target_w + 1
        target_h = target_h if target_h % 2 == 0 else target_h + 1
        return target_w, target_h

    @staticmethod
    def _slice_segments(global_segments, clip_start, clip_end):
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

            # Cargar Video (Subclip)
            original_clip = VideoFileClip(original_path).subclipped(clip_obj.start_time, clip_obj.end_time)
            
            # Aplicar Layout
            target_w, target_h = RenderEngine._get_target_resolution(project.aspect_ratio)
            use_ft = getattr(project, 'use_facetracking', False)
            layout_strategy = get_layout_strategy(project.render_layout, target_w, target_h, use_facetracking=use_ft)
            video_layout_processed = layout_strategy.apply(original_clip)
            
            # Subtítulos
            if getattr(project, 'add_subtitles', True):
                existing_transcription = project.metadata.get('transcription', [])
                
                # Configuramos opciones
                sub_color = getattr(project, 'subtitle_color', '#FFFF00')
                use_emojis = getattr(project, 'subtitle_with_emojis', False)
                sub_size = getattr(project, 'subtitle_size', 'medium')
                sub_pos = getattr(project, 'subtitle_position', 'bottom')
                max_w = getattr(project, 'subtitle_words_per_segment', 3)
                
                # Instanciamos motor para obtener el mapa de emojis
                subtitler = SubtitleEngine(
                    color=sub_color, 
                    with_emojis=use_emojis,
                    size_type=sub_size,
                    position_type=sub_pos
                )
                
                emoji_map = subtitler.EMOJI_MAP if use_emojis else None

                if existing_transcription:
                    # 1. Cortamos los segmentos base (palabras sueltas)
                    raw_segments = RenderEngine._slice_segments(existing_transcription, clip_obj.start_time, clip_obj.end_time)
                    
                    # 2. Agrupamos con la nueva lógica de ruptura por emoji
                    segments = TranscriptionEngine.group_words(raw_segments, max_words=max_w, emoji_map=emoji_map)
                else:
                    # Fallback de respaldo
                    temp_audio_path = os.path.join(temp_dir, f"audio_{clip_obj.id}.mp3")
                    original_clip.audio.write_audiofile(temp_audio_path, codec='mp3', logger=None)
                    transcriber = TranscriptionEngine(model_size="tiny") 
                    raw_segments = transcriber.transcribe(temp_audio_path, word_timestamps=True)
                    segments = TranscriptionEngine.group_words(raw_segments, max_words=max_w, emoji_map=emoji_map)

                if segments:
                    final_clip = subtitler.add_subtitles(video_layout_processed, segments)
                else:
                    final_clip = video_layout_processed
            else:
                final_clip = video_layout_processed

            # 🚀 RENDERIZADO FÍSICO CON PARÁMETROS SEGUROS
            final_clip.write_videofile(
                output_path,
                codec='libx264',
                audio_codec='aac',
                fps=24,
                preset='fast',
                threads=4,
                ffmpeg_params=['-pix_fmt', 'yuv420p', '-profile:v', 'main'],
                logger=None
            )
            
            # Guardar en Storage de Django
            with open(output_path, 'rb') as f:
                clip_obj.output_file.save(filename, File(f), save=True)
                
            clip_obj.status = VideoClip.Status.COMPLETED
            clip_obj.save()
            return True

        except Exception as e:
            logger.error(f"❌ Falló Render {clip_id}: {e}", exc_info=True)
            if clip_obj:
                clip_obj.status = VideoClip.Status.DRAFT
                clip_obj.save()
            raise e
        finally:
            # Limpieza de recursos
            try:
                if original_clip: original_clip.close()
                if final_clip and final_clip != original_clip: final_clip.close()
                if output_path and os.path.exists(output_path): os.remove(output_path)
                if temp_audio_path and os.path.exists(temp_audio_path): os.remove(temp_audio_path)
            except: pass
