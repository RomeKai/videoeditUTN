"""
CORE-03 · Layouts nuevos: reaction, cinematic y PiP real.

Las dimensiones en 9:16, 1:1 y 16:9 las cubre test_layouts.py (toma todo el
LAYOUT_REGISTRY); acá se verifica lo propio de cada layout, sin face tracking.
"""
import pytest

from apps.videos.services.layouts import get_layout_strategy

TARGETS = [
    pytest.param(360, 640, id="9:16"),
    pytest.param(360, 360, id="1:1"),
    pytest.param(640, 360, id="16:9"),
]


# --- reaction ----------------------------------------------------------------


@pytest.mark.parametrize("target_w, target_h", TARGETS)
def test_reaction_puts_the_content_on_top_and_the_face_below_half_and_half(target_w, target_h, synthetic_clip):
    result = get_layout_strategy("reaction", target_w, target_h).apply(synthetic_clip)

    _bg, content, face = result.clips
    face_h = int(target_h * 0.5)
    assert tuple(face.size) == (target_w, face_h)
    assert tuple(content.size) == (target_w, target_h - face_h)
    assert content.pos(0) == ("center", "top")
    assert face.pos(0) == ("center", "bottom")


def test_split_keeps_the_camera_on_top():
    """Parametrizar los paneles para reaction no cambia split."""
    from apps.videos.services.layouts import GamingLayout, ReactionLayout

    assert (GamingLayout.CAMERA_SHARE, GamingLayout.CAMERA_ON_TOP) == (0.35, True)
    assert issubclass(ReactionLayout, GamingLayout)


# --- cinematic ---------------------------------------------------------------


@pytest.mark.parametrize("target_w, target_h, band", [
    (360, 640, (360, 152)),
    (360, 360, (360, 152)),
    (640, 360, (640, 272)),
    (1080, 1920, (1080, 460)),
    (300, 100, (234, 100)),  # más ancho que 2.35:1: la banda toma todo el alto
])
def test_cinematic_band_is_2_35_and_even(target_w, target_h, band):
    from apps.videos.services.layouts.cinematic import cinematic_band

    w, h = cinematic_band(target_w, target_h)
    assert (w, h) == band
    assert w % 2 == 0 and h % 2 == 0
    assert w <= target_w and h <= target_h
    assert w / h == pytest.approx(2.35, rel=0.02)


@pytest.mark.parametrize("target_w, target_h", TARGETS)
def test_cinematic_letterboxes_the_band_on_black(target_w, target_h, synthetic_clip):
    from apps.videos.services.layouts.cinematic import cinematic_band

    result = get_layout_strategy("cinematic", target_w, target_h).apply(synthetic_clip)

    band_w, band_h = cinematic_band(target_w, target_h)
    _bg, band = result.clips
    assert tuple(band.size) == (band_w, band_h)
    frame = result.get_frame(0.5)
    top = (target_h - band_h) // 2
    assert frame[:top].max() == 0  # barra negra de arriba
    assert frame[top + band_h:].max() == 0  # barra negra de abajo
    assert frame[top:top + band_h].max() > 0


class _FakePath:
    def __init__(self, x, y):
        self.points = [object()]
        self._xy = (x, y)

    def get_at(self, t):
        return (*self._xy, 10, 10)


@pytest.fixture
def rows_clip():
    """Clip 640x360 donde cada fila tiene su propio valor: el recorte vertical se nota."""
    import numpy as np
    from moviepy import VideoClip

    rows = np.arange(360, dtype=np.uint8)[:, None, None].repeat(640, axis=1).repeat(3, axis=2)
    clip = VideoClip(lambda t: rows, duration=1).with_fps(4)
    yield clip
    clip.close()


def test_cinematic_follows_the_face_path(rows_clip, monkeypatch):
    """Con caras, el recorte 2.35:1 se centra en la cara: una cara abajo baja el recorte."""
    from apps.videos.services.layouts.cinematic import CinematicLayout

    layout = CinematicLayout(640, 360, use_facetracking=True)
    monkeypatch.setattr(layout, "_face_path", lambda clip: _FakePath(320, 350))
    followed = layout.apply(rows_clip).clips[1].get_frame(0.5)

    centered = get_layout_strategy("cinematic", 640, 360).apply(rows_clip).clips[1].get_frame(0.5)
    # El recorte (640x272) pegado abajo empieza en la fila 88; el centrado, más arriba.
    assert followed[0, 0, 0] == 88
    assert centered[0, 0, 0] < followed[0, 0, 0]


def test_cinematic_without_faces_stays_centered(synthetic_clip, monkeypatch):
    from apps.videos.services.layouts.cinematic import CinematicLayout
    from apps.videos.services.tracking_utils import TrackingPath

    layout = CinematicLayout(640, 360, use_facetracking=True)
    monkeypatch.setattr(layout, "_face_path", lambda clip: TrackingPath([]))
    tracked = layout.apply(synthetic_clip).clips[1]

    centered = get_layout_strategy("cinematic", 640, 360).apply(synthetic_clip).clips[1]
    assert (tracked.get_frame(0.5) == centered.get_frame(0.5)).all()


def test_static_cinematic_never_tracks(synthetic_clip, monkeypatch):
    from apps.videos.services.layouts.cinematic import CinematicLayout

    layout = CinematicLayout(640, 360, use_facetracking=True, static=True)
    monkeypatch.setattr(layout, "_face_path", lambda clip: pytest.fail("static no sigue caras"))
    layout.apply(synthetic_clip)


# --- pip ---------------------------------------------------------------------


def test_pip_is_no_longer_the_fit_placeholder():
    from apps.videos.services.layouts import LAYOUT_REGISTRY, FitLayout, PipLayout

    assert LAYOUT_REGISTRY["pip"] is PipLayout
    assert not issubclass(PipLayout, FitLayout)


@pytest.mark.parametrize("target_w, target_h, expected", [
    (360, 640, (196, 474, 150, 152)),  # 9:16: 42 % del ancho, 24 % del alto
    (360, 360, (196, 202, 150, 144)),  # 1:1: 40 % del alto
    (640, 360, (346, 190, 268, 144)),  # 16:9
])
def test_pip_inset_sits_bottom_right_with_even_size(target_w, target_h, expected):
    from apps.videos.services.layouts.pip import pip_inset_box

    box = pip_inset_box(target_w, target_h)
    assert tuple(box) == expected
    assert box.w % 2 == 0 and box.h % 2 == 0
    assert box.x1 + box.w <= target_w and box.y1 + box.h <= target_h


@pytest.mark.parametrize("corner", ["bottom-right", "bottom-left", "top-right", "top-left"])
def test_pip_inset_corners_stay_inside_the_frame(corner):
    from apps.videos.services.layouts.pip import pip_inset_box

    box = pip_inset_box(360, 640, corner)
    assert box.x1 >= 0 and box.y1 >= 0
    assert box.x1 + box.w <= 360 and box.y1 + box.h <= 640
    assert (box.x1 < 180) == corner.endswith("left")
    assert (box.y1 < 320) == corner.startswith("top")


def test_unknown_corner_falls_back_to_bottom_right():
    from apps.videos.services.layouts.pip import pip_inset_box

    assert pip_inset_box(360, 640, "middle") == pip_inset_box(360, 640)


@pytest.mark.parametrize("target_w, target_h", TARGETS)
def test_pip_fills_the_frame_and_insets_the_camera(target_w, target_h, synthetic_clip):
    from apps.videos.services.layouts.pip import pip_inset_box

    result = get_layout_strategy("pip", target_w, target_h).apply(synthetic_clip)

    bg, cam = result.clips
    inset = pip_inset_box(target_w, target_h)
    assert tuple(bg.size) == (target_w, target_h)  # sin bandas negras
    assert tuple(cam.size) == (inset.w, inset.h)
    assert cam.pos(0) == (inset.x1, inset.y1)
    assert result.get_frame(0.5).min(axis=2).max() > 0


# --- podcast -----------------------------------------------------------------


def _track(track_id, x, y, size=40, times=(0.0, 0.5, 1.0)):
    from apps.videos.services.face_tracking import Detection, FaceTrack

    return FaceTrack(id=track_id, detections=[
        Detection(t=t, x=x - size / 2, y=y - size / 2, w=size, h=size) for t in times
    ])


def _fake_faces(monkeypatch, *tracks):
    from apps.videos.services.face_tracker import FaceTracker
    from apps.videos.services.face_tracking import FaceTracks

    monkeypatch.setattr(FaceTracker, "__init__", lambda self, *a, **k: None)
    monkeypatch.setattr(
        FaceTracker, "track_faces",
        lambda self, clip, fps=2.0, tracker=None: FaceTracks(list(tracks), [0.0, 0.5, 1.0], tuple(clip.size)),
    )


def test_faces_are_split_by_the_half_they_are_in():
    from apps.videos.services.face_tracking import FaceTracks
    from apps.videos.services.layouts.podcast import faces_by_half

    left_face, right_face = _track(1, 100, 100), _track(2, 500, 100)
    left, right = faces_by_half(FaceTracks([left_face, right_face], [0.0], (640, 360)), 640)
    assert left == [left_face] and right == [right_face]


def test_follow_box_keeps_the_face_high_and_inside_its_half():
    from apps.videos.services.layouts.camera import Box
    from apps.videos.services.layouts.podcast import follow_box

    area = Box(320, 0, 320, 360)
    box = follow_box(330, 100, 200, 200, area)
    assert box.x1 == 320  # no se sale de la mitad derecha
    assert box.y1 == 100 - 80  # la cara queda al 40 % del alto del recorte
    assert (box.w, box.h) == (200, 200)


def test_podcast_follows_each_half_on_its_own(rows_clip, monkeypatch):
    """La cara de la izquierda está abajo: el panel de arriba baja; la derecha no tiene caras y queda fija."""
    _fake_faces(monkeypatch, _track(1, 160, 350))

    podcast = get_layout_strategy("podcast", 360, 640, use_facetracking=True).apply(rows_clip)
    versus = get_layout_strategy("versus", 360, 640).apply(rows_clip)

    _bg, top, bottom = podcast.clips
    _vbg, versus_top, versus_bottom = versus.clips
    assert int(top.get_frame(0.5)[0, 0, 0]) > int(versus_top.get_frame(0.5)[0, 0, 0]) + 50
    assert (bottom.get_frame(0.5) == versus_bottom.get_frame(0.5)).all()


def test_podcast_without_tracking_is_versus(rows_clip, monkeypatch):
    _fake_faces(monkeypatch, _track(1, 160, 350))

    podcast = get_layout_strategy("podcast", 360, 640, use_facetracking=False).apply(rows_clip)
    versus = get_layout_strategy("versus", 360, 640).apply(rows_clip)
    assert (podcast.get_frame(0.5) == versus.get_frame(0.5)).all()


def test_podcast_manual_camera_wins_over_tracking(rows_clip, monkeypatch):
    from apps.videos.services.face_tracker import FaceTracker

    monkeypatch.setattr(FaceTracker, "track_faces", lambda *a, **k: pytest.fail("la cámara manual no sigue caras"))
    manual = {"x": 0.5, "y": 0.8, "zoom": 1.5}
    get_layout_strategy("podcast", 360, 640, use_facetracking=True, manual_camera_coords=manual).apply(rows_clip)


def test_static_podcast_never_tracks(rows_clip, monkeypatch):
    from apps.videos.services.face_tracker import FaceTracker

    monkeypatch.setattr(FaceTracker, "track_faces", lambda *a, **k: pytest.fail("static no sigue caras"))
    get_layout_strategy("podcast", 360, 640, use_facetracking=True, static=True).apply(rows_clip)


def test_pip_corner_comes_from_the_layout_options(synthetic_clip):
    from apps.videos.services.layouts.pip import pip_inset_box

    result = get_layout_strategy("pip", 360, 640, layout_options={"pip_corner": "top-left"}).apply(synthetic_clip)

    inset = pip_inset_box(360, 640, "top-left")
    assert result.clips[1].pos(0) == (inset.x1, inset.y1)


def test_layout_options_default_to_empty():
    assert get_layout_strategy("fill", 360, 640).layout_options == {}


# --- trending ----------------------------------------------------------------


@pytest.mark.parametrize("value, expected", [
    ("#00FF80", (0, 255, 128)),
    ("#ff2d55", (255, 45, 85)),
    ("rojo", (255, 45, 85)),  # inválido: color por defecto
    (None, (255, 45, 85)),
])
def test_trending_color_parsing(value, expected):
    from apps.videos.services.layouts.trending import parse_color

    assert parse_color(value) == expected


@pytest.mark.parametrize("target_w, target_h", [(360, 640), (360, 360), (640, 360), (1080, 1920)])
@pytest.mark.parametrize("has_text", [True, False])
def test_trending_geometry_is_even_and_inside(target_w, target_h, has_text):
    from apps.videos.services.layouts.trending import trending_geometry

    border, band_h, inner_w, inner_h = trending_geometry(target_w, target_h, has_text)
    assert inner_w % 2 == 0 and inner_h % 2 == 0
    assert border * 2 + inner_w <= target_w
    assert border * 2 + band_h + inner_h <= target_h
    assert (band_h > 0) == has_text


def test_gradient_goes_from_one_color_to_the_other():
    from apps.videos.services.layouts.trending import gradient_frame

    frame = gradient_frame(400, 200, (255, 0, 0), (0, 0, 255), angle=0.0)
    assert frame.shape == (200, 400, 3)
    assert frame[100, 0, 0] > frame[100, -1, 0]  # rojo a la izquierda
    assert frame[100, -1, 2] > frame[100, 0, 2]  # azul a la derecha


@pytest.mark.parametrize("target_w, target_h", TARGETS)
def test_trending_frames_the_video_with_the_text_above(target_w, target_h, synthetic_clip):
    from apps.videos.services.layouts.trending import trending_geometry

    options = {"trending_color": "#00FF80", "trending_text": "Lo que nadie te cuenta"}
    result = get_layout_strategy("trending", target_w, target_h, layout_options=options).apply(synthetic_clip)

    border, band_h, inner_w, inner_h = trending_geometry(target_w, target_h, True)
    _bg, video = result.clips
    assert tuple(video.size) == (inner_w, inner_h)
    assert video.pos(0) == (border, border + band_h)
    frame = result.get_frame(0.5)
    band = frame[border:border + band_h, border:border + inner_w]
    assert (band.min(axis=2) > 200).any()  # texto blanco
    assert (band.max(axis=2) < 60).any()  # contorno oscuro


def test_trending_without_text_has_no_band(synthetic_clip):
    from apps.videos.services.layouts.trending import trending_geometry

    result = get_layout_strategy("trending", 360, 640).apply(synthetic_clip)
    border, band_h, _w, _h = trending_geometry(360, 640, False)
    assert band_h == 0
    assert result.clips[1].pos(0) == (border, border)


def test_trending_gradient_turns_over_time(synthetic_clip):
    result = get_layout_strategy("trending", 360, 640).apply(synthetic_clip)
    bg = result.clips[0]
    assert not (bg.get_frame(0.0) == bg.get_frame(1.5)).all()
