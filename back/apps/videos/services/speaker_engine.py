"""
Active speaker detection (CORE-02, #11) without pyannote.

Decision: pyannote needs PyTorch, which AICORE-10 (#44) removes from the
image, and its models are gated behind a Hugging Face token. Two light,
local signals are enough to choose which face to frame:

- Speech: when the clip's audio rises above its own noise floor (RMS).
- Mouth movement: for each tracked face (CORE-01), how much the mouth area
  changes between two close frames, minus how much the upper face changes
  (so moving the whole head does not count as talking).

While someone speaks, the face whose mouth moves clearly more than the
current speaker's takes the camera; short flips are merged, so the camera
does not cut on every syllable. The result is a timeline of
``(start, end, face_id)``.
"""

import logging
import math
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

Segment = Tuple[float, float, Optional[int]]


@dataclass
class SpeechActivity:
    """Whether there is speech in each audio frame of ``hop`` seconds."""

    hop: float
    speaking: np.ndarray

    def fraction(self, start: float, end: float) -> float:
        lo, hi = int(start / self.hop), int(math.ceil(end / self.hop))
        window = self.speaking[lo:hi]
        return float(window.mean()) if window.size else 0.0


def speech_activity(clip, hop: float = 0.05, sample_rate: int = 16000) -> Optional[SpeechActivity]:
    """RMS voice activity relative to the clip's own noise floor; None without audio."""
    audio = getattr(clip, "audio", None)
    if audio is None:
        return None
    try:
        # One-second reads: MoviePy's file reader returns wrong samples for
        # longer reads at a resampled rate (it repeated one sample for the
        # whole clip), so the default buffer is not safe here.
        samples = np.asarray(
            audio.to_soundarray(fps=sample_rate, buffersize=int(sample_rate)), dtype=np.float32,
        )
    except Exception as exc:
        logger.warning("speaker.audio_failed error=%s", type(exc).__name__)
        return None
    if samples.ndim > 1:
        samples = samples.mean(axis=1)
    frame = max(1, int(hop * sample_rate))
    count = len(samples) // frame
    if count == 0:
        return None
    rms = np.sqrt(np.mean(samples[: count * frame].reshape(count, frame) ** 2, axis=1))
    floor, peak = np.percentile(rms, 20), np.percentile(rms, 95)
    if peak <= 1e-4 or peak - floor <= 1e-4:
        # Silence, or a level that never changes (music bed, tone): no evidence.
        return SpeechActivity(hop, np.zeros(count, dtype=bool) if peak <= 1e-4 else np.ones(count, dtype=bool))
    return SpeechActivity(hop, rms > floor + 0.25 * (peak - floor))


def _region(gray, x1, y1, w, h, top, bottom, left=0.25, right=0.75):
    rows = slice(max(0, int(y1 + top * h)), max(0, int(y1 + bottom * h)))
    cols = slice(max(0, int(x1 + left * w)), max(0, int(x1 + right * w)))
    patch = gray[rows, cols]
    return patch if patch.size else None


def mouth_activity(frame_a, frame_b, box) -> float:
    """
    Mouth change between two frames for the face ``box`` = (center x, center y,
    width, height), net of the change in the upper face (head movement, light).
    Only the face's own pixels are converted (a whole 1080p frame per face and
    sample made this the slowest step).
    """
    cx, cy, w, h = box
    if w <= 0 or h <= 0:
        return 0.0
    height, width = frame_a.shape[:2]
    x1, y1 = max(0, int(cx - w / 2)), max(0, int(cy - h / 2))
    x2, y2 = min(width, int(cx + w / 2)), min(height, int(cy + h / 2))
    if x2 <= x1 or y2 <= y1:
        return 0.0
    # Offsets of the clamped crop inside the full face box.
    ox, oy = x1 - (cx - w / 2), y1 - (cy - h / 2)
    diff = np.abs(
        np.asarray(frame_a[y1:y2, x1:x2], dtype=np.float32).mean(axis=2)
        - np.asarray(frame_b[y1:y2, x1:x2], dtype=np.float32).mean(axis=2)
    )
    mouth = _region(diff, -ox, -oy, w, h, 0.62, 0.95)
    upper = _region(diff, -ox, -oy, w, h, 0.10, 0.45)
    if mouth is None or upper is None:
        return 0.0
    return max(0.0, float(mouth.mean()) - float(upper.mean()))


def _box_at(track, t):
    """The track's box at ``t`` while it is on screen (its span), else None."""
    if not track.start <= t <= track.end:
        return None
    times = [d.t for d in track.detections]
    cxs = [d.center[0] for d in track.detections]
    cys = [d.center[1] for d in track.detections]
    return (
        float(np.interp(t, times, cxs)), float(np.interp(t, times, cys)),
        float(np.interp(t, times, [d.w for d in track.detections])),
        float(np.interp(t, times, [d.h for d in track.detections])),
    )


def speaker_at(timeline: List[Segment]) -> Callable[[float], Optional[int]]:
    """``t -> face id`` for a timeline (None outside it)."""
    def lookup(t):
        for start, end, face_id in timeline:
            if start <= t < end:
                return face_id
        return timeline[-1][2] if timeline and t >= timeline[-1][1] else None
    return lookup


class SpeakerTrackingEngine:
    """
    Builds the speaker timeline of a clip from its tracked faces (FaceTracks).

    - ``sample_rate``: mouth measurements per second; each one compares the
      frame at ``t`` with the one ``pair_gap`` seconds later.
    - ``window``: decisions are taken per window of this many seconds, only
      when at least ``min_speech`` of it has speech.
    - ``switch_margin``: a new face takes the camera when its mouth moves this
      many times more than the current speaker's.
    - ``min_segment``: shorter turns are merged into their neighbour.
    """

    def __init__(self, sample_rate: float = 4.0, pair_gap: float = 0.1, window: float = 1.0,
                 switch_margin: float = 1.5, min_activity: float = 1.0, min_speech: float = 0.3,
                 min_segment: float = 1.5):
        self.sample_rate = sample_rate
        self.pair_gap = pair_gap
        self.window = window
        self.switch_margin = switch_margin
        self.min_activity = min_activity
        self.min_speech = min_speech
        self.min_segment = min_segment

    # -- measurement ---------------------------------------------------------

    def activity(self, clip, faces, speech: Optional[SpeechActivity] = None) -> Dict[int, List[Tuple[float, float]]]:
        """
        Mouth activity samples ``(t, score)`` per face id. Samples without
        speech around them are skipped: they could never take the camera.
        """
        scores: Dict[int, List[Tuple[float, float]]] = {track.id: [] for track in faces.tracks}
        last_t = max(0.0, clip.duration - self.pair_gap - 1e-3)
        failures = 0
        for t in np.arange(0, last_t, 1.0 / self.sample_rate):
            t = float(t)
            if speech is not None and speech.fraction(t, t + self.pair_gap + 0.05) == 0:
                continue
            boxes = {track.id: _box_at(track, t) for track in faces.tracks}
            boxes = {face_id: box for face_id, box in boxes.items() if box is not None}
            if not boxes:
                continue
            try:
                frame_a, frame_b = clip.get_frame(t), clip.get_frame(t + self.pair_gap)
            except Exception as exc:
                failures += 1
                if failures <= 3:
                    logger.warning("speaker.frame_failed t=%.2f error=%s", t, type(exc).__name__)
                continue
            for face_id, box in boxes.items():
                scores[face_id].append((t, mouth_activity(frame_a, frame_b, box)))
        if failures > 3:
            logger.warning("speaker.frames_failed count=%d", failures)
        return scores

    # -- decision ------------------------------------------------------------

    def timeline(self, clip, faces, speech: Optional[SpeechActivity] = None,
                 activity: Optional[Dict[int, List[Tuple[float, float]]]] = None) -> List[Segment]:
        duration = float(clip.duration)
        if not faces.tracks:
            return []
        primary = faces.primary()
        if len(faces.tracks) == 1:
            return [(0.0, duration, primary.id)]
        if speech is None:
            speech = speech_activity(clip)
        if speech is not None and not speech.speaking.any():
            # Nobody talks in the clip: no evidence to move the camera.
            return [(0.0, duration, primary.id)]
        if activity is None:
            activity = self.activity(clip, faces, speech)
        labels = self._label_windows(duration, faces, speech, activity, primary.id)
        timeline = self._merge_short(self._merge_same(labels))
        logger.info("speaker.timeline faces=%d segments=%d", len(faces.tracks), len(timeline))
        return timeline

    def _label_windows(self, duration, faces, speech, activity, start_id):
        current = start_id
        labels = []
        windows = max(1, int(math.ceil(duration / self.window)))
        for k in range(windows):
            start, end = k * self.window, min(duration, (k + 1) * self.window)
            in_window = {
                face_id: [score for t, score in samples if start <= t < end]
                for face_id, samples in activity.items()
            }
            scores = {face_id: float(np.mean(v)) for face_id, v in in_window.items() if v}
            talking = speech is None or speech.fraction(start, end) >= self.min_speech
            if scores:
                leader = max(scores, key=lambda face_id: (scores[face_id], -face_id))
                current_visible = current in scores
                if not current_visible:
                    # The current speaker left the frame: whoever is on screen and
                    # clearly moving their mouth takes it, else the most important face.
                    current = (
                        leader if talking and scores[leader] >= self.min_activity
                        else self._most_important(faces, scores)
                    )
                elif talking and leader != current and scores[leader] >= self.min_activity \
                        and scores[leader] >= self.switch_margin * scores[current]:
                    current = leader
            labels.append((start, end, current))
        return labels

    @staticmethod
    def _most_important(faces, visible):
        candidates = [track for track in faces.tracks if track.id in visible]
        return max(candidates, key=lambda track: (track.importance(), -track.id)).id

    @staticmethod
    def _merge_same(labels):
        merged = []
        for start, end, face_id in labels:
            if merged and merged[-1][2] == face_id:
                merged[-1] = (merged[-1][0], end, face_id)
            else:
                merged.append((start, end, face_id))
        return merged

    def _merge_short(self, segments):
        """
        A turn shorter than ``min_segment`` is absorbed by its longer neighbour.
        Turns between two others go first and the shortest first, so two short
        turns at the edge never add up to one that passes the minimum.
        """
        segments = list(segments)
        while len(segments) > 1:
            short = [i for i, (s, e, _) in enumerate(segments) if e - s < self.min_segment]
            if not short:
                break
            middle = [i for i in short if 0 < i < len(segments) - 1]
            i = min(middle or short, key=lambda k: (segments[k][1] - segments[k][0], k))
            if 0 < i < len(segments) - 1:
                before, after = segments[i - 1], segments[i + 1]
                neighbour = i - 1 if before[1] - before[0] >= after[1] - after[0] else i + 1
            else:
                neighbour = 1 if i == 0 else i - 1
            start, end, _ = segments[i]
            n_start, n_end, n_id = segments[neighbour]
            segments[neighbour] = (min(start, n_start), max(end, n_end), n_id)
            del segments[i]
            segments = self._merge_same(segments)
        return segments

    # -- previous API ----------------------------------------------------------

    def detect_active_speaker_segment(self, clip, interval=1.0, faces=None):
        """``[(start, end, face_id)]`` for the clip (tracks the faces if not given)."""
        if faces is None:
            from apps.videos.services.face_tracker import FaceTracker

            faces = FaceTracker().track_faces(clip)
        self.window = interval
        return self.timeline(clip, faces)

    def close(self):
        """Nothing to release: the face detector is shared by the worker (CORE-01)."""
