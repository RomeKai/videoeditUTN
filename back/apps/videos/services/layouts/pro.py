from moviepy import CompositeVideoClip, ColorClip, VideoClip
from .camera import Box, ManualCamera, fit_size, manual_box, place
from .interface import BaseLayout
from ..face_tracker import FaceTracker
import logging

logger = logging.getLogger(__name__)


# A wide pane (1:1, 16:9) would keep a thin strip of each half: never less
# than this share of its height. The rest of the pane is black.
VERSUS_MIN_HEIGHT = 0.6
# A crop with the pane's aspect this close under the floor is kept as is: the
# floor would only add black slivers (e.g. a 4:3 source at 9:16 keeps 59 %).
VERSUS_FLOOR_TOLERANCE = 0.03
# Faces sit in the upper part of the frame: the crop is centred this high.
VERSUS_CENTER_Y = 0.4


def versus_size(half_w, half_h, aspect):
    """
    Crop size in one half: the pane's aspect, but at least VERSUS_MIN_HEIGHT
    of the half's height (then the crop is narrower than the pane), unless
    the pane's aspect is within VERSUS_FLOOR_TOLERANCE of that floor.
    """
    w, h = fit_size(half_w, half_h, aspect)
    min_h = min(half_h, int(round(half_h * VERSUS_MIN_HEIGHT)))
    if h < min_h * (1 - VERSUS_FLOOR_TOLERANCE):
        w, h = half_w, min_h
    return w, h


def versus_boxes(frame_w, frame_h, aspect, manual=None):
    """
    The crops of the two panes: one per half of the source (left on top,
    right below), of ``versus_size``. By default centred across each half
    and at VERSUS_CENTER_Y of its height; a manual camera frames both
    halves the same way, with x and y measured inside each half and the
    zoom dividing that size.
    """
    half = frame_w // 2
    boxes = []
    for area in (Box(0, 0, half, frame_h), Box(half, 0, half, frame_h)):
        w, h = versus_size(area.w, area.h, aspect)
        if manual is not None:
            boxes.append(manual_box(manual, area, w, h))
        else:
            boxes.append(place(area.x1 + area.w // 2, area.y1 + area.h * VERSUS_CENTER_Y, w, h, area))
    return boxes


def pane_size(box, pane_w, pane_h):
    """
    Size of a crop on its pane: the whole pane when the crop has the pane's
    aspect (up to rounding), else the crop fitted inside with black around.
    """
    scale = min(pane_w / box.w, pane_h / box.h)
    w, h = int(round(box.w * scale)), int(round(box.h * scale))
    if pane_w - w <= 2 and pane_h - h <= 2:
        return pane_w, pane_h
    return w, h


class VersusLayout(BaseLayout):
    """
    Layout 'Versus' for 2 people: the left half of the source on top and the
    right half below. Each half is cropped to its pane's aspect, keeping at
    least VERSUS_MIN_HEIGHT of it (a wide pane then has black on the sides);
    the manual camera (``manual_camera_x/y/zoom``) sets the framing of both.
    ``camera_selection_position`` and ``gameplay_position`` do not apply.
    """
    def apply(self, clip):
        target_w, target_h = self.target_w, self.target_h

        # Split screen half height
        pane_h = target_h // 2
        manual = ManualCamera.from_coords(self.manual_camera_coords)
        top, bottom = versus_boxes(clip.w, clip.h, target_w / pane_h, manual)

        # Person A (Top) and Person B (Bottom), each centred in its pane
        panes = []
        for index, (box, pane_y) in enumerate(((top, 0), (bottom, target_h - pane_h))):
            w, h = pane_size(box, target_w, pane_h)
            pane = self._crop_pane(clip, box, index)
            pane = pane.resized(new_size=(w, h))
            panes.append(pane.with_position(((target_w - w) // 2, pane_y + (pane_h - h) // 2)))
        p1, p2 = panes
        
        bg = ColorClip(size=(target_w, target_h), color=(0,0,0), duration=clip.duration)
        final = CompositeVideoClip([bg, p1, p2], size=(target_w, target_h))
        
        if clip.audio:
            final = final.with_audio(clip.audio)
            
        return final

    def _crop_pane(self, clip, box, index):
        """The crop of one pane (0 top, 1 bottom): fixed here, it follows a face in ``podcast``."""
        return clip.cropped(x1=box.x1, y1=box.y1, width=box.w, height=box.h)

class ActiveSpeakerLayout(BaseLayout):
    """
    Layout 'ActiveSpeaker': the camera follows whoever is talking (CORE-02).
    Faces are tracked (CORE-01), the speaker timeline comes from
    SpeakerTrackingEngine, and the crop cuts to the speaker's face at each
    turn. Without faces it falls back to the centered crop. It needs the
    faces, so ``use_facetracking`` does not turn it off; a ``static`` layout
    (previews) uses the centered crop without tracking.
    """
    def apply(self, clip):
        if self.static:
            return self._centered(clip)
        try:
            followed = self._follow_speaker(clip)
        except Exception as exc:
            logger.error("layout.active_speaker_failed error=%s", type(exc).__name__)
            followed = None
        return followed if followed is not None else self._centered(clip)

    def _follow_speaker(self, clip):
        from apps.videos.services.speaker_engine import SpeakerTrackingEngine, speaker_at

        faces = FaceTracker().track_faces(clip, fps=self.tracking_fps)
        if not faces.tracks:
            return None
        timeline = SpeakerTrackingEngine().timeline(clip, faces)
        path = faces.path_following(speaker_at(timeline))
        if not path.points:
            return None

        aspect = self.target_w / self.target_h
        crop_h = clip.h
        crop_w = int(crop_h * aspect)
        if crop_w > clip.w:
            crop_w = clip.w
            crop_h = int(crop_w / aspect)

        def speaker_frame(get_frame, t):
            frame = get_frame(t)
            face_x, _, _, _ = path.get_at(t)
            x1 = max(0, min(int(face_x - crop_w // 2), clip.w - crop_w))
            y1 = (clip.h - crop_h) // 2
            return frame[y1:y1 + crop_h, x1:x1 + crop_w]

        logger.info("layout.active_speaker segments=%d faces=%d", len(timeline), len(faces.tracks))
        return clip.transform(speaker_frame).resized(new_size=(self.target_w, self.target_h))

    def _centered(self, clip):
        target_w, target_h = self.target_w, self.target_h
        main = clip.resized(height=target_h)
        if main.w < target_w:
            main = clip.resized(width=target_w)
            
        main = main.cropped(x_center=main.w//2, y_center=main.h//2, width=target_w, height=target_h)
        
        if clip.audio:
            main = main.with_audio(clip.audio)
            
        return main
