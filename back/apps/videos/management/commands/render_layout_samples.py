"""
Renders the same few seconds with each layout, for visual review (CORE-03).

The automated tests check sizes and that nothing breaks; how a layout looks
is checked by eye. This command writes, per layout and output format, a PNG of
the middle frame and, with ``--mp4``, the clip itself:

    python manage.py render_layout_samples --out /tmp/layouts
    python manage.py render_layout_samples --source mi_video.mp4 --start 30 --mp4

Without ``--source`` it uses a synthetic 16:9 clip (a moving gradient with two
light "faces"), so it runs anywhere; the face tracker finds no real faces in
it and every layout shows its fallback framing. With a real video the
tracking layouts (podcast, cinematic, pip, reaction) follow the faces when
``--facetracking`` is on.
"""

from pathlib import Path

import numpy as np
from django.core.management.base import BaseCommand, CommandError, CommandParser

from apps.videos.services.layouts import LAYOUT_REGISTRY, get_layout_strategy

# The layouts CORE-03 added; --all renders every registered layout.
NEW_LAYOUTS = ("reaction", "cinematic", "pip", "podcast", "trending")
FORMATS = {"9x16": (360, 640), "1x1": (360, 360), "16x9": (640, 360)}
SAMPLE_OPTIONS = {"trending_text": "Lo que nadie te cuenta", "trending_color": "#FF2D55", "pip_corner": "bottom-right"}


def synthetic_clip(seconds):
    """A 640x360 clip: a gradient that moves, and two light ovals where two people would sit."""
    from moviepy import VideoClip

    w, h = 640, 360
    xs = np.linspace(0, 255, w, dtype=np.uint8)
    yy, xx = np.mgrid[0:h, 0:w]
    faces = np.zeros((h, w), dtype=bool)
    for cx in (160, 480):
        faces |= ((xx - cx) / 45.0) ** 2 + ((yy - 130) / 60.0) ** 2 <= 1

    def make_frame(t):
        row = np.roll(xs, int(t * 100) % w)
        frame = np.zeros((h, w, 3), dtype=np.uint8)
        frame[:, :, 0] = row
        frame[:, :, 1] = 90
        frame[:, :, 2] = 255 - row
        frame[faces] = (235, 200, 170)
        return frame

    return VideoClip(make_frame, duration=seconds).with_fps(24)


class Command(BaseCommand):
    help = "Renderiza unos segundos con cada layout (PNG y, opcional, MP4) para revisarlos a ojo (CORE-03)."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--out", default="layout_samples", help="Carpeta de salida (se crea si no existe).")
        parser.add_argument("--source", help="Video de origen; sin él se usa un clip sintético.")
        parser.add_argument("--start", type=float, default=0.0, help="Segundo del origen donde empieza el tramo.")
        parser.add_argument("--seconds", type=float, default=5.0, help="Duración del tramo (5 s por defecto).")
        parser.add_argument("--layouts", nargs="*", help="Layouts a renderizar; por defecto, los de CORE-03.")
        parser.add_argument("--all", action="store_true", help="Renderizar todos los layouts registrados.")
        parser.add_argument("--formats", nargs="*", choices=sorted(FORMATS), help="Formatos; por defecto, los tres.")
        parser.add_argument("--facetracking", action="store_true", help="Seguir caras (necesita el modelo de MediaPipe).")
        parser.add_argument("--mp4", action="store_true", help="Además del PNG, escribir el clip en MP4.")

    def handle(self, *args, **options):
        layouts = sorted(LAYOUT_REGISTRY) if options["all"] else (options["layouts"] or list(NEW_LAYOUTS))
        unknown = [name for name in layouts if name not in LAYOUT_REGISTRY]
        if unknown:
            raise CommandError(f"Layouts desconocidos: {', '.join(unknown)}. Opciones: {', '.join(sorted(LAYOUT_REGISTRY))}.")
        if options["seconds"] <= 0:
            raise CommandError("--seconds tiene que ser mayor que 0.")
        formats = options["formats"] or list(FORMATS)
        out = Path(options["out"])
        out.mkdir(parents=True, exist_ok=True)

        source = self._source(options)
        written = 0
        try:
            for layout in layouts:
                for name in formats:
                    target_w, target_h = FORMATS[name]
                    strategy = get_layout_strategy(
                        layout, target_w, target_h, use_facetracking=options["facetracking"],
                        layout_options=SAMPLE_OPTIONS,
                    )
                    result = strategy.apply(source)
                    try:
                        stem = out / f"{layout}_{name}"
                        result.save_frame(str(stem.with_suffix(".png")), t=source.duration / 2)
                        if options["mp4"]:
                            result.write_videofile(
                                str(stem.with_suffix(".mp4")), fps=24, codec="libx264",
                                audio=bool(source.audio), logger=None,
                            )
                        written += 1
                    finally:
                        result.close()
        finally:
            source.close()
        self.stdout.write(self.style.SUCCESS(f"{written} muestras en {out}"))

    @staticmethod
    def _source(options):
        if not options["source"]:
            return synthetic_clip(options["seconds"])
        from moviepy import VideoFileClip

        path = Path(options["source"])
        if not path.is_file():
            raise CommandError(f"No existe el video {path}.")
        clip = VideoFileClip(str(path))
        end = min(clip.duration, options["start"] + options["seconds"])
        if end <= options["start"]:
            clip.close()
            raise CommandError("--start queda después del final del video.")
        return clip.subclipped(options["start"], end)
