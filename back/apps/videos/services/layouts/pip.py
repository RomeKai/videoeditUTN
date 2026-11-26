import logging

from moviepy import CompositeVideoClip

from .camera import Box, fit_size, frame_box, place
from .gaming import GamingLayout

logger = logging.getLogger(__name__)

# Size of the camera inset, as the "Nuevo proyecto" drawing shows it: 42 % of
# the width, and 24 % of the height in 9:16 (40 % in 1:1 and 16:9).
PIP_INSET_WIDTH = 0.42
PIP_INSET_HEIGHT_VERTICAL = 0.24
PIP_INSET_HEIGHT_WIDE = 0.40
# Gap between the inset and the edges of the frame, as a share of the width.
PIP_MARGIN = 0.04
PIP_CORNERS = ("bottom-right", "bottom-left", "top-right", "top-left")
PIP_DEFAULT_CORNER = "bottom-right"


def _even(value):
    value = int(value)
    return max(2, value - value % 2)


def pip_inset_box(target_w, target_h, corner=PIP_DEFAULT_CORNER):
    """Where the camera inset goes on the output: an even-sized box in a corner."""
    vertical = target_w / target_h < 0.75
    w = _even(target_w * PIP_INSET_WIDTH)
    h = _even(target_h * (PIP_INSET_HEIGHT_VERTICAL if vertical else PIP_INSET_HEIGHT_WIDE))
    margin = int(round(target_w * PIP_MARGIN))
    if corner not in PIP_CORNERS:
        corner = PIP_DEFAULT_CORNER
    vertical_side, horizontal_side = corner.split("-")
    x1 = target_w - margin - w if horizontal_side == "right" else margin
    y1 = target_h - margin - h if vertical_side == "bottom" else margin
    return Box(x1, y1, w, h)


class PipLayout(GamingLayout):
    """
    Layout 'PiP' (Picture-in-Picture, CORE-03): the video fills the frame and
    the camera goes in an inset in a corner (``layout_options['pip_corner']``,
    bottom right by default). Replaces the placeholder that rendered 'pip' as ``fit``.

    The inset is the camera pane of ``split`` with the inset's aspect: it
    follows the face found in ``camera_selection_position`` (the whole
    frame by default), or the manual camera when the project set one.
    """
    corner = PIP_DEFAULT_CORNER

    def apply(self, clip, camera_pos=None):
        target_w, target_h = self.target_w, self.target_h

        # 1. Background: the source cropped to the output's aspect, centred.
        crop_w, crop_h = fit_size(clip.w, clip.h, target_w / target_h)
        box = place(clip.w / 2, clip.h / 2, crop_w, crop_h, frame_box(clip.w, clip.h))
        bg = clip.cropped(x1=box.x1, y1=box.y1, width=box.w, height=box.h)
        bg = bg.resized(new_size=(target_w, target_h))

        # 2. Camera inset
        corner = self.layout_options.get('pip_corner') or self.corner
        inset = pip_inset_box(target_w, target_h, corner)
        cam = self._camera(clip, inset.w / inset.h)
        cam = cam.resized(new_size=(inset.w, inset.h)).with_position((inset.x1, inset.y1))

        final = CompositeVideoClip([bg, cam], size=(target_w, target_h))
        if clip.audio:
            final = final.with_audio(clip.audio)
        logger.info("layout.pip corner=%s facetracking=%s", corner, self.use_facetracking)
        return final
