import mediapipe as mp
import numpy as np
import logging
from moviepy.video.io.VideoFileClip import VideoFileClip

logger = logging.getLogger(__name__)

class FaceTracker:
    def __init__(self):
        self.mp_face_detection = mp.solutions.face_detection
        self.face_detection = self.mp_face_detection.FaceDetection(
            model_selection=1, 
            min_detection_confidence=0.6
        )

    def detect_face_center(self, clip):
        """
        Analiza el primer segundo del video para encontrar la cara.
        Retorna una tupla (center_x, center_y) o None.
        """
        try:
            # Tomamos un frame al segundo 1.0 (o al principio si es muy corto)
            t_check = min(1.0, clip.duration / 2)
            frame = clip.get_frame(t_check)
            
            height, width, _ = frame.shape
            results = self.face_detection.process(frame)

            if not results.detections:
                logger.warning("⚠️ No se detectaron caras.")
                return None

            # Tomamos la cara con mayor confianza (score)
            best_detection = max(results.detections, key=lambda d: d.score[0])
            bbox = best_detection.location_data.relative_bounding_box

            # Calculamos el centro absoluto en píxeles
            center_x = int((bbox.xmin + bbox.width / 2) * width)
            center_y = int((bbox.ymin + bbox.height / 2) * height)
            
            logger.info(f"📍 Cara encontrada en: X={center_x}, Y={center_y}")
            return (center_x, center_y)

        except Exception as e:
            logger.error(f"Error en FaceTracker: {e}")
            return None

    def close(self):
        self.face_detection.close()