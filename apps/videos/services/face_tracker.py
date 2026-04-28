import mediapipe as mp
import numpy as np
import logging
import os
from .tracking_utils import TrackingPoint, TrackingPath, MovingAverageSmoothing

# Desactivar GPU para MediaPipe en Docker para evitar errores de EGL/OpenGL
os.environ['MEDIAPIPE_DISABLE_GPU'] = '1'

logger = logging.getLogger(__name__)

class FaceTracker:
    def __init__(self, min_detection_confidence: float = 0.5):
        self.mp_face_detection = mp.solutions.face_detection
        # model_selection=1 para rostros a > 2 metros, model_selection=0 para cerca (selfies)
        self.face_detection = self.mp_face_detection.FaceDetection(
            model_selection=1, min_detection_confidence=min_detection_confidence
        )

    def generate_path(self, clip, fps: float = 2.0) -> TrackingPath:
        try:
            duration = clip.duration
            times_to_check = np.arange(0, duration, 1.0 / fps)
            points = []
            last_valid_point = None
            width, height = clip.size
            
            detections_count = 0

            for t in times_to_check:
                try:
                    frame = clip.get_frame(t)
                    # MediaPipe requiere RGB (MoviePy ya lo entrega así)
                    results = self.face_detection.process(frame)
                    
                    if results.detections:
                        detections_count += 1
                        best_detection = max(results.detections, key=lambda d: d.score[0])
                        bbox = best_detection.location_data.relative_bounding_box
                        
                        # Guardar punto si la confianza es decente
                        if best_detection.score[0] > 0.4:
                            last_valid_point = TrackingPoint(
                                t,
                                int((bbox.xmin + bbox.width / 2) * width),
                                int((bbox.ymin + bbox.height / 2) * height),
                                int(bbox.width * width),
                                int(bbox.height * height)
                            )
                    
                    if last_valid_point:
                        points.append(TrackingPoint(t, last_valid_point.x, last_valid_point.y, last_valid_point.w, last_valid_point.h))
                except Exception as e:
                    logger.warning(f"Error procesando frame en t={t}: {e}")
                    continue

            if not points:
                logger.warning(f"⚠️ FaceTracker: No se detectaron rostros en {len(times_to_check)} frames analizados.")
                points = [TrackingPoint(0, width // 2, height // 2, width // 3, height // 3)]
            else:
                logger.info(f"✅ FaceTracker: {detections_count} detecciones exitosas.")
            
            return TrackingPath(points, smoothing_strategy=MovingAverageSmoothing(window_size=5))
        except Exception as e:
            logger.error(f"Error generando trayectoria FaceTracker: {e}")
            width, height = clip.size
            return TrackingPath([TrackingPoint(0, width // 2, height // 2, width // 3, height // 3)])

    def close(self):
        try:
            self.face_detection.close()
        except: pass
