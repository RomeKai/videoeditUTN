from moviepy import CompositeVideoClip, ColorClip
import moviepy.video.fx as vfx 
from .interface import BaseLayout
from ..face_tracker import FaceTracker
import logging

logger = logging.getLogger(__name__)

class BlurredLayout(BaseLayout):
    """
    Layout Profesional 'Blur PIP' (Picture-in-Picture):
    - Fondo borroso anclado dinámicamente al rostro.
    - Capa principal nítida con centrado inteligente.
    - Optimizado para MoviePy v2.0+.
    """
    def apply(self, clip):
        target_w, target_h = self.target_w, self.target_h
        
        # 1. INTELIGENCIA ESPACIAL
        tracker = FaceTracker()
        face_data = tracker.detect_face_center(clip)
        tracker.close()

        # Determinamos el centro de interés (focux_x)
        # Si hay cara, lo usamos; si no, usamos el centro geométrico.
        focus_x = face_data[0] if face_data else clip.w // 2
        focus_y = face_data[1] if face_data else clip.h // 2

        # 2. CAPA DE FONDO (Blurred Background)
        # Escalamos para llenar la altura y luego recortamos el ancho
        bg = clip.resized(height=target_h)
        # El recorte del fondo sigue al sujeto para coherencia de color
        bg_focus_x = int(focus_x * (bg.h / clip.h))
        bg = bg.cropped(x_center=bg_focus_x, width=target_w)
        
        bg = bg.with_effects([
            vfx.GaussianBlur(sigma=30),    # Desenfoque cinemático
            vfx.MultiplyColor(factor=0.5)  # Oscurecer para dar contraste
        ])

        # 3. CAPA PRINCIPAL (Foreground Sharp)
        # Típicamente el video original escalado al ancho del reel
        fg = clip.resized(width=target_w)
        
        # Si el video es más alto que el target (ej. formato 4:5), 
        # hacemos un recorte inteligente centrado en la cara.
        if fg.h > target_h:
            fg_focus_y = int(focus_y * (fg.w / clip.w))
            fg = fg.cropped(y_center=fg_focus_y, height=target_h)
        
        fg = fg.with_position("center")

        # 4. SEPARACIÓN ESTÉTICA (Sombra/Borde sutil)
        # Creamos un borde negro muy fino para separar las capas
        border_size = 4
        shadow = ColorClip(
            size=(fg.w + border_size, fg.h + border_size), 
            color=(0,0,0), 
            duration=clip.duration
        ).with_opacity(0.3).with_position("center")

        # 5. COMPOSICIÓN FINAL
        layers = [bg, shadow, fg]
        final_composition = CompositeVideoClip(layers, size=(target_w, target_h))
        
        if clip.audio:
            final_composition = final_composition.with_audio(clip.audio)
            
        logger.info(f"✅ BlurredLayout aplicado con éxito (Focus X: {focus_x})")
        return final_composition