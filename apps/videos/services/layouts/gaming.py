import os
from django.conf import settings 

from moviepy.video.compositing.CompositeVideoClip import CompositeVideoClip
from moviepy.video.VideoClip import ColorClip, TextClip 
from ..face_tracker import FaceTracker
from .interface import BaseLayout 

class SplitLayout(BaseLayout):
    """
    Layout Híbrido: Permite elegir si la cámara va Arriba o Abajo.
    """
    def apply(self, clip, camera_pos='top'): # <--- NUEVO PARÁMETRO (Default: bottom)
    #def apply(self, clip, camera_pos='bottom'): para probar cámara arriba

        # 1. Configurar Dimensiones
        cam_h = int(self.target_h * 0.40) 
        game_h = self.target_h - cam_h
        
        # 2. DETECCIÓN DE ROSTRO (X + Y)
        tracker = FaceTracker()
        coords = tracker.detect_face_center(clip) 
        tracker.close()

        # --- CÁLCULO DE RECORTE (Zoom Natural) ---
        target_aspect_ratio = self.target_w / cam_h
        src_crop_h = int(clip.h * 0.8)
        src_crop_w = int(src_crop_h * target_aspect_ratio)
        
        if src_crop_w > clip.w:
            src_crop_w = clip.w
            src_crop_h = int(src_crop_w / target_aspect_ratio)

        # Centrado Inteligente
        if coords:
            face_x, face_y = coords
            x1 = face_x - (src_crop_w // 2)
            y1 = face_y - (src_crop_h // 2)
        else:
            x1 = (clip.w / 2) - (src_crop_w // 2)
            y1 = (clip.h / 2) - (src_crop_h // 2)

        # Validar límites
        x1 = max(0, min(x1, clip.w - src_crop_w))
        y1 = max(0, min(y1, clip.h - src_crop_h))

        # --- GENERACIÓN DE CLIPS ---
        
        # A. Clip de Cámara
        cam = clip.cropped(x1=x1, y1=y1, width=src_crop_w, height=src_crop_h)
        cam = cam.resized(height=cam_h)
        
        # B. Clip de Contenido (Juego/Video Original)
        # Usamos todo el ancho posible
        game = clip.cropped(x_center=clip.w/2, y_center=clip.h/2, width=clip.h * (self.target_w/game_h), height=clip.h)
        game = game.resized(height=game_h)

        # --- LÓGICA DE POSICIONAMIENTO DINÁMICO ---
        if camera_pos == 'top':
            # Cámara ARRIBA, Juego ABAJO
            cam = cam.with_position(('center', 'top'))
            game = game.with_position(('center', 'bottom'))
            text_y = 'center' # Texto en el medio exacto
        else:
            # Cámara ABAJO, Juego ARRIBA (Estilo Streamer)
            cam = cam.with_position(('center', 'bottom'))
            game = game.with_position(('center', 'top'))
            text_y = 'center' 

        # C. TEXTO
        try:
            font_path = os.path.join(settings.BASE_DIR, 'assets', 'fonts', 'Montserrat-Bold.ttf')
            if not os.path.exists(font_path):
                font_path = 'DejaVu-Sans-Bold'

            txt_clip = TextClip(
                text="IA GENERATED", 
                font_size=40, 
                color='white', 
                font=font_path,
                stroke_color='black',
                stroke_width=2
            )
            txt_clip = txt_clip.with_position(text_y).with_duration(clip.duration)
            
            # El orden en la lista determina el eje Z (quién tapa a quién)
            # Ponemos el texto al final para que siempre esté encima de todo
            layers = [game, cam, txt_clip]
        except Exception as e:
            print(f"⚠️ Error texto: {e}")
            layers = [game, cam]

        bg = ColorClip(size=(self.target_w, self.target_h), color=(0,0,0), duration=clip.duration)
        return CompositeVideoClip([bg] + layers, size=(self.target_w, self.target_h))