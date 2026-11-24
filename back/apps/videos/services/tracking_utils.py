
import numpy as np
from abc import ABC, abstractmethod
from typing import List, Tuple, Optional

class TrackingPoint:
    def __init__(self, t: float, x: int, y: int, w: int, h: int):
        self.t = t
        self.x = x
        self.y = y
        self.w = w
        self.h = h

class SmoothingStrategy(ABC):
    @abstractmethod
    def smooth(self, points: List[TrackingPoint]) -> List[TrackingPoint]:
        pass

class MovingAverageSmoothing(SmoothingStrategy):
    def __init__(self, window_size: int = 5):
        self.window_size = window_size

    def smooth(self, points: List[TrackingPoint]) -> List[TrackingPoint]:
        if len(points) <= self.window_size:
            return points
        smoothed = []
        for i in range(len(points)):
            start = max(0, i - self.window_size // 2)
            end = min(len(points), i + self.window_size // 2 + 1)
            window = points[start:end]
            avg_x = sum(p.x for p in window) / len(window)
            avg_y = sum(p.y for p in window) / len(window)
            avg_w = sum(p.w for p in window) / len(window)
            avg_h = sum(p.h for p in window) / len(window)
            smoothed.append(TrackingPoint(points[i].t, int(avg_x), int(avg_y), int(avg_w), int(avg_h)))
        return smoothed

class EMASmoothing(SmoothingStrategy):
    """
    Exponential moving average (CORE-01). ``alpha`` in (0, 1]: lower is smoother.
    ``bidirectional`` runs it forward and then backward, which cancels the lag a
    single pass adds (the camera would trail the face). The render has the
    whole path up front, so the zero-lag version is the default.
    """

    def __init__(self, alpha: float = 0.35, bidirectional: bool = True):
        if not 0 < alpha <= 1:
            raise ValueError("alpha must be in (0, 1]")
        self.alpha = alpha
        self.bidirectional = bidirectional

    def _pass(self, values: List[float]) -> List[float]:
        out = []
        current = None
        for value in values:
            current = value if current is None else self.alpha * value + (1 - self.alpha) * current
            out.append(current)
        return out

    def _smooth_series(self, values: List[float]) -> List[float]:
        forward = self._pass(values)
        if not self.bidirectional:
            return forward
        return list(reversed(self._pass(list(reversed(forward)))))

    def smooth(self, points: List[TrackingPoint]) -> List[TrackingPoint]:
        if len(points) < 3:
            return points
        series = [self._smooth_series([getattr(p, attr) for p in points]) for attr in ("x", "y", "w", "h")]
        return [
            TrackingPoint(p.t, int(round(x)), int(round(y)), int(round(w)), int(round(h)))
            for p, x, y, w, h in zip(points, *series)
        ]


def interpolate_gaps(points: List[TrackingPoint], times: List[float]) -> List[TrackingPoint]:
    """
    One point per sampled time between the first and the last detection: the
    missing ones are linear between their neighbours (CORE-01), instead of
    holding the last position and then jumping.
    """
    if len(points) < 2:
        return list(points)
    known_t = [p.t for p in points]
    start, end = known_t[0], known_t[-1]
    grid = sorted({t for t in times if start <= t <= end} | set(known_t))
    columns = {
        attr: np.interp(grid, known_t, [getattr(p, attr) for p in points])
        for attr in ("x", "y", "w", "h")
    }
    return [
        TrackingPoint(t, int(round(columns["x"][i])), int(round(columns["y"][i])),
                      int(round(columns["w"][i])), int(round(columns["h"][i])))
        for i, t in enumerate(grid)
    ]


class TrackingPath:
    def __init__(self, points: List[TrackingPoint], smoothing_strategy: Optional[SmoothingStrategy] = None):
        self.points = smoothing_strategy.smooth(points) if smoothing_strategy else points
        self.times = [p.t for p in self.points]
        self.xs = [p.x for p in self.points]
        self.ys = [p.y for p in self.points]
        self.ws = [p.w for p in self.points]
        self.hs = [p.h for p in self.points]

    def get_at(self, t: float) -> Tuple[int, int, int, int]:
        if not self.points: return (0, 0, 0, 0)
        interp_x = int(np.interp(t, self.times, self.xs))
        interp_y = int(np.interp(t, self.times, self.ys))
        interp_w = int(np.interp(t, self.times, self.ws))
        interp_h = int(np.interp(t, self.times, self.hs))
        return (interp_x, interp_y, interp_w, interp_h)

    def get_average_size(self) -> Tuple[int, int]:
        if not self.hs: return (0, 0)
        return (int(np.median(self.ws)), int(np.median(self.hs)))
