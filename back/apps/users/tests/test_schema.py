"""The OpenAPI schema documents the account and status endpoints with the answers they give."""
import pytest
from drf_spectacular.generators import SchemaGenerator

EXPECTED = {
    ("/api/token/", "post"): {"200", "401", "429"},
    ("/api/token/refresh/", "post"): {"200", "401", "429"},
    ("/api/v1/me/", "get"): {"200", "401"},
    ("/api/v1/me/", "patch"): {"200", "400", "401", "429"},
    ("/api/v1/me/password/", "post"): {"200", "400", "401", "429"},
    ("/api/v1/me/notification-preferences/", "get"): {"200", "401"},
    ("/api/v1/me/notification-preferences/", "patch"): {"200", "400", "401"},
    ("/api/v1/auth/verify-email/", "post"): {"200", "400"},
    ("/api/v1/status/", "get"): {"200"},
}


@pytest.fixture(scope="module")
def schema():
    return SchemaGenerator().get_schema(request=None, public=True)


@pytest.mark.parametrize("path, method", list(EXPECTED))
def test_each_operation_lists_its_status_codes(schema, path, method):
    assert set(schema["paths"][path][method]["responses"]) == EXPECTED[(path, method)]


def test_the_bodies_are_described(schema):
    components = schema["components"]["schemas"]

    assert set(components["Me"]["properties"]) >= {"pending_email", "onboarding_use_case", "onboarding", "active_workspace_id"}
    assert set(components["TokenPair"]["properties"]) == {"access", "refresh"}
    assert set(components["SystemStatus"]["properties"]) == {"maintenance", "maintenance_window"}
    patch = schema["paths"]["/api/v1/me/"]["patch"]["requestBody"]["content"]["application/json"]["schema"]["$ref"]
    assert set(components[patch.rsplit("/", 1)[1]]["properties"]) == {
        "name", "email", "current_password", "tour_completed", "onboarding_use_case", "active_workspace_id",
    }
