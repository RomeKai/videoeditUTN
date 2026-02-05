from moviepy import CompositeVideoClip, ColorClip
from .interface import BaseLayout
from ..face_tracker import FaceTracker # Importamos el tracker que acabamos de crear

class GamingLayout(BaseLayout):
    """
    Layout 'Split Screen' Avanzado con Face Tracking.
    - Arriba: Facecam (Detectada y centrada por IA).
    - Abajo: Gameplay.
    """
    
    def apply(self, clip, camera_pos='top'):
        # 1. Configurar Dimensiones
        target_w = self.target_w
        target_h = self.target_h
        
        # Altura de la cámara (40% de la pantalla vertical)
        cam_h = int(target_h * 0.40) 
        game_h = target_h - cam_h
        
        # 2. DETECCIÓN DE ROSTRO (X + Y)
        tracker = FaceTracker()
        coords = tracker.detect_face_center(clip) 
        tracker.close()

        # --- CÁLCULO DE RECORTE (Smart Crop) ---
        # Queremos mantener el aspect ratio del hueco donde irá la cámara
        target_aspect_ratio = target_w / cam_h
        
        # Definimos el tamaño del recorte original (Zoom level)
        # Tomamos el 80% de la altura original para hacer un poco de zoom natural
        src_crop_h = int(clip.h * 0.8)
        src_crop_w = int(src_crop_h * target_aspect_ratio)
        
        # Si el ancho calculado es mayor al video, ajustamos
        if src_crop_w > clip.w:
            src_crop_w = clip.w
            src_crop_h = int(src_crop_w / target_aspect_ratio)

        # 3. Calcular coordenadas de recorte (x1, y1)
        if coords:
            face_x, face_y = coords
            x1 = face_x - (src_crop_w // 2)
            y1 = face_y - (src_crop_h // 2)
        else:
            # Fallback: Centro geométrico
            x1 = (clip.w / 2) - (src_crop_w // 2)
            y1 = (clip.h / 2) - (src_crop_h // 2)

        # Validar límites (que no se salga del video)
        x1 = max(0, min(x1, clip.w - src_crop_w))
        y1 = max(0, min(y1, clip.h - src_crop_h))

        # --- GENERACIÓN DE CLIPS ---
        
        # A. Clip de Cámara (Facecam)
        cam = clip.cropped(x1=x1, y1=y1, width=src_crop_w, height=src_crop_h)
        cam = cam.resized(height=cam_h) # Redimensionar al tamaño final
        cam = cam.with_position(('center', 'top')) # Por defecto arriba

        # B. Clip de Contenido (Gameplay)
        # Recortamos el centro del video para llenar la parte de abajo
        game = clip.cropped(
            x_center=clip.w/2, 
            y_center=clip.h/2, 
            width=clip.h * (target_w/game_h), # Ancho proporcional
            height=clip.h
        )
        game = game.resized(height=game_h)
        game = game.with_position(('center', 'bottom')) # Por defecto abajo

        # --- COMPOSICIÓN FINAL ---
        # Fondo negro para evitar glitches visuales
        bg = ColorClip(size=(target_w, target_h), color=(0,0,0), duration=clip.duration)
        
        # El orden importa: bg -> game -> cam (cámara siempre visible)
        layers = [bg, game, cam]
        
        return CompositeVideoClip(layers, size=(target_w, target_h))