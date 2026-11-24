import logging
import os
import threading

import numpy as np

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
    logger.warning("face_tracker.mediapipe_unavailable: face tracking is disabled")

from apps.videos.services.face_tracking import (  # noqa: E402
    DEFAULT_SAMPLING_FPS,
    Detection,
    FaceTracks,
    IoUTracker,
)
from apps.videos.services.tracking_utils import TrackingPath, TrackingPoint  # noqa: E402

MODEL_PATH = os.path.join(os.path.dirname(__file__), 'face_detector.tflite')

# One detector per worker process (CORE-01): loading the model for every clip
# was most of the tracking time. MediaPipe detectors are not thread-safe, so
# calls to each one are serialised.
_detectors = {}
_detectors_lock = threading.Lock()


class _SharedDetector:
    """
    Detector protocol used by FaceTracker: ``detect(rgb_frame)`` takes a numpy
    RGB frame and returns MediaPipe's result (``.detections``). Tests pass any
    object with that method.
    """

    def __init__(self, detector):
        self._detector = detector
        self._lock = threading.Lock()

    def detect(self, frame):
        # MediaPipe images need libEGL and libGLESv2 even on CPU (Dockerfile).
        image = mp.Image(
            image_format=mp.ImageFormat.SRGB,
            data=np.ascontiguousarray(frame, dtype=np.uint8),
        )
        with self._lock:
            return self._detector.detect(image)

    def close(self):
        """Shared for the life of the worker: closing one tracker must not close it."""


def get_face_detector(min_detection_confidence: float = 0.5):
    """
    The process-wide MediaPipe face detector, or None when it is unavailable.
    A failed load is remembered too: it is logged once per process, not retried
    for every clip.
    """
    if not MEDIAPIPE_TASKS_AVAILABLE or not os.path.exists(MODEL_PATH):
        return None
    key = round(float(min_detection_confidence), 3)
    with _detectors_lock:
        if key not in _detectors:
            try:
                options = vision.FaceDetectorOptions(
                    base_options=python.BaseOptions(model_asset_path=MODEL_PATH),
                    min_detection_confidence=key,
                )
                _detectors[key] = _SharedDetector(vision.FaceDetector.create_from_options(options))
                logger.info("face_tracker.detector_loaded")
            except Exception as exc:
                logger.error("face_tracker.detector_failed error=%s", type(exc).__name__)
                _detectors[key] = None
        return _detectors[key]


class FaceTracker:
    """
    Face tracking for intelligent cropping (CORE-01, Face Tracking v2).

    ``track_faces`` returns every face with a stable id over the clip;
    ``generate_path`` keeps the previous contract (one smoothed path): it stays
    on the main face (most present and largest) while it is on screen and cuts
    to the face that is there otherwise, instead of jumping to whichever face
    scored highest in each frame.
    """

    def __init__(self, min_detection_confidence: float = 0.5, detector=None):
        self.detector = detector if detector is not None else get_face_detector(min_detection_confidence)
        if self.detector is None:
            logger.warning("face_tracker.detector_unavailable")

    # -- detection ---------------------------------------------------------

    def detect(self, frame, t: float):
        """Every face in one RGB frame, as Detections in pixels."""
        result = self.detector.detect(frame)
        detections = []
        for detection in result.detections or []:
            box = detection.bounding_box
            score = detection.categories[0].score if detection.categories else 1.0
            if box.width > 0 and box.height > 0:
                detections.append(Detection(
                    t=t, x=float(box.origin_x), y=float(box.origin_y),
                    w=float(box.width), h=float(box.height), score=float(score),
                ))
        return detections

    def track_faces(self, clip, fps: float = DEFAULT_SAMPLING_FPS, tracker: IoUTracker = None) -> FaceTracks:
        """Samples the clip at ``fps`` and links the faces into tracks."""
        tracker = tracker or IoUTracker(max_missed=max(2, int(round(fps * 1.5))))
        size = tuple(clip.size)
        if not self.detector:
            return FaceTracks([], [], size)
        failures = 0
        for t in np.arange(0, clip.duration, 1.0 / fps):
            t = float(t)
            try:
                detections = self.detect(clip.get_frame(t), t)
            except Exception as exc:
                failures += 1
                if failures <= 3:  # the rest are counted in the summary below
                    logger.warning("face_tracker.frame_failed t=%.2f error=%s", t, type(exc).__name__)
                detections = []
            tracker.update(t, detections)
        tracks = tracker.tracks()
        if failures and failures == len(tracker.sample_times):
            # Every frame failed: tracking is broken here (e.g. missing system
            # libraries), not just a clip without faces.
            logger.error("face_tracker.all_frames_failed samples=%d", failures)
        logger.info(
            "face_tracker.tracked samples=%d faces=%d failed_frames=%d",
            len(tracker.sample_times), len(tracks), failures,
        )
        return FaceTracks(tracks, tracker.sample_times, size)

    # -- single path (layouts) ---------------------------------------------

    @staticmethod
    def _center_path(width, height) -> TrackingPath:
        return TrackingPath([TrackingPoint(0, width // 2, height // 2, width // 3, height // 3)])

    def generate_path(self, clip, fps: float = DEFAULT_SAMPLING_FPS) -> TrackingPath:
        """Smoothed path of the main face; the frame center when there is none."""
        width, height = clip.size
        if not self.detector:
            return self._center_path(width, height)
        try:
            faces = self.track_faces(clip, fps)
            if faces.primary() is None:
                logger.warning("face_tracker.no_faces samples=%d", len(faces.sample_times))
                return self._center_path(width, height)
            return faces.framing_path()
        except Exception as exc:
            logger.error("face_tracker.failed error=%s", type(exc).__name__)
            return self._center_path(width, height)

    def close(self):
        """Kept for callers; the detector is shared by the worker process."""
        if self.detector and not isinstance(self.detector, _SharedDetector):
            try:
                self.detector.close()
            except Exception:
                pass
