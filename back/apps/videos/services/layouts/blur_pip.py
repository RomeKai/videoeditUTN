import os
# Silenciar errores de GPU en MediaPipe antes de importar
os.environ['MEDIAPIPE_DISABLE_GPU'] = '1'

from moviepy import CompositeVideoClip, ColorClip
import moviepy.video.fx as vfx 
from .interface import BaseLayout
from ..face_tracker import FaceTracker
import logging

logger = logging.getLogger(__name__)

class BlurredLayout(BaseLayout):
    """
    Layout Profesional 'Blur PIP' (Picture-in-Picture):
    - Fondo borroso escalado.
    - Capa principal nítida con centrado inteligente opcional (FaceTracking).
    """
    def apply(self, clip):
        target_w, target_h = self.target_w, self.target_h
        path = None

        # 1. TRAYECTORIA (OPCIONAL)
        if self.use_facetracking:
            tracker = FaceTracker()
            path = tracker.generate_path(clip, fps=2.0)
            tracker.close()

        # 2. CAPA DE FONDO (Blurred Background)
        # Escalamos el video para que cubra todo el alto (o ancho)
        bg = clip.resized(height=target_h)
        if bg.w < target_w:
            bg = clip.resized(width=target_w)
        
        # Recorte del fondo (Fijo al centro para estabilidad si no hay tracking)
        bg = bg.cropped(x_center=bg.w//2, y_center=bg.h//2, width=target_w, height=target_h)
        
        # Efectos de desenfoque y oscurecimiento
        bg = bg.with_effects([
            vfx.GaussianBlur(sigma=30),
            vfx.MultiplyColor(factor=0.6) # Un poco más claro que antes
        ])

        # 3. CAPA PRINCIPAL (Foreground Sharp)
        # Ajustamos el video para que quepa en el ancho del target
        fg = clip.resized(width=target_w)
        
        if fg.h > target_h:
            # Si el video es más alto que la pantalla, recortamos el alto
            if self.use_facetracking and path:
                # Seguimiento facial en el recorte vertical
                def get_fg_frame(get_frame, t):
                    frame = get_frame(t)
                    _, face_y, _, _ = path.get_at(t)
                    # Escalar face_y al tamaño del clip redimensionado (fg)
                    scale_y = fg.h / clip.h
                    y_center = int(face_y * scale_y)
                    
                    y1 = max(0, min(y_center - (target_h // 2), fg.h - target_h))
                    # Como ya redimensionamos 'fg', necesitamos extraer del frame de 'fg'
                    # Pero fg es un clip derivado, mejor trabajar sobre el frame redimensionado
                    # MoviePy transform nos da el frame del clip actual (fg)
                    return frame[y1:y1+target_h, :]
                
                fg = fg.transform(get_fg_frame)
            else:
                # Recorte central estático
                fg = fg.cropped(y_center=fg.h//2, height=target_h)
        
        fg = fg.with_position("center")

        # 4. COMPOSICIÓN FINAL
        layers = [bg, fg]
        final_composition = CompositeVideoClip(layers, size=(target_w, target_h))
        
        if clip.audio:
            final_composition = final_composition.with_audio(clip.audio)
            
        logger.info(f"✅ BlurredLayout aplicado (FaceTracking: {self.use_facetracking})")
        return final_composition
