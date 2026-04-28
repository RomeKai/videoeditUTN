import os
# Silenciar errores de GPU en MediaPipe antes de importar
os.environ['MEDIAPIPE_DISABLE_GPU'] = '1'

from moviepy import ColorClip, CompositeVideoClip
from .interface import BaseLayout
from ..face_tracker import FaceTracker
import logging

logger = logging.getLogger(__name__)

class FillLayout(BaseLayout):
    """
    Layout 'Smart Fill' (Horizontal to Vertical):
    - Si use_facetracking=True: Detecta el rostro para centrar el recorte.
    - Si use_facetracking=False: Recorte central estático.
    """
    def apply(self, clip):
        target_w, target_h = self.target_w, self.target_h
        aspect_ratio = target_w / target_h
        
        path = None
        zoom_factor = 1.0

        # 1. TRAYECTORIA (OPCIONAL)
        if self.use_facetracking:
            tracker = FaceTracker()
            path = tracker.generate_path(clip, fps=2.0)
            tracker.close()

            # DETERMINAR ZOOM
            avg_w, avg_h = path.get_average_size()
            face_ratio = avg_h / clip.h if avg_h > 0 else 0
            if 0 < face_ratio < 0.15:
                zoom_factor = 1.3

        # 2. MATEMÁTICA DE RECORTE
        crop_w = int((clip.h * aspect_ratio) / zoom_factor)
        crop_h = int(clip.h / zoom_factor)

        if crop_w > clip.w:
            crop_w = clip.w
            crop_h = int(crop_w / aspect_ratio)

        # 3. GENERACIÓN DEL CLIP FINAL
        if self.use_facetracking and path:
            def get_face_frame(get_frame, t):
                frame = get_frame(t)
                face_x, _, _, _ = path.get_at(t)
                x1 = max(0, min(int(face_x - (crop_w // 2)), clip.w - crop_w))
                y1 = (clip.h // 2) - (crop_h // 2) # Centrado vertical estático
                return frame[y1:y1+crop_h, x1:x1+crop_w]
            
            final_clip = clip.transform(get_face_frame)
            logger.info(f"✨ Smart Fill dinámico aplicado (Zoom: {zoom_factor}x)")
        else:
            # Recorte central estático
            final_clip = clip.cropped(x_center=clip.w//2, y_center=clip.h//2, width=crop_w, height=crop_h)
            logger.info("✨ Fill central estático aplicado")

        final_clip = final_clip.resized(width=target_w, height=target_h)
        return final_clip

class FitLayout(BaseLayout):
    """
    Layout 'Fit' (Letterbox):
    - Mantiene el video original completo con barras negras.
    """
    def apply(self, clip):
        # 1. Redimensionar (resized) para encajar en el ancho
        main = clip.resized(width=self.target_w)
        
        # Si sigue siendo muy alto, ajustamos por el alto
        if main.h > self.target_h:
            main = clip.resized(height=self.target_h)
            
        # 2. Centrar
        main = main.with_position("center")
        
        # 3. Fondo Negro y Audio
        bg = ColorClip(
            size=(self.target_w, self.target_h), 
            color=(0,0,0), 
            duration=clip.duration
        )
        
        final_composition = CompositeVideoClip([bg, main], size=(self.target_w, self.target_h))
        
        if clip.audio:
            final_composition = final_composition.with_audio(clip.audio)
            
        return final_composition
