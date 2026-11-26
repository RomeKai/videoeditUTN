import os
import subprocess
import sys
from pathlib import Path

import pytest
from django.test import override_settings
from rest_framework.test import APIClient

STATUS_URL = "/api/v1/status/"
WINDOW = {"MAINTENANCE_STARTS_AT": "2026-11-24T02:00:00Z", "MAINTENANCE_ENDS_AT": "2099-11-24T03:30:00Z"}


def test_status_is_public_and_off_by_default():
    response = APIClient().get(STATUS_URL)

    assert response.status_code == 200
    assert response.json() == {"maintenance": False, "maintenance_window": None}


@pytest.mark.parametrize("flag", [True])
def test_status_reports_maintenance_from_settings(flag):
    with override_settings(MAINTENANCE_MODE=flag):
        response = APIClient().get(STATUS_URL)

    assert response.json()["maintenance"] is flag


def test_maintenance_mode_and_window_are_read_from_the_environment():
    env = {**os.environ, "MAINTENANCE_MODE": "True", **WINDOW}
    code = (
        "import django; django.setup(); from django.conf import settings; "
        "print(settings.MAINTENANCE_MODE, settings.MAINTENANCE_STARTS_AT, settings.MAINTENANCE_ENDS_AT)"
    )

    result = subprocess.run(
        [sys.executable, "-c", code], env=env, cwd=Path(__file__).resolve().parents[3],
        capture_output=True, text=True, check=True,
    )

    assert result.stdout.split() == ["True", WINDOW["MAINTENANCE_STARTS_AT"], WINDOW["MAINTENANCE_ENDS_AT"]]


@override_settings(MAINTENANCE_MODE=True, **WINDOW)
def test_status_answers_with_the_window_during_maintenance():
    response = APIClient().get(STATUS_URL)

    assert response.status_code == 200
    assert response.json() == {
        "maintenance": True,
        "maintenance_window": {"starts_at": "2026-11-24T02:00:00Z", "ends_at": "2099-11-24T03:30:00Z"},
    }


@override_settings(MAINTENANCE_MODE=False, **WINDOW)
def test_the_window_is_not_reported_outside_maintenance():
    assert APIClient().get(STATUS_URL).json()["maintenance_window"] is None


@pytest.mark.parametrize(
    "starts_at, ends_at",
    [("", WINDOW["MAINTENANCE_ENDS_AT"]), ("mañana", WINDOW["MAINTENANCE_ENDS_AT"]), (WINDOW["MAINTENANCE_ENDS_AT"], WINDOW["MAINTENANCE_STARTS_AT"])],
)
def test_an_incomplete_or_invalid_window_is_left_out(starts_at, ends_at):
    with override_settings(MAINTENANCE_MODE=True, MAINTENANCE_STARTS_AT=starts_at, MAINTENANCE_ENDS_AT=ends_at):
        response = APIClient().get(STATUS_URL)

    assert response.json() == {"maintenance": True, "maintenance_window": None}


@pytest.mark.django_db
@override_settings(MAINTENANCE_MODE=True, **WINDOW)
@pytest.mark.parametrize("method, path", [("get", "/api/v1/me/"), ("post", "/api/token/"), ("get", "/api/v1/plans/")])
def test_the_rest_of_the_api_answers_503_during_maintenance(method, path):
    response = getattr(APIClient(), method)(path)

    assert response.status_code == 503
    assert response.json() == {"detail": "Estamos haciendo mantenimiento. Volvé en un rato."}
    assert int(response["Retry-After"]) > 0


@pytest.mark.django_db
@override_settings(MAINTENANCE_MODE=True, CORS_ALLOW_ALL_ORIGINS=True)
def test_the_browser_can_read_the_503():
    response = APIClient().get("/api/v1/me/", HTTP_ORIGIN="http://localhost:3000")

    assert response.status_code == 503
    assert response["Access-Control-Allow-Origin"] == "*"
    assert not response.has_header("Retry-After")


@pytest.mark.django_db
@override_settings(MAINTENANCE_MODE=True)
def test_the_admin_keeps_working_during_maintenance():
    assert APIClient().get("/admin/login/").status_code == 200
