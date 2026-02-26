from moviepy import TextClip, CompositeVideoClip, ImageClip
import logging
from django.conf import settings
import os
import platform
import numpy as np
from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

class SubtitleEngine:
    EMOJI_MAP = {
        "dinero": "💰", "money": "💰", "cash": "💸", "rico": "🤑",
        "fuego": "🔥", "fire": "🔥", "brutal": "🤯", "increíble": "😲",
        "amor": "❤️", "love": "❤️", "triste": "😢", "llorar": "😭",
        "risa": "😂", "lol": "🤣", "jajaja": "😆",
        "tiempo": "⏳", "time": "🕒", "reloj": "⌚",
        "éxito": "🏆", "exito": "🏆", "win": "🥇", "ganar": "🚀",
        "miedo": "😱", "scary": "👻", "muerte": "💀",
        "comida": "🍔", "food": "🍕", "hambre": "🤤",
        "viaje": "✈️", "travel": "🌍", "mundo": "🗺️",
        "idea": "💡", "pensar": "🤔", "wow": "✨",
        "importante": "🚨", "alerta": "⚠️", "stop": "🛑"
    }

    def __init__(self, font: str = None, font_size: int = None, color: str = "#FFFF00", 
                 with_emojis: bool = False, size_type: str = "medium", position_type: str = "bottom"):
        
        self.base_font_path = os.path.join(settings.BASE_DIR, 'assets', 'fonts', 'Montserrat-Bold.ttf')
        
        if platform.system() == "Windows":
            self.base_font = self.base_font_path if os.path.exists(self.base_font_path) else "Arial-Bold"
            self.emoji_font_path = r"C:\Windows\Fonts\seguiemj.ttf"
        else:
            self.base_font = self.base_font_path if os.path.exists(self.base_font_path) else "DejaVuSans-Bold"
            self.emoji_font_path = "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf"

        # Tamaños pro
        size_map = {"small": 30, "medium": 45, "large": 65}
        self.font_size = font_size or size_map.get(size_type, 45)
        
        # Mapeo de 3 Segmentos (Top, Mid, Down)
        # Usamos coordenadas relativas (0.0 a 1.0)
        pos_map = {
            "top": 0.15,    # Segmento Superior
            "center": 0.50, # Segmento Medio
            "bottom": 0.85  # Segmento Inferior
        }
        self.y_pos = pos_map.get(position_type, 0.85)

        self.color = color
        self.with_emojis = with_emojis
        self.stroke_color = 'black'
        self.stroke_width = 2.0

    def _make_emoji_clip(self, emoji_str):
        try:
            # Emoji grande pero acorde al segmento
            logger.info(f"🎨 Creando clip para emoji: {emoji_str}")
            size = 180 
            font = ImageFont.truetype(self.emoji_font_path, size)
            canvas_size = size * 2
            img = Image.new("RGBA", (canvas_size, canvas_size), (0, 0, 0, 0))
            draw = ImageDraw.Draw(img)
            draw.text((canvas_size//2, canvas_size//2), emoji_str, font=font, embedded_color=True, anchor="mm")
            bbox = img.getbbox()
            if bbox:
                img = img.crop((bbox[0]-20, bbox[1]-20, bbox[2]+20, bbox[3]+20))
            return ImageClip(np.array(img))
        except Exception as e:
            logger.error(f"Error emoji Pillow ({emoji_str}): {e}")
            return None

    def add_subtitles(self, video_clip, segments):
        subtitle_clips = []
        video_w, video_h = video_clip.size
        
        for segment in segments:
            try:
                # Caso A: Emoji Solo (Aparece en la posición elegida)
                if segment.get("is_emoji", False):
                    emoji_clip = self._make_emoji_clip(segment["text"])
                    if emoji_clip:
                        # En MoviePy 2.0+, aplicamos posición relativa directamente
                        emoji_pop = (emoji_clip
                                     .with_position(('center', self.y_pos), relative=True)
                                     .with_start(segment["start"])
                                     .with_end(segment["end"]))
                        subtitle_clips.append(emoji_pop)
                        logger.info(f"✅ Emoji añadido a la composición: {segment['text']} en Y={self.y_pos}")
                
                # Caso B: Texto Normal
                else:
                    text_upper = segment["text"].upper()
                    # Reducimos a 0.8 del ancho para evitar que se pegue a los bordes y forzar wrap antes
                    max_text_w = int(video_w * 0.8)
                    
                    txt_clip = TextClip(
                        text=text_upper,
                        font=self.base_font,
                        font_size=self.font_size,
                        color=self.color,
                        stroke_color=self.stroke_color,
                        stroke_width=self.stroke_width,
                        method='caption',
                        text_align='center',
                        size=(max_text_w, None)
                    )
                    
                    # Aumentamos el padding a 60px para evitar recortes de trazos (strokes)
                    # y asegurar que letras como 'G', 'J', 'Y' o el stroke no se corten.
                    padding = 60
                    txt_container = CompositeVideoClip(
                        [txt_clip.with_position('center')],
                        size=(int(txt_clip.w + padding), int(txt_clip.h + padding))
                    ).with_position(('center', self.y_pos), relative=True).with_start(segment["start"]).with_end(segment["end"])
                    
                    subtitle_clips.append(txt_container)

            except Exception as e:
                logger.error(f"❌ Error en segmento: {e}")
                continue

        if not subtitle_clips:
            return video_clip

        # Aseguramos que los clips de subtítulos estén por encima del video
        final_composition = CompositeVideoClip([video_clip] + subtitle_clips, size=video_clip.size)
        final_composition.duration = video_clip.duration
        
        return final_composition