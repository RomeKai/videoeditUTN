"""
The camera region of the split and versus layouts honours
camera_selection_position and the manual camera (x, y, zoom).

The boxes are checked as pure functions, and on a clip whose pixels encode
their own coordinates (red = x, green = y): the corners of a rendered pane
tell which box of the source it shows. No real video, no MediaPipe.
"""
import math
from unittest.mock import patch

import numpy as np
import pytest

from apps.videos.models import VideoProject
from apps.videos.services.layouts import get_layout_strategy
from apps.videos.services.layouts.camera import (
    DEFAULT_X,
    DEFAULT_Y,
    DEFAULT_ZOOM,
    MAX_ZOOM,
    Box,
    ManualCamera,
    manual_box,
    preset_region,
)
from apps.videos.services.layouts.gaming import camera_size, static_box, tracked_box
from apps.videos.services.layouts.pro import VERSUS_MIN_HEIGHT, pane_size, versus_boxes
from apps.videos.services.tracking_utils import TrackingPath, TrackingPoint

W, H = 640, 360
# Camera pane of a 360x640 split: 35 % of the height.
CAM_W, CAM_H = 360, int(640 * 0.35)
CAM_ASPECT = CAM_W / CAM_H
# The split camera's automatic crop of the coded clip without tracking.
AUTO_W, AUTO_H = 424, 264
FACE_TRACKER = "apps.videos.services.layouts.gaming.FaceTracker"


def coded_clip(reads=None):
    """2 s, 640x360: red encodes x and green encodes y (0..255)."""
    from moviepy import VideoClip

    xs = np.round(np.arange(W) * 255 / (W - 1)).astype(np.uint8)
    ys = np.round(np.arange(H) * 255 / (H - 1)).astype(np.uint8)
    frame = np.zeros((H, W, 3), dtype=np.uint8)
    frame[:, :, 0] = xs[None, :]
    frame[:, :, 1] = ys[:, None]

    def make_frame(t):
        if reads is not None:
            reads.append(t)
        return frame

    return VideoClip(make_frame, duration=2).with_fps(12)


def shown_box(pane):
    """The source box a rendered pane shows, decoded from its corner pixels."""
    frame = pane.get_frame(0).astype(float)
    x1 = round(frame[0, 0, 0] * (W - 1) / 255)
    x2 = round(frame[0, -1, 0] * (W - 1) / 255)
    y1 = round(frame[0, 0, 1] * (H - 1) / 255)
    y2 = round(frame[-1, 0, 1] * (H - 1) / 255)
    return x1, y1, x2, y2


def assert_shows(pane, box, tolerance=3):
    """A pixel of the coded clip is ~2.5 px of the source; resizing blurs the edge a little."""
    expected = (box.x1, box.y1, box.x1 + box.w - 1, box.y1 + box.h - 1)
    shown = shown_box(pane)
    assert all(abs(a - b) <= tolerance for a, b in zip(shown, expected)), (shown, expected)


# --- Manual camera: when it is on and how its values are read ---------------


def test_the_defaults_are_the_model_defaults():
    fields = {name: VideoProject._meta.get_field(name).default
              for name in ("manual_camera_x", "manual_camera_y", "manual_camera_zoom")}
    assert fields == {"manual_camera_x": DEFAULT_X, "manual_camera_y": DEFAULT_Y,
                      "manual_camera_zoom": DEFAULT_ZOOM}


@pytest.mark.parametrize("coords", [
    None,
    {},
    {"x": 0.5, "y": 0.2, "zoom": 1.0},
    {"x": "0.5", "y": None, "zoom": 1},  # missing or textual values read as the default
])
def test_with_the_defaults_there_is_no_manual_camera(coords):
    assert ManualCamera.from_coords(coords) is None


@pytest.mark.parametrize("changed", [{"x": 0.1}, {"y": 0.6}, {"zoom": 2.0}])
def test_any_changed_value_turns_the_manual_camera_on(changed):
    coords = {"x": 0.5, "y": 0.2, "zoom": 1.0, **changed}
    camera = ManualCamera.from_coords(coords)
    assert camera == ManualCamera(x=coords["x"], y=coords["y"], zoom=coords["zoom"])


def test_out_of_range_values_are_clamped():
    assert ManualCamera.from_coords({"x": -1, "y": 2, "zoom": 10}) == ManualCamera(0.0, 1.0, MAX_ZOOM)
    assert ManualCamera.from_coords({"x": 0.3, "y": 0.2, "zoom": 0.2}).zoom == 1.0
    assert ManualCamera.from_coords({"x": math.nan, "y": 0.2, "zoom": 1.0}) is None


# --- Pure boxes ---------------------------------------------------------------


def test_zoom_one_keeps_the_layout_crop_size():
    box = manual_box(ManualCamera(0.45, 0.5, 1.0), Box(0, 0, W, H), AUTO_W, AUTO_H)
    assert box == Box(76, 48, AUTO_W, AUTO_H)


def test_the_manual_box_is_centred_on_x_y_and_divided_by_the_zoom():
    box = manual_box(ManualCamera(0.5, 0.5, 2.0), Box(0, 0, W, H), 576, 360)
    assert box == Box(176, 90, 288, 180)


@pytest.mark.parametrize("x, y, expected", [
    (1.0, 1.0, Box(496, 270, 144, 90)),
    (0.0, 0.0, Box(0, 0, 144, 90)),
])
def test_the_manual_box_is_clamped_to_the_frame(x, y, expected):
    assert manual_box(ManualCamera(x, y, 4.0), Box(0, 0, W, H), 576, 360) == expected


@pytest.mark.parametrize("position, expected", [
    ("left", Box(0, 0, 320, 360)),
    ("right", Box(320, 0, 320, 360)),
    ("center", Box(0, 0, 640, 360)),
    ("anything", Box(0, 0, 640, 360)),
])
def test_preset_regions(position, expected):
    assert preset_region(position, W, H) == expected


@pytest.mark.parametrize("position, expected", [
    ("left", Box(0, 48, 424, 264)),
    ("center", Box(108, 48, 424, 264)),  # the centred crop the split layout always used
    ("right", Box(216, 48, 424, 264)),
])
def test_split_static_camera_box_per_preset(position, expected):
    w, h = camera_size(W, H, CAM_ASPECT, H // 3)
    assert (w, h) == (AUTO_W, AUTO_H)
    assert static_box(preset_region(position, W, H), w, h, W, H) == expected


def test_versus_default_boxes_have_the_pane_aspect_and_sit_high():
    # 9:16: the crop keeps 79 % of the height, centred at 40 % of it (clamped).
    assert versus_boxes(W, H, 360 / 320) == [Box(0, 2, 320, 284), Box(320, 2, 320, 284)]


@pytest.mark.parametrize("pane_aspect", [360 / 180, 640 / 180], ids=["1:1", "16:9"])
def test_a_wide_versus_pane_keeps_at_least_60_percent_of_the_height(pane_aspect):
    # The pane's aspect would keep 44 % (1:1) or 25 % (16:9): the crop keeps
    # 60 %, centred at 40 % of the height, so the top of the heads stays.
    boxes = versus_boxes(W, H, pane_aspect)
    assert boxes == [Box(0, 36, 320, 216), Box(320, 36, 320, 216)]
    assert boxes[0].h == round(H * VERSUS_MIN_HEIGHT)


def test_a_crop_narrower_than_its_pane_is_fitted_with_black_around():
    assert pane_size(Box(0, 36, 320, 216), 360, 180) == (267, 180)
    assert pane_size(Box(0, 36, 320, 216), 640, 180) == (267, 180)
    assert pane_size(Box(0, 2, 320, 284), 360, 320) == (360, 320)  # same aspect: fills the pane


def test_versus_manual_camera_frames_both_halves_alike():
    boxes = versus_boxes(W, H, 360 / 320, ManualCamera(0.5, 0.2, 2.0))
    assert boxes == [Box(80, 1, 160, 142), Box(400, 1, 160, 142)]


# --- Split layout -------------------------------------------------------------


@pytest.mark.parametrize("position", ["left", "center", "right"])
def test_split_camera_shows_the_selected_region(position):
    result = get_layout_strategy("split", 360, 640, camera_selection_pos=position).apply(coded_clip())

    w, h = camera_size(W, H, CAM_ASPECT, H // 3)
    assert_shows(result.clips[2], static_box(preset_region(position, W, H), w, h, W, H))


def test_split_manual_camera_sets_the_crop():
    coords = {"x": 0.9, "y": 0.8, "zoom": 3.0}
    result = get_layout_strategy("split", 360, 640, manual_camera_coords=coords).apply(coded_clip())

    expected = manual_box(ManualCamera.from_coords(coords), Box(0, 0, W, H), AUTO_W, AUTO_H)
    assert expected == Box(499, 244, 141, 88)
    assert_shows(result.clips[2], expected)


def test_turning_the_manual_camera_on_moves_the_crop_without_zooming_out():
    """Nudging x from 0.5 to 0.45 keeps the automatic crop size (zoom 1)."""
    coords = {"x": 0.45, "y": 0.2, "zoom": 1.0}
    result = get_layout_strategy("split", 360, 640, manual_camera_coords=coords).apply(coded_clip())

    assert_shows(result.clips[2], Box(76, 0, AUTO_W, AUTO_H))


def test_split_manual_camera_wins_over_face_tracking():
    with patch(FACE_TRACKER) as tracker:
        layout = get_layout_strategy(
            "split", 360, 640, use_facetracking=True, manual_camera_coords={"x": 0.2, "y": 0.2, "zoom": 2.0},
        )
        layout.apply(coded_clip())
    tracker.assert_not_called()


def test_split_tracks_faces_only_inside_the_selected_region():
    face = TrackingPath([TrackingPoint(0, 100, 100, 60, 60)])  # in the region's coordinates
    with patch(FACE_TRACKER) as tracker:
        tracker.return_value.generate_path.return_value = face
        result = get_layout_strategy(
            "split", 360, 640, use_facetracking=True, camera_selection_pos="right",
        ).apply(coded_clip())

        searched = tracker.return_value.generate_path.call_args.args[0]
        assert tuple(searched.size) == (320, 360)
        # The right half: its first column is x = 320 of the source (red ~128).
        assert abs(int(searched.get_frame(0)[0, 0, 0]) - round(320 * 255 / (W - 1))) <= 1
        w, h = camera_size(W, H, CAM_ASPECT, 60)
        assert_shows(result.clips[2], tracked_box(320 + 100, 100, w, h, 0.35, W, H))


def test_split_default_tracking_searches_the_whole_frame():
    clip = coded_clip()
    with patch(FACE_TRACKER) as tracker:
        tracker.return_value.generate_path.return_value = TrackingPath([TrackingPoint(0, 500, 100, 60, 60)])
        result = get_layout_strategy("split", 360, 640, use_facetracking=True).apply(clip)

        assert tracker.return_value.generate_path.call_args.args[0] is clip
        w, h = camera_size(W, H, CAM_ASPECT, 60)
        assert_shows(result.clips[2], tracked_box(500, 100, w, h, 0.35, W, H))


def test_an_unknown_gameplay_position_is_centred_without_analysing_the_video():
    """The saliency branch is gone: only left, center and right exist."""
    counted = {}
    for position in ("center", "auto"):
        reads = []
        result = get_layout_strategy("split", 360, 640, gameplay_pos=position).apply(coded_clip(reads))
        counted[position] = len(reads)
        game = result.clips[1]
        crop_w = int(H * 360 / (640 - CAM_H))
        assert_shows(game, Box(W // 2 - crop_w // 2, 0, crop_w, H))
    assert counted["auto"] == counted["center"]


# --- Versus layout ------------------------------------------------------------


def test_versus_9_16_panes_fill_the_width():
    result = get_layout_strategy("versus", 360, 640).apply(coded_clip())

    _bg, top, bottom = result.clips
    assert tuple(top.size) == (360, 320)
    assert tuple(bottom.size) == (360, 320)


def test_versus_shows_each_half_with_the_manual_framing():
    coords = {"x": 0.5, "y": 0.2, "zoom": 2.0}
    result = get_layout_strategy("versus", 360, 640, manual_camera_coords=coords).apply(coded_clip())

    _bg, top, bottom = result.clips
    left_box, right_box = versus_boxes(W, H, 360 / 320, ManualCamera.from_coords(coords))
    assert_shows(top, left_box)
    assert_shows(bottom, right_box)


def test_versus_by_default_shows_each_half_from_near_the_top():
    _bg, top, bottom = get_layout_strategy("versus", 360, 640).apply(coded_clip()).clips

    assert_shows(top, Box(0, 2, 320, 284))
    assert_shows(bottom, Box(320, 2, 320, 284))


@pytest.mark.parametrize("target_w, target_h", [(360, 360), (640, 360)], ids=["1:1", "16:9"])
def test_wide_versus_keeps_60_percent_of_each_half_with_black_sides(target_w, target_h):
    result = get_layout_strategy("versus", target_w, target_h).apply(coded_clip())

    _bg, top, bottom = result.clips
    assert_shows(top, Box(0, 36, 320, 216))  # y 36..251: the top 10 % is all that is cut
    assert_shows(bottom, Box(320, 36, 320, 216))
    pane_x = (target_w - 267) // 2
    assert tuple(top.size) == tuple(bottom.size) == (267, 180)
    assert top.pos(0) == (pane_x, 0) and bottom.pos(0) == (pane_x, 180)
    frame = result.get_frame(0)
    assert not frame[90, : pane_x - 1].any()  # black on the sides of the pane
    assert frame[90, pane_x + 10].any()
