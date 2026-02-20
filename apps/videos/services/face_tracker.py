import mediapipe as mp
import numpy as np
import logging

logger = logging.getLogger(__name__)

class FaceTracker:
    def __init__(self):
        self.mp_face_detection = mp.solutions.face_detection
        self.face_detection = self.mp_face_detection.FaceDetection(
            model_selection=1, 
            min_detection_confidence=0.5
        )

    def detect_face_center(self, clip, num_samples=5):
        try:
            dur = clip.duration
            # Muestreamos en 5 puntos diferentes del clip
            times_to_check = np.linspace(0.5, dur - 0.5, num=num_samples)
            face_coords = []
            
            for t in times_to_check:
                try:
                    frame = clip.get_frame(t)
                    height, width, _ = frame.shape
                    results = self.face_detection.process(frame)

                    if results.detections:
                        best_detection = max(results.detections, key=lambda d: d.score[0])
                        bbox = best_detection.location_data.relative_bounding_box
                        
                        # FILTRO ANTI-BASURA: Ignorar cajas < 5% de la pantalla (Logos, NPCs)
                        if bbox.width < 0.05 or bbox.height < 0.05:
                            continue
                            
                        center_x = int((bbox.xmin + bbox.width / 2) * width)
                        center_y = int((bbox.ymin + bbox.height / 2) * height)
                        face_coords.append((center_x, center_y))
                except:
                    continue

            if not face_coords:
                logger.warning("⚠️ Sin rostros válidos detectados.")
                return None

            # MEDIANA MATEMÁTICA: Mitiga el jitter y descarta falsos positivos anómalos
            median_x = int(np.median([c[0] for c in face_coords]))
            median_y = int(np.median([c[1] for c in face_coords]))
            
            logger.info(f"📍 Cara del streamer anclada en X={median_x}, Y={median_y}")
            return (median_x, median_y)

        except Exception as e:
            logger.error(f"Error en FaceTracker: {e}")
            return None

    def close(self):
        try:
            self.face_detection.close()
        except: pass
