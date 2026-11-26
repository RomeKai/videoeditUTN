"""CORE-03 · El comando que renderiza muestras de los layouts para revisarlas a ojo."""
import pytest
from django.core.management import CommandError, call_command
from PIL import Image


def test_renders_a_png_per_new_layout_and_format(tmp_path):
    call_command("render_layout_samples", out=str(tmp_path), seconds=1, formats=["9x16", "16x9"])

    for layout in ("reaction", "cinematic", "pip", "podcast", "trending"):
        with Image.open(tmp_path / f"{layout}_9x16.png") as image:
            assert image.size == (360, 640)
        with Image.open(tmp_path / f"{layout}_16x9.png") as image:
            assert image.size == (640, 360)


def test_writes_the_clip_with_mp4(tmp_path):
    call_command("render_layout_samples", out=str(tmp_path), seconds=0.5, layouts=["pip"], formats=["1x1"], mp4=True)

    assert (tmp_path / "pip_1x1.mp4").stat().st_size > 0


def test_unknown_layout_is_an_error(tmp_path):
    with pytest.raises(CommandError, match="desconocidos"):
        call_command("render_layout_samples", out=str(tmp_path), layouts=["nope"])
