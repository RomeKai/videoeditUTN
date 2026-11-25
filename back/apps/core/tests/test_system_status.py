import pytest
from django.test import override_settings
from rest_framework.test import APIClient

STATUS_URL = "/api/v1/status/"


def test_status_is_public_and_off_by_default():
    response = APIClient().get(STATUS_URL)

    assert response.status_code == 200
    assert response.json() == {"maintenance": False, "maintenance_window": None}


@pytest.mark.parametrize("flag", [True])
def test_status_reports_maintenance_from_settings(flag):
    with override_settings(MAINTENANCE_MODE=flag):
        response = APIClient().get(STATUS_URL)

    assert response.json()["maintenance"] is flag
