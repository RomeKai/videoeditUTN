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
