"""
CORE-06 · Cada LayoutStrategy.apply() con un clip sintético de 2s.

Para cada layout registrado se verifica: no crashea, respeta target_w x target_h,
dimensiones pares (H.264) y los frames renderizados tienen la forma correcta.
Sin face tracking (no necesita el modelo de MediaPipe). Si se agrega un layout
nuevo al LAYOUT_REGISTRY (CORE-03) entra solo en estos tests.
"""
import pytest

from apps.videos.services.layouts import LAYOUT_REGISTRY, FillLayout, get_layout_strategy
from apps.videos.utils.moviepy_utils import ensure_even

TARGETS = [
    pytest.param(360, 640, id="9:16"),
    pytest.param(360, 360, id="1:1"),
    pytest.param(640, 360, id="16:9"),
]


@pytest.mark.parametrize("target_w, target_h", TARGETS)
@pytest.mark.parametrize("layout_name", sorted(LAYOUT_REGISTRY))
def test_layout_produces_target_size(layout_name, target_w, target_h, synthetic_clip):
    strategy = get_layout_strategy(layout_name, target_w, target_h, use_facetracking=False)

    result = strategy.apply(synthetic_clip)

    assert tuple(result.size) == (target_w, target_h)
    assert result.w % 2 == 0 and result.h % 2 == 0
    assert result.duration == pytest.approx(synthetic_clip.duration)

    for t in (0, 1.0, 1.9):
        frame = result.get_frame(t)
        assert frame.shape[:2] == (target_h, target_w)


@pytest.mark.parametrize("gameplay_pos", ["left", "center", "right"])
def test_split_layout_gameplay_positions(gameplay_pos, synthetic_clip):
    strategy = get_layout_strategy("split", 360, 640, gameplay_pos=gameplay_pos)
    result = strategy.apply(synthetic_clip)
    assert tuple(result.size) == (360, 640)
    assert result.get_frame(0.5).shape == (640, 360, 3)


def test_unknown_layout_falls_back_to_fill():
    assert isinstance(get_layout_strategy("no-existe", 360, 640), FillLayout)


def test_registry_entries_receive_constructor_params():
    strategy = get_layout_strategy(
        "split", 720, 1280, use_facetracking=False,
        gameplay_pos="left", camera_selection_pos="right",
    )
    assert (strategy.target_w, strategy.target_h) == (720, 1280)
    assert strategy.gameplay_pos == "left"
    assert strategy.camera_selection_pos == "right"


@pytest.mark.parametrize("value, expected", [(1080, 1080), (1081, 1082), (607.5, 608), (0, 0)])
def test_ensure_even(value, expected):
    assert ensure_even(value) == expected
