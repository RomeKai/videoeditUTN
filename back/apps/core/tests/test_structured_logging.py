"""
INFRA-02 · Logs estructurados (JSON) y alertas por fallas seguidas de tareas.
"""
import json
import logging
import sys
import types
from unittest.mock import patch

import pytest
from django.core import mail

from apps.core import task_alerts
from apps.core.structured_logging import CeleryTaskContextFilter, JsonFormatter


def make_record(msg, *args, level=logging.INFO, extra=None, exc_info=None):
    logger = logging.getLogger("apps.test")
    return logger.makeRecord(
        "apps.test", level, __file__, 10, msg, args, exc_info, extra=extra,
    )


# ---------------------------------------------------------------------------
# JsonFormatter
# ---------------------------------------------------------------------------

def test_a_record_becomes_one_json_line_with_its_event_and_extras():
    record = make_record(
        "pipeline.stage_completed project_id=%s duration_s=%.3f", "p1", 4.2,
        extra={"project_id": "p1", "stage": "transcribed", "duration_s": 4.2},
    )

    line = JsonFormatter(service="worker").format(record)
    data = json.loads(line)

    assert "\n" not in line
    assert data["level"] == "INFO"
    assert data["logger"] == "apps.test"
    assert data["event"] == "pipeline.stage_completed"
    assert data["message"] == "pipeline.stage_completed project_id=p1 duration_s=4.200"
    assert data["service"] == "worker"
    assert data["project_id"] == "p1" and data["stage"] == "transcribed" and data["duration_s"] == 4.2
    assert data["ts"].endswith("Z")


def test_internal_log_record_attributes_are_not_serialized():
    data = json.loads(JsonFormatter().format(make_record("upload.rejected reason=%s", "size")))
    for internal in ("args", "msg", "pathname", "lineno", "levelno", "created", "process"):
        assert internal not in data
    assert "service" not in data


def test_messages_without_an_event_prefix_have_a_null_event():
    data = json.loads(JsonFormatter().format(make_record("Something happened")))
    assert data["event"] is None


def test_values_that_are_not_json_are_rendered_as_text():
    from decimal import Decimal
    from uuid import UUID

    record = make_record("seo.persisted", extra={
        "cost": Decimal("0.10"), "post_id": UUID("12345678-1234-5678-1234-567812345678"),
    })
    data = json.loads(JsonFormatter().format(record))
    assert data["cost"] == "0.10"
    assert data["post_id"] == "12345678-1234-5678-1234-567812345678"


def test_exceptions_are_reported_by_class_and_stack():
    try:
        raise ValueError("boom")
    except ValueError:
        record = make_record("render.failed", level=logging.ERROR, exc_info=sys.exc_info())

    data = json.loads(JsonFormatter().format(record))
    assert data["exc_type"] == "ValueError"
    assert "Traceback" in data["stack"]


def test_the_celery_filter_adds_the_running_task(monkeypatch):
    import celery

    task = types.SimpleNamespace(name="apps.videos.tasks.render_clip_task",
                                 request=types.SimpleNamespace(id="task-123"))
    monkeypatch.setattr(celery, "current_task", task)
    record = make_record("render.started")

    assert CeleryTaskContextFilter().filter(record) is True
    assert record.task_id == "task-123"
    assert record.task_name == "apps.videos.tasks.render_clip_task"


def test_outside_a_task_the_filter_adds_nothing(monkeypatch):
    import celery

    monkeypatch.setattr(celery, "current_task", None)
    record = make_record("upload.rejected")
    CeleryTaskContextFilter().filter(record)
    assert not hasattr(record, "task_id")


def test_production_settings_log_json_and_celery_keeps_the_handlers():
    from django.conf import settings

    from backend.celery import app

    # backend.settings.ci corre con DEBUG=False, como producción.
    assert settings.LOG_FORMAT == "json"
    assert settings.LOGGING["handlers"]["console"]["formatter"] == "json"
    assert settings.LOGGING["formatters"]["json"]["()"] == "apps.core.structured_logging.JsonFormatter"
    assert app.conf.worker_hijack_root_logger is False


def test_the_logging_config_is_valid():
    import copy
    import logging.config

    from django.conf import settings

    root = logging.getLogger()
    saved = (root.handlers[:], root.level)
    try:
        logging.config.dictConfig(copy.deepcopy(settings.LOGGING))
        assert any(isinstance(h.formatter, JsonFormatter) for h in logging.getLogger().handlers)
    finally:
        root.handlers[:], root.level = saved


# ---------------------------------------------------------------------------
# Alertas por fallas seguidas
# ---------------------------------------------------------------------------

class CounterRedis:
    def __init__(self):
        self.values = {}
        self.ttl = {}

    def incr(self, key):
        self.values[key] = self.values.get(key, 0) + 1
        return self.values[key]

    def expire(self, key, seconds):
        self.ttl[key] = seconds

    def delete(self, key):
        self.values.pop(key, None)


def task(name="apps.videos.tasks.render_clip_task", eager=False):
    return types.SimpleNamespace(name=name, request=types.SimpleNamespace(is_eager=eager))


@pytest.fixture
def redis_counter(monkeypatch, settings):
    settings.TASK_FAILURE_ALERT_THRESHOLD = 3
    settings.ADMINS = []
    fake = CounterRedis()
    monkeypatch.setattr(task_alerts, "_redis", lambda: fake)
    return fake


def critical_alerts(caplog):
    return [r for r in caplog.records if r.levelno == logging.CRITICAL and "alert.task_failing" in r.getMessage()]


def test_the_alert_fires_on_the_third_consecutive_failure_and_every_multiple(redis_counter, caplog):
    t = task()
    with caplog.at_level(logging.INFO, logger=task_alerts.logger.name):
        counts = [task_alerts.record_task_failure(t, RuntimeError()) for _ in range(6)]

    assert counts == [1, 2, 3, 4, 5, 6]
    alerts = critical_alerts(caplog)
    assert [a.consecutive_failures for a in alerts] == [3, 6]
    assert alerts[0].task == t.name and alerts[0].error == "RuntimeError"
    assert redis_counter.ttl  # el contador vence solo


def test_a_success_resets_the_count(redis_counter, caplog):
    t = task()
    with caplog.at_level(logging.INFO, logger=task_alerts.logger.name):
        task_alerts.record_task_failure(t)
        task_alerts.record_task_failure(t)
        task_alerts.record_task_success(t)
        task_alerts.record_task_failure(t)
        task_alerts.record_task_failure(t)

    assert critical_alerts(caplog) == []


def test_counts_are_per_task(redis_counter, caplog):
    with caplog.at_level(logging.INFO, logger=task_alerts.logger.name):
        for name in ("a", "b", "a", "b", "a"):
            task_alerts.record_task_failure(task(name))
    assert [a.task for a in critical_alerts(caplog)] == ["a"]


def test_eager_tasks_and_a_zero_threshold_are_ignored(redis_counter, settings):
    assert task_alerts.record_task_failure(task(eager=True)) == 0
    settings.TASK_FAILURE_ALERT_THRESHOLD = 0
    assert task_alerts.record_task_failure(task()) == 0
    assert redis_counter.values == {}


def test_a_redis_outage_never_breaks_the_task(monkeypatch, settings):
    settings.TASK_FAILURE_ALERT_THRESHOLD = 3

    def down():
        raise ConnectionError("redis down")

    monkeypatch.setattr(task_alerts, "_redis", down)
    assert task_alerts.record_task_failure(task(), RuntimeError()) == 0
    task_alerts.record_task_success(task())


def test_admins_get_an_email(redis_counter, settings):
    settings.ADMINS = [("ops@test.com", "ops@test.com")]
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    for _ in range(3):
        task_alerts.record_task_failure(task(), TimeoutError())

    assert len(mail.outbox) == 1
    assert "falló 3 veces seguidas" in mail.outbox[0].subject
    assert "TimeoutError" in mail.outbox[0].body


def test_celery_signals_feed_the_counter():
    from celery.signals import task_failure, task_success

    from backend import celery as celery_module

    failure_receivers = [ref() for _, ref in task_failure.receivers]
    success_receivers = [ref() for _, ref in task_success.receivers]
    assert celery_module.count_task_failure_alert in failure_receivers
    assert celery_module.reset_task_failure_alert in success_receivers

    t = task()
    with patch.object(task_alerts, "record_task_failure") as failed, \
            patch.object(task_alerts, "record_task_success") as succeeded:
        celery_module.count_task_failure_alert(sender=t, exception=KeyError())
        celery_module.reset_task_failure_alert(sender=t)
    failed.assert_called_once()
    succeeded.assert_called_once_with(t)
