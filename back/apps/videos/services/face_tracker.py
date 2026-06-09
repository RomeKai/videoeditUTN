import numpy as np
import logging
import os
import cv2

# Disable GPU for MediaPipe to avoid EGL/OpenGL errors in headless environments
os.environ['MEDIAPIPE_DISABLE_GPU'] = '1'

logger = logging.getLogger(__name__)

try:
    import mediapipe as mp
    from mediapipe.tasks import python
    from mediapipe.tasks.python import vision
    MEDIAPIPE_TASKS_AVAILABLE = True
except (ImportError, ModuleNotFoundError):
    MEDIAPIPE_TASKS_AVAILABLE = False
    logger.warning("âš ï¸ MediaPipe Tasks not found. Face tracking will be disabled.")

from apps.videos.services.tracking_utils import TrackingPoint, TrackingPath, MovingAverageSmoothing

class FaceTracker:
    """
    Automated Face Tracking service using MediaPipe Tasks API.
    Generates a smoothed trajectory path for intelligent cropping.
    """
    def __init__(self, min_detection_confidence: float = 0.5):
        self.detector = None
        if MEDIAPIPE_TASKS_AVAILABLE:
            try:
                # Path to the downloaded model
                model_path = os.path.join(os.path.dirname(__file__), 'face_detector.tflite')
                if not os.path.exists(model_path):
                    logger.warning(f"Face detector model not found at {model_path}")
                    return

                base_options = python.BaseOptions(model_asset_path=model_path)
                options = vision.FaceDetectorOptions(base_options=base_options, min_detection_confidence=min_detection_confidence)
                self.detector = vision.FaceDetector.create_from_options(options)
                logger.info("âœ… FaceTracker: MediaPipe Tasks Detector initialized.")
            except Exception as e:
                logger.error(f"Failed to initialize MediaPipe FaceDetector: {e}")

    def generate_path(self, clip, fps: float = 3.0) -> TrackingPath:
        """
        Analyzes video clip at specific intervals to map face movement.
        """
        width, height = clip.size
        if not self.detector:
            logger.warning("FaceTracker: Detector not available, returning center path.")
            return TrackingPath([TrackingPoint(0, width // 2, height // 2, width // 3, height // 3)])
        
        try:
            duration = clip.duration
            times_to_check = np.arange(0, duration, 1.0 / fps)
            points = []
            last_valid_point = None
            
            detections_count = 0

            for t in times_to_check:
                try:
                    frame = clip.get_frame(t)
                    # MediaPipe Tasks requires Image object
                    mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=frame)
                    
                    detection_result = self.detector.detect(mp_image)
                    
                    if detection_result.detections:
                        detections_count += 1
                        # Get the best detection
                        best_detection = max(detection_result.detections, key=lambda d: d.categories[0].score)
                        bbox = best_detection.bounding_box
                        
                        last_valid_point = TrackingPoint(
                            t,
                            int(bbox.origin_x + bbox.width / 2),
                            int(bbox.origin_y + bbox.height / 2),
                            int(bbox.width),
                            int(bbox.height)
                        )
                    
                    if last_valid_point:
                        points.append(TrackingPoint(t, last_valid_point.x, last_valid_point.y, last_valid_point.w, last_valid_point.h))
                except Exception as e:
                    logger.warning(f"Error processing frame at t={t}: {e}")
                    continue

            if not points:
                logger.warning(f"âš ï¸ FaceTracker: No faces detected in {len(times_to_check)} frames.")
                points = [TrackingPoint(0, width // 2, height // 2, width // 3, height // 3)]
            else:
                logger.info(f"âœ… FaceTracker: {detections_count} successful detections.")
            
            return TrackingPath(points, smoothing_strategy=MovingAverageSmoothing(window_size=5))
        except Exception as e:
            logger.error(f"Error in FaceTracker generation: {e}")
            return TrackingPath([TrackingPoint(0, width // 2, height // 2, width // 3, height // 3)])

    def close(self):
        """Releases MediaPipe resources."""
        if self.detector:
            try:
                self.detector.close()
            except: 
                pass
