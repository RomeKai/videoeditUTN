from moviepy import CompositeVideoClip, ColorClip, VideoClip
from .camera import Box, ManualCamera, fit_size, manual_box, place
from .interface import BaseLayout
from ..face_tracker import FaceTracker
import logging

logger = logging.getLogger(__name__)


def versus_boxes(frame_w, frame_h, aspect, manual=None):
    """
    The crops of the two panes: one per half of the source (left on top,
    right below), with the pane's aspect. Centred in each half by default;
    a manual camera frames both halves the same way, with x and y measured
    inside each half.
    """
    half = frame_w // 2
    boxes = []
    for area in (Box(0, 0, half, frame_h), Box(half, 0, half, frame_h)):
        if manual is not None:
            boxes.append(manual_box(manual, area, aspect))
        else:
            w, h = fit_size(area.w, area.h, aspect)
            boxes.append(place(area.x1 + area.w // 2, area.h // 2, w, h, area))
    return boxes


class VersusLayout(BaseLayout):
    """
    Layout 'Versus' for 2 people: the left half of the source on top and the
    right half below. Each pane is cropped to its own aspect so it fills the
    width; the manual camera (``manual_camera_x/y/zoom``) sets the framing of
    both. ``camera_selection_position`` and ``gameplay_position`` do not apply.
    """
    def apply(self, clip):
        target_w, target_h = self.target_w, self.target_h

        # Split screen half height
        pane_h = target_h // 2
        manual = ManualCamera.from_coords(self.manual_camera_coords)
        top, bottom = versus_boxes(clip.w, clip.h, target_w / pane_h, manual)

        # Person A (Top)
        p1 = clip.cropped(x1=top.x1, y1=top.y1, width=top.w, height=top.h)
        p1 = p1.resized(new_size=(target_w, pane_h))
        p1 = p1.with_position(('center', 'top'))

        # Person B (Bottom)
        p2 = clip.cropped(x1=bottom.x1, y1=bottom.y1, width=bottom.w, height=bottom.h)
        p2 = p2.resized(new_size=(target_w, pane_h))
        p2 = p2.with_position(('center', 'bottom'))
        
        bg = ColorClip(size=(target_w, target_h), color=(0,0,0), duration=clip.duration)
        final = CompositeVideoClip([bg, p1, p2], size=(target_w, target_h))
        
        if clip.audio:
            final = final.with_audio(clip.audio)
            
        return final

class ActiveSpeakerLayout(BaseLayout):
    """
    Layout 'ActiveSpeaker': the camera follows whoever is talking (CORE-02).
    Faces are tracked (CORE-01), the speaker timeline comes from
    SpeakerTrackingEngine, and the crop cuts to the speaker's face at each
    turn. Without faces it falls back to the centered crop.
    """
    def apply(self, clip):
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
