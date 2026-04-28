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
