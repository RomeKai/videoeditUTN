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

    def detect_face_center(self, clip, num_samples=10):
        try:
            dur = clip.duration
            times_to_check = np.linspace(0.5, dur - 0.5, num=num_samples)
            face_data = [] # Cambiamos el nombre para que sea más claro
            
            for t in times_to_check:
                try:
                    frame = clip.get_frame(t)
                    height, width, _ = frame.shape
                    results = self.face_detection.process(frame)

                    if results.detections:
                        best_detection = max(results.detections, key=lambda d: d.score[0])
                        bbox = best_detection.location_data.relative_bounding_box
                        
                        # 🔥 FILTRO PRO: Ignorar caras minúsculas o con baja confianza
                        # Si mide menos de 40px en un video HD, probablemente no es el streamer.
                        face_w = int(bbox.width * width)
                        face_h = int(bbox.height * height)
                        
                        if face_w < 40 or face_h < 40 or best_detection.score[0] < 0.6:
                            continue
                            
                        center_x = int((bbox.xmin + bbox.width / 2) * width)
                        center_y = int((bbox.ymin + bbox.height / 2) * height)
                        
                        face_data.append((center_x, center_y, face_w, face_h))
                except:
                    continue

            if not face_data:
                logger.warning("⚠️ Sin rostros válidos detectados.")
                return None

            # 🔥 RECHAZO DE OUTLIERS: Si tenemos varias muestras, eliminamos las más lejanas
            # para evitar que un frame con un cartel o un efecto del juego ensucie la mediana.
            if len(face_data) >= 3:
                # Ordenamos por X y quitamos el máximo y el mínimo si hay suficientes muestras
                face_data.sort(key=lambda d: d[0])
                face_data = face_data[1:-1]

            # Mediana matemática para las 4 dimensiones restantes
            median_x = int(np.median([d[0] for d in face_data]))
            median_y = int(np.median([d[1] for d in face_data]))
            median_w = int(np.median([d[2] for d in face_data]))
            median_h = int(np.median([d[3] for d in face_data]))
            
            logger.info(f"📍 Cara anclada en X={median_x}, Y={median_y} | Tamaño: {median_w}x{median_h}px")
            return (median_x, median_y, median_w, median_h)

        except Exception as e:
            logger.error(f"Error en FaceTracker: {e}")
            return None

    def close(self):
        try:
            self.face_detection.close()
        except: pass
