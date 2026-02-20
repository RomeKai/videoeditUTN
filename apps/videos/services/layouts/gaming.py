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
        
        # 1. DETECCIÓN DE ROSTRO PARA CÁMARA
        tracker = FaceTracker()
        coords = tracker.detect_face_center(clip) 
        tracker.close()

        # MATEMÁTICA CÁMARA
        cam_target_aspect = target_w / cam_h
        src_crop_h = int(clip.h * 0.75)
        src_crop_w = int(src_crop_h * cam_target_aspect)
        
        if src_crop_w > clip.w:
            src_crop_w = clip.w
            src_crop_h = int(src_crop_w / cam_target_aspect)

        if coords:
            face_x, face_y = coords
            # Regla de los tercios: El rostro suele estar en el tercio superior de la webcam
            x1 = face_x - (src_crop_w // 2)
            y1 = face_y - int(src_crop_h * 0.33) 
        else:
            x1 = (clip.w // 2) - (src_crop_w // 2)
            y1 = 0 # Fallback al techo de la pantalla original

        # Clamp para no salir de los bordes del video original
        x1 = max(0, min(x1, clip.w - src_crop_w))
        y1 = max(0, min(y1, clip.h - src_crop_h))

        cam = clip.cropped(x1=x1, y1=y1, width=src_crop_w, height=src_crop_h)
        cam = cam.resized(width=target_w, height=cam_h)
        cam = cam.with_position(('center', 'top'))

        # 2. MATEMÁTICA GAMEPLAY (CROP CENTRAL FIJO)
        game_target_aspect = target_w / game_h
        orig_aspect = clip.w / clip.h

        if orig_aspect > game_target_aspect:
            game_crop_h = clip.h
            game_crop_w = int(clip.h * game_target_aspect)
        else:
            game_crop_w = clip.w
            game_crop_h = int(clip.w / game_target_aspect)
        
        game = clip.cropped(x_center=clip.w//2, y_center=clip.h//2, width=game_crop_w, height=game_crop_h)
        game = game.resized(width=target_w, height=game_h)
        game = game.with_position(('center', 'bottom'))

        # 3. COMPOSICIÓN Y AUDIO
        bg = ColorClip(size=(target_w, target_h), color=(0,0,0), duration=clip.duration)
        final_composition = CompositeVideoClip([bg, game, cam], size=(target_w, target_h))
        
        if clip.audio:
            final_composition = final_composition.with_audio(clip.audio)
            
        return final_composition
