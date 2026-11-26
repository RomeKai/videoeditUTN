import logging

from .camera import Box, ManualCamera, place
from .pro import VERSUS_CENTER_Y, VersusLayout
from ..face_tracker import FaceTracker
from ..face_tracking import FaceTracks

logger = logging.getLogger(__name__)


def half_areas(frame_w, frame_h):
    """The left and right halves of the source, as ``versus_boxes`` splits them."""
    half = frame_w // 2
    return Box(0, 0, half, frame_h), Box(half, 0, half, frame_h)


def faces_by_half(faces, frame_w):
    """The tracks of each half (left, right), by where their mean centre falls."""
    half = frame_w / 2
    left, right = [], []
    for track in faces.tracks:
        points = track.points()
        if not points:
            continue
        mean_x = sum(p.x for p in points) / len(points)
        (left if mean_x < half else right).append(track)
    return left, right


def follow_box(face_x, face_y, w, h, area):
    """A ``w x h`` crop in ``area`` with the face at VERSUS_CENTER_Y of its height."""
    return place(face_x, face_y + h * (0.5 - VERSUS_CENTER_Y), w, h, area)


class PodcastLayout(VersusLayout):
    """
    Layout 'Podcast' (CORE-03, Riverside style): two people, one per half of
    the output, each followed on its own.

    The panes are the ones of ``versus`` (left half of the source on top,
    right half below, same crop size). With face tracking, the faces of the
    clip are tracked once (CORE-01) and split by the half they are in; each
    pane follows the faces of its half, cutting between them like
    ``framing_path``. A half without faces, a ``static`` preview, tracking
    off or a manual camera keep the fixed ``versus`` crop.
    """
    def apply(self, clip):
        self._paths = (None, None)
        if self.use_facetracking and ManualCamera.from_coords(self.manual_camera_coords) is None:
            self._paths = self._half_paths(clip)
        return super().apply(clip)

    def _half_paths(self, clip):
        try:
            faces = FaceTracker().track_faces(clip, fps=self.tracking_fps)
        except Exception as exc:
            logger.error("layout.podcast_tracking_failed error=%s", type(exc).__name__)
            return None, None
        paths = []
        for tracks in faces_by_half(faces, clip.w):
            path = FaceTracks(tracks, faces.sample_times, faces.frame_size).framing_path() if tracks else None
            paths.append(path if path is not None and path.points else None)
        logger.info(
            "layout.podcast faces=%d followed_panes=%d",
            len(faces.tracks), sum(path is not None for path in paths),
        )
        return tuple(paths)

    def _crop_pane(self, clip, box, index):
        path = self._paths[index] if self._paths else None
        if path is None:
            return super()._crop_pane(clip, box, index)
        area = half_areas(clip.w, clip.h)[index]

        def follow(get_frame, t):
            frame = get_frame(t)
            face_x, face_y, _, _ = path.get_at(t)
            crop = follow_box(face_x, face_y, box.w, box.h, area)
            return frame[crop.y1:crop.y1 + crop.h, crop.x1:crop.x1 + crop.w]

        return clip.transform(follow)
