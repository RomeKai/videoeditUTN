"""
Alert when a Celery task fails N times in a row (INFRA-02).

Every final failure of a task (``task_failure``: retries do not count) bumps a
per-task counter in Redis; any success resets it. When the counter reaches
``TASK_FAILURE_ALERT_THRESHOLD`` (and every further multiple of it, so a task
that keeps failing alerts again instead of going quiet) the alert is raised:

- a CRITICAL log ``alert.task_failing`` with the task name, the count and the
  exception CLASS (a log platform turns it into a notification), and
- an email to ``ADMINS`` when it is configured.

Consecutive is per task name across all workers, which is what points to a
systemic problem (provider down, bad deploy) rather than one bad video. The
counter expires after a day without failures. Nothing here may break a task:
Redis or email errors are logged and swallowed. Eager tasks (tests, CI) are
ignored so they never touch Redis.
"""

import logging

from django.conf import settings

logger = logging.getLogger(__name__)

_KEY = "alerts:task_failures:{name}"
_COUNTER_TTL_SECONDS = 24 * 3600


def _redis():
    from apps.videos.services.ai.project_lock import get_redis_client

    return get_redis_client()


def _threshold() -> int:
    return int(getattr(settings, "TASK_FAILURE_ALERT_THRESHOLD", 3) or 0)


def _is_eager(task) -> bool:
    request = getattr(task, "request", None)
    return bool(getattr(request, "is_eager", False))


def record_task_success(task) -> None:
    """Resets the consecutive-failure counter of ``task``."""
    if _threshold() <= 0 or task is None or _is_eager(task):
        return
    try:
        _redis().delete(_KEY.format(name=task.name))
    except Exception as exc:
        logger.warning("alert.counter_reset_failed task=%s error=%s", task.name, type(exc).__name__)


def record_task_failure(task, exception=None) -> int:
    """
    Counts a final failure of ``task`` and raises the alert on every multiple of
    the threshold. Returns the consecutive count (0 when disabled or unknown).
    """
    threshold = _threshold()
    if threshold <= 0 or task is None or _is_eager(task):
        return 0
    key = _KEY.format(name=task.name)
    try:
        client = _redis()
        count = int(client.incr(key))
        client.expire(key, _COUNTER_TTL_SECONDS)
    except Exception as exc:
        logger.warning("alert.counter_failed task=%s error=%s", task.name, type(exc).__name__)
        return 0
    if count % threshold == 0:
        _raise_alert(task.name, count, type(exception).__name__ if exception else None)
    return count


def _raise_alert(task_name: str, count: int, error: str) -> None:
    logger.critical(
        "alert.task_failing task=%s consecutive_failures=%d error=%s",
        task_name, count, error,
        extra={"task": task_name, "consecutive_failures": count, "error": error},
    )
    if not getattr(settings, "ADMINS", None):
        return
    try:
        from django.core.mail import mail_admins

        mail_admins(
            subject=f"La tarea {task_name} falló {count} veces seguidas",
            message=(
                f"La tarea {task_name} falló {count} veces seguidas "
                f"(última excepción: {error}).\n"
                "Revisá los logs con event=alert.task_failing y los del worker."
            ),
            fail_silently=False,
        )
    except Exception as exc:
        logger.error("alert.email_failed task=%s error=%s", task_name, type(exc).__name__)
