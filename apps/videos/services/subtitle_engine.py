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

        # Tamaños pro mejorados para visibilidad
        size_map = {"small": 35, "medium": 55, "large": 80}
        self.font_size = font_size or size_map.get(size_type, 55)
        
        # Coordenadas relativas
        pos_map = {"top": 0.15, "center": 0.50, "bottom": 0.85}
        self.y_pos = pos_map.get(position_type, 0.85)

        self.color = color
        self.with_emojis = with_emojis
        self.stroke_color = 'black'
        self.stroke_width = 3

    def _make_text_image_clip(self, text, video_w, is_emoji=False):
        """
        Renderiza texto o emojis usando Pillow para control total de píxeles y evitar recortes.
        """
        try:
            # 1. Seleccionar fuente y tamaño
            font_path = self.emoji_font_path if is_emoji else self.base_font
            size = 180 if is_emoji else self.font_size
            font = ImageFont.truetype(font_path, size)

            # 2. Preparar texto multilínea si es necesario
            text = text.upper() if not is_emoji else text
            lines = [text]
            if not is_emoji and len(text) > 15:
                words = text.split()
                if len(words) > 3:
                    mid = len(words) // 2
                    lines = [" ".join(words[:mid]), " ".join(words[mid:])]

            # 3. Calcular dimensiones con márgenes de seguridad amplios
            # Creamos un canvas temporal para medir
            temp_img = Image.new("RGBA", (video_w, 1000))
            draw = ImageDraw.Draw(temp_img)
            
            max_line_w = 0
            total_h = 0
            line_spacing = 15
            
            for line in lines:
                bbox = draw.textbbox((0, 0), line, font=font, stroke_width=self.stroke_width)
                line_w = bbox[2] - bbox[0]
                line_h = bbox[3] - bbox[1]
                max_line_w = max(max_line_w, line_w)
                total_h += line_h + line_spacing

            # Añadimos un padding generoso para evitar recortes de strokes o descendentes
            padding_v = 60 
            padding_h = 40
            canvas_w = int(max_line_w + padding_h * 2)
            canvas_h = int(total_h + padding_v * 2)
            
            img = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
            draw = ImageDraw.Draw(img)

            # 4. Dibujar centrado
            current_y = padding_v
            for line in lines:
                draw.text(
                    (canvas_w // 2, current_y), 
                    line, 
                    font=font, 
                    fill=self.color if not is_emoji else None,
                    stroke_width=self.stroke_width if not is_emoji else 0,
                    stroke_fill=self.stroke_color if not is_emoji else None,
                    anchor="mt",
                    embedded_color=is_emoji
                )
                bbox = draw.textbbox((0, 0), line, font=font)
                current_y += (bbox[3] - bbox[1]) + line_spacing

            # 5. Recorte final basado en píxeles reales pero con colchón
            final_bbox = img.getbbox()
            if final_bbox:
                # Dejamos 15px de margen extra en el recorte final por seguridad absoluta
                img = img.crop((final_bbox[0]-15, final_bbox[1]-15, final_bbox[2]+15, final_bbox[3]+15))
            
            return ImageClip(np.array(img))

        except Exception as e:
            logger.error(f"Error Pillow render: {e}")
            return None

    def add_subtitles(self, video_clip, segments):
        subtitle_clips = []
        video_w, video_h = video_clip.size
        
        for segment in segments:
            try:
                is_emoji = segment.get("is_emoji", False)
                txt_img_clip = self._make_text_image_clip(segment["text"], video_w, is_emoji=is_emoji)
                
                if txt_img_clip:
                    sub_clip = (txt_img_clip
                                 .with_position(('center', self.y_pos), relative=True)
                                 .with_start(segment["start"])
                                 .with_end(segment["end"]))
                    subtitle_clips.append(sub_clip)
                    
            except Exception as e:
                logger.error(f"❌ Error en segmento: {e}")
                continue

        if not subtitle_clips:
            return video_clip

        final_composition = CompositeVideoClip([video_clip] + subtitle_clips, size=video_clip.size)
        final_composition.duration = video_clip.duration
        
        return final_composition
