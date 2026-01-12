from moviepy import CompositeVideoClip, ColorClip
from ..face_tracker import FaceTracker # <--- Importamos el nuevo servicio
from .interface import BaseLayout

class SplitLayout(BaseLayout):
    """
    Layout Gaming Inteligente:
    - Top 30%: Cámara (centrada en la cara del streamer).
    - Bottom 70%: Gameplay (centro del video).
    """
    def apply(self, clip):
        # 1. Configurar Dimensiones
        cam_h = int(self.target_h * 0.35) # Aumentamos un poco la cámara (35%)
        game_h = self.target_h - cam_h
        
        # 2. DETECCIÓN DE ROSTRO (Smart Crop)
        tracker = FaceTracker()
        face_x = tracker.detect_face_center(clip) # Devuelve pixel X o None
        tracker.close()

        # Lógica de recorte inteligente para la cámara (Top)
        # Queremos cortar un cuadrado/rectángulo de ancho = clip.w/3 (aprox)
        # O mejor, usar un ancho fijo basado en el aspecto 9:16
        
        # Definimos el ancho del recorte de la cámara (ej: un tercio del original)
        cam_crop_w = int(clip.w / 3) 
        cam_crop_h = int(clip.h / 3) # Asumimos que la camara ocupa el tercio superior

        if face_x:
            # Centrar en la cara
            x1 = face_x - (cam_crop_w // 2)
        else:
            # Fallback: Usar el centro geométrico si no hay cara o falla IA
            # Ojo: En streams, la camara suele estar a la derecha o izquierda.
            # Por defecto intentaremos buscar en el tercio superior izquierdo o derecho?
            # Por ahora, centro.
            x1 = (clip.w / 2) - (cam_crop_w // 2)

        # CLAMPING: Evitar salirnos del video (x1 < 0 o x2 > width)
        x1 = max(0, x1)
        if (x1 + cam_crop_w) > clip.w:
            x1 = clip.w - cam_crop_w

        # A. Crear Clip de Cámara
        # Usamos cropped con coordenadas calculadas
        cam = clip.cropped(
            x1=x1, 
            y1=0, # Asumimos que la cámara está arriba del todo
            width=cam_crop_w, 
            height=cam_crop_h
        )
        cam = cam.resized(height=cam_h)
        cam = cam.with_position(('center', 'top'))

        # B. Crear Clip de Gameplay (Bottom)
        # El gameplay suele ser el centro de la pantalla
        game = clip.cropped(
            x_center=clip.w/2, 
            y_center=clip.h/2, 
            width=self.target_w, 
            height=game_h
        )
        game = game.resized(height=game_h)
        game = game.with_position(('center', 'bottom'))

        # Fondo negro base
        bg = ColorClip(
            size=(self.target_w, self.target_h), 
            color=(0,0,0), 
            duration=clip.duration
        )
        
        return CompositeVideoClip(
            [bg, game, cam], 
            size=(self.target_w, self.target_h)
        )