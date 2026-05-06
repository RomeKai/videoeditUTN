import mediapipe as mp
import mediapipe.solutions.face_detection as mp_face_detection
import numpy as np
import logging
import os
from apps.videos.services.tracking_utils import TrackingPoint, TrackingPath, MovingAverageSmoothing

# Disable GPU for MediaPipe to avoid EGL/OpenGL errors in headless environments
os.environ['MEDIAPIPE_DISABLE_GPU'] = '1'

logger = logging.getLogger(__name__)

class FaceTracker:
    """
    Automated Face Tracking service using MediaPipe.
    Generates a smoothed trajectory path for intelligent cropping.
    """
    def __init__(self, min_detection_confidence: float = 0.5):
        # Using the explicit submodule import to fix Pylance resolution issues
        self.face_detection = mp_face_detection.FaceDetection(
            model_selection=1, 
            min_detection_confidence=min_detection_confidence
        )

    def generate_path(self, clip, fps: float = 2.0) -> TrackingPath:
        """
        Analyzes video clip at specific intervals to map face movement.
        """
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
                    # MediaPipe requires RGB input
                    results = self.face_detection.process(frame)
                    
                    if results.detections:
                        detections_count += 1
                        best_detection = max(results.detections, key=lambda d: d.score[0])
                        bbox = best_detection.location_data.relative_bounding_box
                        
                        # Store point if confidence is above 0.4
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
                    logger.warning(f"Error processing frame at t={t}: {e}")
                    continue

            if not points:
                logger.warning(f"⚠️ FaceTracker: No faces detected in {len(times_to_check)} frames.")
                points = [TrackingPoint(0, width // 2, height // 2, width // 3, height // 3)]
            else:
                logger.info(f"✅ FaceTracker: {detections_count} successful detections.")
            
            return TrackingPath(points, smoothing_strategy=MovingAverageSmoothing(window_size=5))
        except Exception as e:
            logger.error(f"Error in FaceTracker generation: {e}")
            width, height = clip.size
            return TrackingPath([TrackingPoint(0, width // 2, height // 2, width // 3, height // 3)])

    def close(self):
        """Releases MediaPipe resources."""
        try:
            self.face_detection.close()
        except: 
            pass
