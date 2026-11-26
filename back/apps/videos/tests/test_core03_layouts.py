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
