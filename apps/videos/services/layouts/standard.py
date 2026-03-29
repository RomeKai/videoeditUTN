from moviepy import ColorClip, CompositeVideoClip
from .interface import BaseLayout
from ..face_tracker import FaceTracker
import logging

logger = logging.getLogger(__name__)

class FillLayout(BaseLayout):
    """
    Layout 'Smart Fill' (Horizontal to Vertical):
    - Detecta el rostro para centrar el recorte horizontal.
    - Aplica un zoom dinámico para optimizar el encuadre 9:16.
    - Similar al motor de reencuadre de OpusClip/Submagic.
    """
    def apply(self, clip):
        target_w, target_h = self.target_w, self.target_h
        
        # 1. INTELIGENCIA DE ENCUADRE
        tracker = FaceTracker()
        face_data = tracker.detect_face_center(clip)
        tracker.close()

        # 2. DETERMINAR EL FOCO (X-AXIS)
        if face_data:
            face_x, face_y, face_w, face_h = face_data
            focus_x = face_x
            
            # 🔥 ZOOM ADAPTATIVO 🔥
            # Si la cara es muy pequeña (< 15% del alto), aplicamos un zoom extra 
            # para que el video vertical no se vea con el sujeto muy lejos.
            face_ratio = face_h / clip.h
            if face_ratio < 0.15:
                zoom_factor = 1.3  # 30% más de zoom
            else:
                zoom_factor = 1.0
        else:
            focus_x = clip.w // 2
            zoom_factor = 1.0

        # 3. MATEMÁTICA DE RECORTE (9:16)
        # Calculamos cuánto del video original cabe en el target vertical
        # manteniendo la proporción 1080x1920 (9:16)
        aspect_ratio = target_w / target_h
        
        # El ancho del recorte será el alto del video * aspect_ratio (dividido por el zoom)
        crop_w = int((clip.h * aspect_ratio) / zoom_factor)
        crop_h = int(clip.h / zoom_factor)

        # Aseguramos que el recorte no exceda los límites del video original
        if crop_w > clip.w:
            crop_w = clip.w
            crop_h = int(crop_w / aspect_ratio)

        # 4. CLAMP PARA NO SALIR DE BORDES
        # El centro del recorte debe ser focus_x, pero sin salirnos del clip original
        x1 = focus_x - (crop_w // 2)
        x1 = max(0, min(x1, clip.w - crop_w))
        
        y1 = (clip.h // 2) - (crop_h // 2) # Centrado vertical por defecto

        # 5. GENERACIÓN DEL CLIP FINAL
        final_clip = clip.cropped(x1=x1, y1=y1, width=crop_w, height=crop_h)
        final_clip = final_clip.resized(width=target_w, height=target_h)
        
        logger.info(f"✨ Smart Fill aplicado (Focus X: {focus_x}, Zoom: {zoom_factor}x)")
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