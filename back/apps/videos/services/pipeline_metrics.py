"""
Pipeline metrics (INFRA-02): the data behind the operations dashboard.

Computed from the database over the projects created in a time window:

- ``stages``: per pipeline stage (and the web proxy), how many runs and the
  p50 / p95 / max duration in seconds, from ``metadata['stage_durations']``.
- ``projects``: how many projects there are per status.
- ``failures``: failed projects per static ``pipeline_error_code``.
- ``failure_rate``: failed / finished (completed, awaiting approval or failed).

Only counts, durations and static codes: no title, transcript, prompt or user.
Served by ``manage.py pipeline_metrics`` and ``GET /api/v1/ops/pipeline-metrics/``.
"""

from datetime import timedelta
from typing import Any, Dict, Iterable, List, Optional

from django.db.models import Count
from django.utils import timezone

from apps.videos.models import VideoProject

DEFAULT_WINDOW_HOURS = 24
# Keeps the dashboard query bounded however busy the window was.
MAX_PROJECTS = 5000


def _percentile(sorted_values: List[float], fraction: float) -> Optional[float]:
    """Nearest-rank percentile of an already sorted list (None when empty)."""
    if not sorted_values:
        return None
    # ceil(p * n) in integer math: 0.95 * 20 in floats is 19.000000000000004.
    percent = round(fraction * 100)
    rank = max(1, -(-percent * len(sorted_values) // 100))
    return round(sorted_values[min(rank, len(sorted_values)) - 1], 3)


def _stage_summary(durations: Iterable[float]) -> Dict[str, Any]:
    values = sorted(float(v) for v in durations)
    return {
        "count": len(values),
        "p50_s": _percentile(values, 0.50),
        "p95_s": _percentile(values, 0.95),
        "max_s": round(values[-1], 3) if values else None,
    }


def collect(hours: int = DEFAULT_WINDOW_HOURS, now=None) -> Dict[str, Any]:
    """Metrics of the projects created in the last ``hours`` hours."""
    now = now or timezone.now()
    since = now - timedelta(hours=hours)
    window = VideoProject.objects.filter(created_at__gte=since)

    per_stage: Dict[str, List[float]] = {}
    for metadata in window.order_by('-created_at').values_list('metadata', flat=True)[:MAX_PROJECTS]:
        for stage, seconds in ((metadata or {}).get('stage_durations') or {}).items():
            if isinstance(seconds, (int, float)) and seconds >= 0:
                per_stage.setdefault(stage, []).append(seconds)

    by_status = {
        row['status']: row['n'] for row in window.values('status').annotate(n=Count('id'))
    }
    failures = {
        row['pipeline_error_code'] or 'unknown': row['n']
        for row in window.filter(status=VideoProject.Status.FAILED)
        .values('pipeline_error_code').annotate(n=Count('id'))
    }
    finished = sum(
        by_status.get(s, 0)
        for s in (
            VideoProject.Status.COMPLETED,
            VideoProject.Status.AWAITING_APPROVAL,
            VideoProject.Status.FAILED,
        )
    )
    failed = by_status.get(VideoProject.Status.FAILED, 0)

    return {
        "window": {"since": since.isoformat(), "until": now.isoformat(), "hours": hours},
        "projects": by_status,
        "failure_rate": round(failed / finished, 4) if finished else None,
        "failures": dict(sorted(failures.items(), key=lambda item: -item[1])),
        "stages": {stage: _stage_summary(values) for stage, values in sorted(per_stage.items())},
    }
