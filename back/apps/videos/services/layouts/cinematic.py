import logging

from moviepy import ColorClip, CompositeVideoClip

from .camera import fit_size, frame_box, place
from .interface import BaseLayout
from ..face_tracker import FaceTracker

logger = logging.getLogger(__name__)

# Film look: a 2.35:1 band with black above and below.
CINEMATIC_ASPECT = 2.35
# Without faces the band is centred this high on the source: faces and
# action sit a bit above the middle.
CINEMATIC_CENTER_Y = 0.45


def even_floor(value):
    """Largest even integer not above ``value`` (H.264 needs even sizes)."""
    value = int(value)
    return max(2, value - value % 2)


def cinematic_band(target_w, target_h):
    """Size of the 2.35:1 band inside the output: the full width when it fits."""
    w = even_floor(target_w)
    h = even_floor(round(target_w / CINEMATIC_ASPECT))
    if h > target_h:
        h = even_floor(target_h)
        w = even_floor(round(h * CINEMATIC_ASPECT))
    return w, h


class CinematicLayout(BaseLayout):
    """
    Layout 'Cinematic' (CORE-03): a 2.35:1 letterbox band centred on black.

    The source is cropped to 2.35:1. With face tracking the crop follows the
    most important face on screen (CORE-01 ``framing_path``: it cuts between
    faces instead of panning); without faces, or for a ``static`` preview,
    it stays centred at ``CINEMATIC_CENTER_Y``.
    """
    def apply(self, clip):
        target_w, target_h = self.target_w, self.target_h
        band_w, band_h = cinematic_band(target_w, target_h)
        crop_w, crop_h = fit_size(clip.w, clip.h, CINEMATIC_ASPECT)
        area = frame_box(clip.w, clip.h)

        path = self._face_path(clip) if self.use_facetracking else None
        if path is not None and path.points:
            def follow(get_frame, t):
                frame = get_frame(t)
                face_x, face_y, _, _ = path.get_at(t)
                box = place(face_x, face_y, crop_w, crop_h, area)
                return frame[box.y1:box.y1 + box.h, box.x1:box.x1 + box.w]

            band = clip.transform(follow)
            logger.info("layout.cinematic mode=faces")
        else:
            box = place(clip.w / 2, clip.h * CINEMATIC_CENTER_Y, crop_w, crop_h, area)
            band = clip.cropped(x1=box.x1, y1=box.y1, width=box.w, height=box.h)
            logger.info("layout.cinematic mode=center")

        band = band.resized(new_size=(band_w, band_h))
        band = band.with_position(((target_w - band_w) // 2, (target_h - band_h) // 2))

        bg = ColorClip(size=(target_w, target_h), color=(0, 0, 0), duration=clip.duration)
        final = CompositeVideoClip([bg, band], size=(target_w, target_h))
        if clip.audio:
            final = final.with_audio(clip.audio)
        return final

    def _face_path(self, clip):
        """Camera path over every face (CORE-01), or None if tracking fails."""
        try:
            faces = FaceTracker().track_faces(clip, fps=self.tracking_fps)
        except Exception as exc:
            logger.error("layout.cinematic_tracking_failed error=%s", type(exc).__name__)
            return None
        return faces.framing_path()
