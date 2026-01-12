import mediapipe as mp
import numpy as np
import logging

# Importamos VideoFileClip dentro de los métodos o usamos inyección para evitar líos
from moviepy import VideoFileClip

logger = logging.getLogger(__name__)

class FaceTracker:
    """
    Servicio encargado de detectar rostros en un video usando MediaPipe.
    Devuelve la coordenada X central para realizar recortes inteligentes.
    """

    def __init__(self):
        self.mp_face_detection = mp.solutions.face_detection
        self.detector = self.mp_face_detection.FaceDetection(
            model_selection=1, # 0 para corta distancia (celular), 1 para larga (webcam/stream)
            min_detection_confidence=0.6
        )

    def detect_face_center(self, clip, sample_time=2.0):
        """
        Analiza un frame del clip y devuelve el centro X de la cara más grande.
        Si no encuentra nada, devuelve None.
        """
        try:
            # 1. Extraer un frame como imagen numpy (H, W, 3)
            # Si el clip es muy corto, usamos la mitad de su duración
            t = min(sample_time, clip.duration / 2)
            frame = clip.get_frame(t)

            # 2. Procesar con MediaPipe
            results = self.detector.process(frame)

            if not results.detections:
                logger.warning("👀 No se detectaron caras. Usando centro por defecto.")
                return None

            # 3. Buscar la cara más relevante (mayor score)
            best_detection = max(results.detections, key=lambda d: d.score[0])
            
            # 4. Calcular coordenadas
            # MediaPipe devuelve coordenadas relativas (0.0 a 1.0)
            bboxC = best_detection.location_data.relative_bounding_box
            h, w, _ = frame.shape
            
            center_x_relative = bboxC.xmin + (bboxC.width / 2)
            center_x_pixel = int(center_x_relative * w)
            
            logger.info(f"👀 Cara detectada en X={center_x_pixel} (Relativo: {center_x_relative:.2f})")
            return center_x_pixel

        except Exception as e:
            logger.error(f"⚠️ Error en FaceTracker: {e}")
            return None
        
    def close(self):
        self.detector.close()