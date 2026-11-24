"""
Which region of the source a camera pane shows (layouts ``split`` and ``versus``).

Pure functions on sizes and coordinates, without MoviePy: the layouts crop
with the boxes they return, and the tests check the boxes without a video.

- ``camera_selection_position`` picks a preset region (``preset_region``):
  the left half, the right half, or the whole frame for ``center``.
- The manual camera (``manual_camera_x``, ``manual_camera_y``,
  ``manual_camera_zoom``) is used only when the project changed it from the
  model defaults (``ManualCamera.from_coords``). Then x and y (0 to 1) are the
  centre of the crop and the zoom divides the largest box with the pane's
  aspect that fits (``manual_box``); the box is moved back inside the frame.
"""

import math
from dataclasses import dataclass
from typing import Any, Mapping, NamedTuple, Optional, Tuple

# VideoProject.manual_camera_* defaults (a test keeps them in sync): with these
# values the layout keeps its own framing.
DEFAULT_X = 0.5
DEFAULT_Y = 0.2
DEFAULT_ZOOM = 1.0
# Zoom 1 is the largest box with the pane's aspect; beyond 4 the crop of a
# 720p source is a few dozen pixels wide.
MAX_ZOOM = 4.0


class Box(NamedTuple):
    """A crop in pixels of the source: top-left corner and size."""

    x1: int
    y1: int
    w: int
    h: int


@dataclass(frozen=True)
class ManualCamera:
    x: float
    y: float
    zoom: float

    @classmethod
    def from_coords(cls, coords: Optional[Mapping[str, Any]]) -> Optional["ManualCamera"]:
        """
        The manual crop, or None when every value is still the model default
        (the project did not set it). Out-of-range values are clamped: x and
        y to 0..1, the zoom to 1..MAX_ZOOM.
        """
        if not coords:
            return None
        x = _number(coords.get("x"), DEFAULT_X)
        y = _number(coords.get("y"), DEFAULT_Y)
        zoom = _number(coords.get("zoom"), DEFAULT_ZOOM)
        if all(math.isclose(v, d, abs_tol=1e-6) for v, d in ((x, DEFAULT_X), (y, DEFAULT_Y), (zoom, DEFAULT_ZOOM))):
            return None
        return cls(x=_clamp(x, 0.0, 1.0), y=_clamp(y, 0.0, 1.0), zoom=_clamp(zoom, 1.0, MAX_ZOOM))


def _number(value: Any, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def fit_size(area_w: int, area_h: int, aspect: float) -> Tuple[int, int]:
    """Largest ``w x h`` with ``w / h == aspect`` that fits in ``area_w x area_h``."""
    w, h = area_w, int(area_w / aspect)
    if h > area_h:
        w, h = int(area_h * aspect), area_h
    return max(1, min(w, area_w)), max(1, min(h, area_h))


def place(center_x: float, center_y: float, w: int, h: int, area: Box) -> Box:
    """A ``w x h`` box centred on the point, moved back inside ``area``."""
    w, h = min(w, area.w), min(h, area.h)
    x1 = max(area.x1, min(int(center_x - w / 2), area.x1 + area.w - w))
    y1 = max(area.y1, min(int(center_y - h / 2), area.y1 + area.h - h))
    return Box(x1, y1, w, h)


def frame_box(frame_w: int, frame_h: int) -> Box:
    return Box(0, 0, int(frame_w), int(frame_h))


def manual_box(camera: ManualCamera, area: Box, aspect: float) -> Box:
    """The manual crop inside ``area``: centre (x, y) of the area, largest box / zoom."""
    w, h = fit_size(area.w, area.h, aspect)
    w, h = max(1, int(w / camera.zoom)), max(1, int(h / camera.zoom))
    return place(area.x1 + camera.x * area.w, area.y1 + camera.y * area.h, w, h, area)


def preset_region(position: Optional[str], frame_w: int, frame_h: int) -> Box:
    """``left`` is the left half, ``right`` the right half; ``center`` (or anything else) the whole frame."""
    half = int(frame_w) // 2
    if position == "left":
        return Box(0, 0, half, int(frame_h))
    if position == "right":
        return Box(int(frame_w) - half, 0, half, int(frame_h))
    return frame_box(frame_w, frame_h)


def covers(region: Box, frame_w: int, frame_h: int) -> bool:
    return region == frame_box(frame_w, frame_h)
