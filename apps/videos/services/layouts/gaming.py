import os
# Silenciar errores de GPU en MediaPipe antes de importar
os.environ['MEDIAPIPE_DISABLE_GPU'] = '1'

from moviepy import CompositeVideoClip, ColorClip
from .interface import BaseLayout
from ..face_tracker import FaceTracker
import logging

logger = logging.getLogger(__name__)

class GamingLayout(BaseLayout):
    def apply(self, clip, camera_pos='top'):
        target_w, target_h = self.target_w, self.target_h
        
        # Proporción Reels: 35% Cámara, 65% Juego
        cam_h = int(target_h * 0.35) 
        game_h = target_h - cam_h
        
        # 1. TRAYECTORIA DE ROSTRO (OPCIONAL)
        path = None
        if self.use_facetracking:
            tracker = FaceTracker()
            path = tracker.generate_path(clip, fps=2.0)
            tracker.close()
            avg_w, avg_h = path.get_average_size()
        else:
            # Fallback a dimensiones centrales si no hay tracking
            avg_w, avg_h = clip.w // 3, clip.h // 3

        # MATEMÁTICA CÁMARA (Dimensiones Estáticas)
        cam_target_aspect = target_w / cam_h
        
        # Smart Zoom Logic (Estática para el tamaño)
        face_relative_height = avg_h / clip.h
        if face_relative_height < 0.15:
            multiplier = 1.6
            y_offset = 0.40
        else:
            multiplier = 2.0
            y_offset = 0.35
            
        src_crop_h = int(avg_h * multiplier) if avg_h > 0 else int(clip.h * 0.40)
        src_crop_w = int(src_crop_h * cam_target_aspect)
        
        # Ajuste de seguridad para el tamaño
        if src_crop_w > clip.w:
            src_crop_w = clip.w
            src_crop_h = int(src_crop_w / cam_target_aspect)
        if src_crop_h > clip.h:
            src_crop_h = clip.h
            src_crop_w = int(src_crop_h * cam_target_aspect)

        # 2. APLICAR RECORTE (DINÁMICO O ESTÁTICO)
        if self.use_facetracking and path:
            def get_face_frame(get_frame, t):
                frame = get_frame(t)
                face_x, face_y, _, _ = path.get_at(t)
                x1 = max(0, min(int(face_x - (src_crop_w // 2)), clip.w - src_crop_w))
                y1 = max(0, min(int(face_y - int(src_crop_h * y_offset)), clip.h - src_crop_h))
                return frame[y1:y1+src_crop_h, x1:x1+src_crop_w]
            cam = clip.transform(get_face_frame)
        else:
            # Recorte central fijo si no hay tracking
            cam = clip.cropped(x_center=clip.w//2, y_center=clip.h//2, width=src_crop_w, height=src_crop_h)

        cam = cam.resized(width=target_w, height=cam_h)
        cam = cam.with_position(('center', 'top'))

        # 3. MATEMÁTICA GAMEPLAY (CROP CENTRAL FIJO)
        game_target_aspect = target_w / game_h
        
        # Calculamos crop estático para gameplay
        if (clip.w / clip.h) > game_target_aspect:
            game_crop_h = clip.h
            game_crop_w = int(clip.h * game_target_aspect)
        else:
            game_crop_w = clip.w
            game_crop_h = int(clip.w / game_target_aspect)
        
        game = clip.cropped(x_center=clip.w//2, y_center=clip.h//2, width=game_crop_w, height=game_crop_h)
        game = game.resized(width=target_w, height=game_h)
        game = game.with_position(('center', 'bottom'))

        # 4. COMPOSICIÓN
        bg = ColorClip(size=(target_w, target_h), color=(0,0,0), duration=clip.duration)
        final_composition = CompositeVideoClip([bg, game, cam], size=(target_w, target_h))
        
        if clip.audio:
            final_composition = final_composition.with_audio(clip.audio)
            
        return final_composition
