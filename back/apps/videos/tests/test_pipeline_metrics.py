"""
INFRA-02 · Duración por etapa guardada y métricas del pipeline (servicio, comando y API).
"""
import json
from datetime import timedelta
from io import StringIO
from unittest.mock import patch

import pytest
from django.core.management import CommandError, call_command
from django.db import DatabaseError
from django.utils import timezone
from rest_framework.test import APIClient

from apps.videos.models import VideoProject
from apps.videos.services import pipeline_metrics
from apps.videos.services.ai.pipeline_state import PipelineStage
from apps.videos.tasks import ingestion

pytestmark = pytest.mark.django_db

URL = "/api/v1/ops/pipeline-metrics/"


def make_project(workspace, user, status=VideoProject.Status.COMPLETED, durations=None,
                 error_code=None, age_hours=1):
    project = VideoProject.objects.create(
        workspace=workspace, uploaded_by=user, title="m", status=status,
        metadata={"stage_durations": durations or {}},
        pipeline_error_code=error_code or "",
    )
    VideoProject.objects.filter(pk=project.pk).update(
        created_at=timezone.now() - timedelta(hours=age_hours),
    )
    return project


# ---------------------------------------------------------------------------
# Duración por etapa guardada en la base
# ---------------------------------------------------------------------------

def test_a_completed_stage_stores_its_duration(workspace, user):
    project = make_project(workspace, user, durations={"audio_extracted": 1.5})
    with patch.object(ingestion, "set_stage_status"):
        with ingestion._running_stage(project.id, PipelineStage.TRANSCRIBED, attempt=1):
            pass

    project.refresh_from_db()
    durations = project.metadata["stage_durations"]
    assert durations["audio_extracted"] == 1.5
    assert durations[str(PipelineStage.TRANSCRIBED)] >= 0


def test_a_failed_stage_stores_nothing(workspace, user):
    project = make_project(workspace, user)
    with patch.object(ingestion, "set_stage_status"), pytest.raises(ValueError):
        with ingestion._running_stage(project.id, PipelineStage.TRANSCRIBED, attempt=1):
            raise ValueError("boom")
    project.refresh_from_db()
    assert project.metadata["stage_durations"] == {}


def test_a_database_error_never_breaks_the_stage(workspace, user):
    project = make_project(workspace, user)
    with patch.object(ingestion.VideoProject.objects, "select_for_update", side_effect=DatabaseError):
        ingestion._record_stage_duration(project.id, "transcribed", 2.0)


def test_the_proxy_duration_is_stored(workspace, user):
    from django.core.files.uploadedfile import SimpleUploadedFile

    project = make_project(workspace, user)
    project.source_file = SimpleUploadedFile("s.mp4", b"x")
    project.save()
    with patch("apps.videos.utils.ffmpeg_utils.FFmpegManager.generate_web_proxy"), \
            patch("apps.videos.services.storage_service.CloudflareR2Manager.upload_video", return_value="k"):
        ingestion._generate_proxy(project, project.source_file.path, "u")
    project.refresh_from_db()
    assert project.metadata["stage_durations"]["proxy"] >= 0


# ---------------------------------------------------------------------------
# Métricas
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("values, p50, p95", [
    ([], None, None),
    ([4.0], 4.0, 4.0),
    ([float(v) for v in range(1, 21)], 10.0, 19.0),
    ([float(v) for v in range(1, 101)], 50.0, 95.0),
])
def test_percentiles_use_the_nearest_rank(values, p50, p95):
    assert pipeline_metrics._percentile(values, 0.50) == p50
    assert pipeline_metrics._percentile(values, 0.95) == p95


def test_collect_summarizes_stages_statuses_and_failures(workspace, user):
    make_project(workspace, user, durations={"transcribed": 10.0, "proxy": 3.0})
    make_project(workspace, user, durations={"transcribed": 30.0})
    make_project(workspace, user, status=VideoProject.Status.AWAITING_APPROVAL,
                 durations={"transcribed": 20.0})
    make_project(workspace, user, status=VideoProject.Status.FAILED, error_code="AITimeoutError")
    make_project(workspace, user, status=VideoProject.Status.FAILED, error_code="AITimeoutError")
    make_project(workspace, user, status=VideoProject.Status.FAILED)
    make_project(workspace, user, durations={"transcribed": 999.0}, age_hours=48)  # fuera de la ventana

    metrics = pipeline_metrics.collect(hours=24)

    assert metrics["stages"]["transcribed"] == {"count": 3, "p50_s": 20.0, "p95_s": 30.0, "max_s": 30.0}
    assert metrics["stages"]["proxy"]["count"] == 1
    assert metrics["projects"][VideoProject.Status.FAILED] == 3
    assert metrics["failures"] == {"AITimeoutError": 2, "unknown": 1}
    assert metrics["failure_rate"] == 0.5  # 3 fallados de 6 terminados
    assert metrics["window"]["hours"] == 24


def test_collect_on_an_empty_window(db):
    metrics = pipeline_metrics.collect(hours=1)
    assert metrics["stages"] == {} and metrics["failure_rate"] is None


def test_collect_ignores_garbage_durations(workspace, user):
    make_project(workspace, user, durations={"transcribed": "rápido", "selection": -1, "proxy": 2})
    assert list(pipeline_metrics.collect()["stages"]) == ["proxy"]


def test_the_command_prints_a_table_and_json(workspace, user):
    make_project(workspace, user, durations={"transcribed": 12.0})

    table = StringIO()
    call_command("pipeline_metrics", stdout=table)
    assert "transcribed" in table.getvalue() and "12.0" in table.getvalue()

    raw = StringIO()
    call_command("pipeline_metrics", "--json", "--hours", "6", stdout=raw)
    assert json.loads(raw.getvalue())["stages"]["transcribed"]["count"] == 1

    with pytest.raises(CommandError):
        call_command("pipeline_metrics", "--hours", "0")


# ---------------------------------------------------------------------------
# Endpoint para staff
# ---------------------------------------------------------------------------

@pytest.fixture
def staff_client(django_user_model):
    admin = django_user_model.objects.create_user(username="ops", email="ops@t.com", password="x",
                                                  is_staff=True)
    client = APIClient()
    client.force_authenticate(user=admin)
    return client


def test_staff_gets_the_metrics(staff_client, workspace, user):
    make_project(workspace, user, durations={"transcribed": 5.0})
    res = staff_client.get(URL, {"hours": 12})
    assert res.status_code == 200
    assert res.data["stages"]["transcribed"]["count"] == 1
    assert res.data["window"]["hours"] == 12


@pytest.mark.parametrize("hours", ["0", "abc", "999999"])
def test_an_invalid_window_is_400(staff_client, hours):
    assert staff_client.get(URL, {"hours": hours}).status_code == 400


def test_regular_users_cannot_see_the_metrics(user):
    client = APIClient()
    client.force_authenticate(user=user)
    assert client.get(URL).status_code == 403
    assert APIClient().get(URL).status_code == 401
