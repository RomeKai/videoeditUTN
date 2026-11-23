"""
Upload validation (CORE-05, #12): the uploaded file must really be a video the
pipeline can process, checked before the project is saved or charged.

- The content type comes from the file's bytes (libmagic), not from the name
  or the header the client sends.
- ffprobe must find a video stream, a duration and a size within the system
  limits. The plan limits (duration, resolution of the render) stay in
  PricingEngine; these are the limits of the pipeline itself.

Errors carry a static code and a fixed message for the user; neither the file
name nor ffprobe's output reaches the logs or the response.
"""

import json
import logging
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass

from django.conf import settings

logger = logging.getLogger(__name__)

# What libmagic reports for the extensions the serializer accepts (.mp4 .mov .avi).
ALLOWED_MIME_TYPES = frozenset({
    "video/mp4", "video/x-m4v", "video/quicktime",
    "video/x-msvideo", "video/avi", "video/msvideo",
})
_HEAD_BYTES = 8192
PROBE_TIMEOUT_SECONDS = 60

MESSAGES = {
    "not_a_video": "El archivo no es un video válido. Formatos admitidos: MP4, MOV y AVI.",
    "unreadable": "No pudimos leer el video. Probá exportarlo de nuevo en MP4 (H.264).",
    "no_video_stream": "El archivo no tiene una pista de video.",
    "no_duration": "No pudimos determinar la duración del video.",
    "too_long": "El video supera la duración máxima que podemos procesar ({limit}).",
    "too_large": "La resolución del video supera el máximo admitido ({limit} px de lado).",
    "too_small": "La resolución del video es demasiado baja (mínimo {limit} px de lado).",
}


class InvalidUploadError(Exception):
    def __init__(self, code, limit=None):
        self.code = code
        self.message = MESSAGES[code].format(limit=limit)
        super().__init__(code)


@dataclass(frozen=True)
class MediaInfo:
    duration_s: float
    width: int
    height: int
    mime: str

    def as_metadata(self):
        return {
            "duration_s": round(self.duration_s, 3), "width": self.width,
            "height": self.height, "mime": self.mime,
        }


def _limit(name, default):
    return getattr(settings, name, default)


def _sniff_mime(uploaded_file):
    import magic

    uploaded_file.seek(0)
    head = uploaded_file.read(_HEAD_BYTES)
    uploaded_file.seek(0)
    return magic.from_buffer(head, mime=True) if head else ""


def _probe(path):
    """ffprobe as JSON; any failure is ``unreadable`` (never its output)."""
    exe = shutil.which("ffprobe") or "ffprobe"
    try:
        result = subprocess.run(
            [exe, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path],
            capture_output=True, text=True, check=True, timeout=PROBE_TIMEOUT_SECONDS,
        )
        return json.loads(result.stdout or "{}")
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        logger.warning("upload.probe_failed error=%s", type(exc).__name__)
        raise InvalidUploadError("unreadable") from None


def _duration(data, stream):
    for raw in ((data.get("format") or {}).get("duration"), stream.get("duration")):
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        if value > 0:
            return value
    raise InvalidUploadError("no_duration")


def _describe(path, mime):
    data = _probe(path)
    stream = next(
        (s for s in data.get("streams") or [] if s.get("codec_type") == "video"
         and (s.get("disposition") or {}).get("attached_pic") != 1),
        None,
    )
    if stream is None:
        raise InvalidUploadError("no_video_stream")
    try:
        width, height = int(stream.get("width") or 0), int(stream.get("height") or 0)
    except (TypeError, ValueError):
        width = height = 0
    if width <= 0 or height <= 0:
        raise InvalidUploadError("no_video_stream")

    duration = _duration(data, stream)
    max_duration = _limit("AI_UPLOAD_MAX_DURATION_SECONDS", 10800)
    if duration > max_duration:
        raise InvalidUploadError("too_long", limit=_format_duration(max_duration))
    max_side = _limit("AI_UPLOAD_MAX_LONG_SIDE_PX", 4096)
    if max(width, height) > max_side:
        raise InvalidUploadError("too_large", limit=max_side)
    min_side = _limit("AI_UPLOAD_MIN_SHORT_SIDE_PX", 240)
    if min(width, height) < min_side:
        raise InvalidUploadError("too_small", limit=min_side)
    return MediaInfo(duration_s=duration, width=width, height=height, mime=mime)


def _format_duration(seconds):
    if seconds < 60:
        return f"{int(seconds)} s"
    minutes = int(seconds) // 60
    return f"{minutes // 60} h {minutes % 60:02d} min" if minutes >= 60 else f"{minutes} min"


def validate_upload(uploaded_file):
    """
    MediaInfo of a Django UploadedFile, or InvalidUploadError. A file kept in
    memory by Django (small uploads) is written to a temporary file for ffprobe.
    """
    mime = _sniff_mime(uploaded_file)
    if mime not in ALLOWED_MIME_TYPES:
        logger.info("upload.rejected code=not_a_video mime=%s", mime[:60])
        raise InvalidUploadError("not_a_video")

    try:
        if hasattr(uploaded_file, "temporary_file_path"):
            return _describe(uploaded_file.temporary_file_path(), mime)
        suffix = os.path.splitext(getattr(uploaded_file, "name", "") or "")[1][:8]
        handle, path = tempfile.mkstemp(suffix=suffix)
        try:
            with os.fdopen(handle, "wb") as out:
                uploaded_file.seek(0)
                for chunk in uploaded_file.chunks():
                    out.write(chunk)
            return _describe(path, mime)
        finally:
            os.unlink(path)
            uploaded_file.seek(0)
    except InvalidUploadError as exc:
        logger.info("upload.rejected code=%s", exc.code)
        raise
