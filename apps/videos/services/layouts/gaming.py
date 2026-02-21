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
        
        # 2. DETECCIÓN DE ROSTRO
        tracker = FaceTracker()
        face_data = tracker.detect_face_center(clip) 
        tracker.close()

        # MATEMÁTICA CÁMARA (Proporción Reels)
        cam_target_aspect = target_w / cam_h

        if face_data:
            face_x, face_y, face_w, face_h = face_data
            logger.info(f"🎯 IA detectó cara en ({face_x}, {face_y}) con tamaño {face_w}x{face_h}")
            
            # 🔥 ZOOM ADAPTATIVO EXTREMO 🔥
            # Si la cara ocupa menos del 15% del alto del video (cara pequeña en webcam),
            # necesitamos un zoom casi total para ignorar el chat/marcos de la webcam.
            face_relative_height = face_h / clip.h
            
            if face_relative_height < 0.15:
                multiplier = 1.6 # Punto dulce: Cierra el encuadre sin ser sofocante
                y_offset = 0.40   # Ajuste vertical balanceado
                logger.info("🔍 Aplicando Smart Zoom (1.6x) para webcam pequeña")
            else:
                multiplier = 2.0  # Zoom estándar para planos medios
                y_offset = 0.35
            
            src_crop_h = int(face_h * multiplier)
            src_crop_w = int(src_crop_h * cam_target_aspect)
            
            # Verificación de seguridad
            if src_crop_w > clip.w:
                src_crop_w = clip.w
                src_crop_h = int(src_crop_w / cam_target_aspect)

            x1 = face_x - (src_crop_w // 2)
            y1 = face_y - int(src_crop_h * y_offset) 
            
        else:
            logger.warning("⚠️ FALLBACK: No se detectó cara, usando recorte genérico superior.")
            # Fallback (Plan B si no hay cara)
            src_crop_h = int(clip.h * 0.40) # Bajamos de 0.50 a 0.40 para que sea más cerrado
            src_crop_w = int(src_crop_h * cam_target_aspect)
            if src_crop_w > clip.w:
                src_crop_w = clip.w
                src_crop_h = int(src_crop_w / cam_target_aspect)
                
            x1 = (clip.w // 2) - (src_crop_w // 2)
            y1 = 0 

        # Validar límites estrictos
        x1 = max(0, min(x1, clip.w - src_crop_w))
        y1 = max(0, min(y1, clip.h - src_crop_h))
        logger.info(f"📐 Recorte final cámara: x={x1}, y={y1}, w={src_crop_w}, h={src_crop_h}")

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
