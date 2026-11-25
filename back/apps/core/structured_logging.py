"""
Structured logging (INFRA-02).

``JsonFormatter`` writes one JSON object per line, ready for a log platform
(Loki, CloudWatch, Datadog...) to index and query without regex parsing:

    {"ts": "2026-11-24T18:30:00.123Z", "level": "INFO", "logger": "apps.videos.tasks.ingestion",
     "event": "pipeline.stage_completed", "message": "pipeline.stage_completed project_id=... ",
     "service": "worker", "task_name": "apps.videos.tasks.process_initial_ingestion",
     "task_id": "...", "project_id": "...", "stage": "transcribed", "duration_s": 41.2}

- ``event`` is the ``domain.event`` prefix every log message of the project
  already starts with, so dashboards and alerts group by it.
- Every ``extra={...}`` field becomes a top-level key (``project_id``, ``stage``,
  ``duration_s``...). Values that are not JSON-native are rendered with ``str``.
- ``CeleryTaskContextFilter`` adds ``task_name`` and ``task_id`` when the record
  is emitted inside a Celery task, so every line of a run can be correlated.

Privacy (PR #56): the formatter only serializes what the code already logs;
it adds no request data. Exceptions are rendered as their class and stack
trace (code locations), never with local variables.
"""

import json
import logging
import re
from datetime import datetime, timezone

# Attributes every LogRecord has; anything else on a record came from ``extra``.
_RESERVED = frozenset(vars(logging.LogRecord("", 0, "", 0, "", (), None))) | {
    "message", "asctime", "taskName",
}
_EVENT = re.compile(r"^([a-z0-9_]+(?:\.[a-z0-9_]+)+)\b")


def _event_of(message: str):
    match = _EVENT.match(message or "")
    return match.group(1) if match else None


class JsonFormatter(logging.Formatter):
    """One JSON object per log record (see the module docstring)."""

    def __init__(self, *args, service: str = "", **kwargs):
        super().__init__(*args, **kwargs)
        self.service = service

    def format(self, record: logging.LogRecord) -> str:
        message = record.getMessage()
        payload = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "event": _event_of(message),
            "message": message,
        }
        if self.service:
            payload["service"] = self.service
        for key, value in vars(record).items():
            if key not in _RESERVED and not key.startswith("_") and key not in payload:
                payload[key] = value
        if record.exc_info and record.exc_info[0] is not None:
            payload["exc_type"] = record.exc_info[0].__name__
            payload["stack"] = self.formatException(record.exc_info)
        elif record.stack_info:
            payload["stack"] = self.formatStack(record.stack_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


class CeleryTaskContextFilter(logging.Filter):
    """Adds ``task_name`` / ``task_id`` of the running Celery task, if any."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            from celery import current_task
        except ImportError:  # pragma: no cover - celery is a hard dependency
            return True
        task = current_task
        request = getattr(task, "request", None) if task else None
        task_id = getattr(request, "id", None) if request else None
        if task_id and not hasattr(record, "task_id"):
            record.task_id = task_id
            record.task_name = getattr(task, "name", None)
        return True
