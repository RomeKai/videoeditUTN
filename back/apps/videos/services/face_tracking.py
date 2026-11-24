"""
Multi-face tracking (CORE-01, Face Tracking v2).

The detector sees every face of each sampled frame; this module links those
detections over time into tracks with stable ids, so a layout can follow one
person (or, with speaker detection, switch between them) instead of jumping to
whichever face scored highest in each frame.

Pure Python on top of the detections: no model, no video, fully testable.
"""

import math
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence

from apps.videos.services.tracking_utils import (
    EMASmoothing,
    TrackingPath,
    TrackingPoint,
    interpolate_gaps,
)


@dataclass(frozen=True)
class Detection:
    """One face in one sampled frame: top-left corner and size, in pixels."""

    t: float
    x: float
    y: float
    w: float
    h: float
    score: float = 1.0

    @property
    def center(self):
        return self.x + self.w / 2, self.y + self.h / 2

    @property
    def area(self):
        return max(self.w, 0) * max(self.h, 0)

    def iou(self, other: "Detection") -> float:
        ix = max(0.0, min(self.x + self.w, other.x + other.w) - max(self.x, other.x))
        iy = max(0.0, min(self.y + self.h, other.y + other.h) - max(self.y, other.y))
        inter = ix * iy
        union = self.area + other.area - inter
        return inter / union if union > 0 else 0.0


@dataclass
class FaceTrack:
    id: int
    detections: List[Detection] = field(default_factory=list)
    missed: int = 0

    @property
    def last(self) -> Detection:
        return self.detections[-1]

    @property
    def start(self) -> float:
        return self.detections[0].t

    @property
    def end(self) -> float:
        return self.detections[-1].t

    @property
    def mean_area(self) -> float:
        return sum(d.area for d in self.detections) / len(self.detections)

    def importance(self) -> float:
        """How much this face matters for framing: present for long and large."""
        return len(self.detections) * math.sqrt(self.mean_area)

    def points(self) -> List[TrackingPoint]:
        return [
            TrackingPoint(d.t, int(d.center[0]), int(d.center[1]), int(d.w), int(d.h))
            for d in self.detections
        ]

    def path(self, sample_times: Sequence[float], smoothing=None) -> TrackingPath:
        """Smoothed path over the samples this face spans; gaps are interpolated."""
        filled = interpolate_gaps(self.points(), list(sample_times))
        return TrackingPath(filled, smoothing_strategy=smoothing or EMASmoothing())


class IoUTracker:
    """
    Greedy association of detections to tracks, best match first.

    A detection continues a track when their boxes overlap (IoU) or, for fast
    movement between samples, when its center moved less than
    ``max_center_shift`` face sizes and its size changed less than 2x. A track
    survives ``max_missed`` samples without a detection (a turned head, a
    hand in front); after that the face gets a new id. Tracks with fewer than
    ``min_hits`` detections are noise and are not reported.
    """

    def __init__(self, iou_threshold: float = 0.2, max_center_shift: float = 0.75,
                 max_missed: int = 4, min_hits: int = 2):
        self.iou_threshold = iou_threshold
        self.max_center_shift = max_center_shift
        self.max_missed = max_missed
        self.min_hits = min_hits
        self._tracks: List[FaceTrack] = []
        self._next_id = 0
        self.sample_times: List[float] = []

    def _affinity(self, last: Detection, det: Detection) -> float:
        iou = last.iou(det)
        if iou >= self.iou_threshold:
            return 1.0 + iou
        size = max(last.w, last.h, det.w, det.h)
        if size <= 0 or not 0.5 <= (det.area / last.area if last.area else 0) <= 2.0:
            return 0.0
        (lx, ly), (dx, dy) = last.center, det.center
        shift = math.hypot(dx - lx, dy - ly) / size
        if shift > self.max_center_shift:
            return 0.0
        return 1.0 - shift / self.max_center_shift

    def update(self, t: float, detections: Iterable[Detection]) -> None:
        detections = list(detections)
        self.sample_times.append(t)
        active = [track for track in self._tracks if track.missed <= self.max_missed]
        pairs = []
        for ti, track in enumerate(active):
            for di, det in enumerate(detections):
                score = self._affinity(track.last, det)
                if score > 0:
                    pairs.append((score, ti, di))
        pairs.sort(key=lambda pair: (-pair[0], pair[1], pair[2]))

        used_tracks, used_dets = set(), set()
        for _score, ti, di in pairs:
            if ti in used_tracks or di in used_dets:
                continue
            used_tracks.add(ti)
            used_dets.add(di)
            active[ti].detections.append(detections[di])
            active[ti].missed = 0
        for ti, track in enumerate(active):
            if ti not in used_tracks:
                track.missed += 1
        for di, det in enumerate(detections):
            if di not in used_dets:
                self._tracks.append(FaceTrack(id=self._next_id, detections=[det]))
                self._next_id += 1

    def tracks(self) -> List[FaceTrack]:
        """Confirmed tracks, by id (the order faces first appeared)."""
        return [track for track in self._tracks if len(track.detections) >= self.min_hits]


@dataclass
class FaceTracks:
    tracks: List[FaceTrack]
    sample_times: List[float]
    frame_size: tuple

    def primary(self) -> Optional[FaceTrack]:
        """The face to frame when only one fits: the most present and largest."""
        if not self.tracks:
            return None
        return max(self.tracks, key=lambda track: (track.importance(), -track.id))

    def by_id(self) -> Dict[int, FaceTrack]:
        return {track.id: track for track in self.tracks}

    def path_for(self, track: FaceTrack, smoothing=None) -> TrackingPath:
        return track.path(self.sample_times, smoothing)

    def framing_path(self, smoothing=None) -> TrackingPath:
        """
        One camera path for the whole clip: at each sample, the most important
        face on screen (its span, short gaps included). The primary face wins
        whenever it is there; a cut to another shot or person moves the camera
        there instead of keeping an empty frame. Each stretch on one face is
        smoothed on its own and the change between faces is a cut, not a pan.
        """
        ranked = sorted(self.tracks, key=lambda track: (-track.importance(), track.id))
        spans = {
            track.id: {p.t: p for p in interpolate_gaps(track.points(), self.sample_times)}
            for track in ranked
        }
        chosen = []  # (track id, point) per covered sample
        for t in self.sample_times:
            for track in ranked:
                point = spans[track.id].get(t)
                if point is not None:
                    chosen.append((track.id, point))
                    break
        if not chosen:
            return TrackingPath([])

        smoothing = smoothing or EMASmoothing()
        stretches, current = [], [chosen[0]]
        for item in chosen[1:]:
            if item[0] == current[-1][0]:
                current.append(item)
            else:
                stretches.append(current)
                current = [item]
        stretches.append(current)

        points: List[TrackingPoint] = []
        for stretch in stretches:
            smoothed = smoothing.smooth([point for _, point in stretch])
            if points:
                # Hold the previous face until just before this one: a cut.
                last = points[-1]
                cut_t = smoothed[0].t - 1e-3
                if cut_t > last.t:
                    points.append(TrackingPoint(cut_t, last.x, last.y, last.w, last.h))
            points.extend(smoothed)
        return TrackingPath(points)


# Faces move faster in dynamic edits than in a podcast; sampling more often
# costs detector time per clip (CORE-01: adaptive sampling by editing style).
SAMPLING_FPS = {
    "dynamic": 3.0,
    "hormozi": 3.0,
    "vlog": 2.0,
    "custom": 2.0,
    "minimalist": 1.5,
}
DEFAULT_SAMPLING_FPS = 2.0


def sampling_fps(editing_style: Optional[str]) -> float:
    return SAMPLING_FPS.get(str(editing_style or "").lower(), DEFAULT_SAMPLING_FPS)
