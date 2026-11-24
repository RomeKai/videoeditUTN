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

# What libmagic reports for the containers the serializer accepts (.mp4 .mov
# .avi), mapped to the ffmpeg demuxer that must read them. 3GPP is the same
# ISO family (phones).
_DEMUXER_BY_MIME = {
    "video/mp4": "mov", "video/x-m4v": "mov", "video/quicktime": "mov",
    "video/3gpp": "mov", "video/3gpp2": "mov",
    "video/x-msvideo": "avi", "video/avi": "avi", "video/msvideo": "avi",
}
ALLOWED_MIME_TYPES = frozenset(_DEMUXER_BY_MIME)
# libmagic calls some real camera files octet-stream (Canon "CAEP" brand, MOVs
# that start with a free/skip/wide box): an ISO/QuickTime box at offset 4 is
# enough, ffprobe with the mov demuxer forced decides the rest.
_ISO_BOX_TYPES = (b"ftyp", b"moov", b"mdat", b"free", b"skip", b"wide", b"pnot")
_HEAD_BYTES = 8192
PROBE_TIMEOUT_SECONDS = 60
# ffmpeg picks a demuxer by content. A DASH manifest inside the file (it only
# needs "<MPD" and a "dash:profile" anywhere in the probe window) makes ffmpeg
# open the URLs it lists, from the server. Such files are rejected here, and
# ffprobe below is forced to the container's own demuxer.
_PROBE_WINDOW_BYTES = 5 * 1024 * 1024

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


def _sniff(uploaded_file):
    """(mime, demuxer) from the file's own bytes; demuxer None means not a video we take."""
    import magic

    uploaded_file.seek(0)
    head = uploaded_file.read(_HEAD_BYTES)
    uploaded_file.seek(0)
    if not head:
        return "", None
    mime = magic.from_buffer(head, mime=True)
    if mime in _DEMUXER_BY_MIME:
        return mime, _DEMUXER_BY_MIME[mime]
    if mime == "application/octet-stream" and head[4:8] in _ISO_BOX_TYPES:
        return "video/quicktime", "mov"
    return mime, None


def _has_streaming_manifest(uploaded_file):
    """A DASH/HLS/concat playlist hidden in the probe window of a 'video'."""
    uploaded_file.seek(0)
    window = uploaded_file.read(_PROBE_WINDOW_BYTES).lower()
    uploaded_file.seek(0)
    return (
        (b"<mpd" in window and b"dash:profile" in window)
        or window.startswith((b"#extm3u", b"ffconcat"))
    )


def _probe(path, demuxer):
    """
    ffprobe as JSON with the container's demuxer forced and local files only;
    any failure is ``unreadable`` (never its output).
    """
    exe = shutil.which("ffprobe") or "ffprobe"
    try:
        result = subprocess.run(
            [
                exe, "-v", "error", "-protocol_whitelist", "file", "-f", demuxer,
                "-print_format", "json", "-show_format", "-show_streams", path,
            ],
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


def _size(stream):
    try:
        return int(stream.get("width") or 0), int(stream.get("height") or 0)
    except (TypeError, ValueError):
        return 0, 0


def _describe(path, mime, demuxer):
    data = _probe(path, demuxer)
    videos = [
        s for s in data.get("streams") or []
        if s.get("codec_type") == "video" and (s.get("disposition") or {}).get("attached_pic") != 1
    ]
    # The main picture, not a small preview track that may come first.
    stream = max(videos, key=lambda s: _size(s)[0] * _size(s)[1], default=None)
    if stream is None:
        raise InvalidUploadError("no_video_stream")
    width, height = _size(stream)
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
    mime, demuxer = _sniff(uploaded_file)
    if demuxer is None:
        logger.info("upload.rejected code=not_a_video mime=%s", mime[:60])
        raise InvalidUploadError("not_a_video")
    if _has_streaming_manifest(uploaded_file):
        logger.warning("upload.rejected code=streaming_manifest")
        raise InvalidUploadError("not_a_video")

    try:
        if hasattr(uploaded_file, "temporary_file_path"):
            return _describe(uploaded_file.temporary_file_path(), mime, demuxer)
        suffix = os.path.splitext(getattr(uploaded_file, "name", "") or "")[1][:8]
        handle, path = tempfile.mkstemp(suffix=suffix)
        try:
            with os.fdopen(handle, "wb") as out:
                uploaded_file.seek(0)
                for chunk in uploaded_file.chunks():
                    out.write(chunk)
            return _describe(path, mime, demuxer)
        finally:
            os.unlink(path)
            uploaded_file.seek(0)
    except InvalidUploadError as exc:
        logger.info("upload.rejected code=%s", exc.code)
        raise
