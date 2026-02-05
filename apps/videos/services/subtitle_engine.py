from moviepy import TextClip, CompositeVideoClip
import logging
from django.conf import settings
import os

logger = logging.getLogger(__name__)

class SubtitleEngine:
    def __init__(self, font: str = None, font_size: int = 50, color: str = "yellow"):
        # 1. Definir rutas posibles
        custom_font_path = os.path.join(settings.BASE_DIR, 'assets', 'fonts', 'Montserrat-Bold.ttf')
        
        # 2. Lógica de selección de fuente (ROBUSTA)
        if os.path.exists(custom_font_path):
            self.font = custom_font_path
            logger.info(f"✅ Fuente personalizada encontrada: {self.font}")
        else:
            # Fallback seguro para Linux/Docker
            logger.warning(f"⚠️ No se encontró {custom_font_path}. Usando fuente de sistema 'DejaVuSans'.")
            self.font = 'DejaVuSans' 
            
        self.font_size = font_size
        self.color = color
        self.stroke_color = 'black'
        self.stroke_width = 2

    def add_subtitles(self, video_clip, segments):
        logger.info(f"🎨 [SubtitleEngine] Generando texto con fuente: {self.font}")
        
        subtitle_clips = []
        video_width = video_clip.w
        text_max_width = int(video_width * 0.85) # Explicitly cast to int

        for segment in segments:
            try:
                # CORRECCIÓN PARA MOVIEPY V2
                txt_clip = (TextClip(
                    text=segment["text"],
                    font=self.font,  # <--- Aquí usará la ruta o 'DejaVuSans'
                    font_size=self.font_size,
                    color=self.color,
                    # stroke_color=self.stroke_color, # Temporarily removed for diagnosis
                    # stroke_width=self.stroke_width, # Temporarily removed for diagnosis
                    method='caption',
                    size=(text_max_width, None), 
                    text_align="center"
                )
                .with_position(('center', 0.80), relative=True)
                .with_start(segment["start"])
                .with_end(segment["end"]))
                
                subtitle_clips.append(txt_clip)
            except Exception as e:
                # Si falla, imprimimos el error exacto pero no detenemos el loop
                logger.error(f"❌ Error creando subtítulo '{segment['text']}': {e}")
                continue

        if not subtitle_clips:
            logger.warning("⚠️ No se generaron clips de subtítulos.")
            return video_clip

        final_composition = CompositeVideoClip([video_clip] + subtitle_clips)
        final_composition.duration = video_clip.duration
        
        return final_composition