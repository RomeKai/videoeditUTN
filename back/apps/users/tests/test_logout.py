"""Logout (I1 and the bar): this session, or every session of the user."""
from datetime import timedelta
from unittest import mock

import pytest
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework.throttling import ScopedRateThrottle
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken
from rest_framework_simplejwt.tokens import RefreshToken

from apps.users.models import User
from apps.users.tests.test_login_throttle import REDIS_DOWN, _refused

pytestmark = pytest.mark.django_db

LOGIN_URL = "/api/token/"
REFRESH_URL = "/api/token/refresh/"
LOGOUT_URL = "/api/v1/auth/logout/"
ME_URL = "/api/v1/me/"
PASSWORD = "password"


def _login(email="tester@test.com"):
    response = APIClient().post(LOGIN_URL, {"email": email, "password": PASSWORD}, format="json")
    assert response.status_code == 200, response.content
    return response.json()


def _client(access):
    api = APIClient()
    api.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
    return api


def _logout(session, refresh=None, **body):
    return _client(session["access"]).post(LOGOUT_URL, {"refresh": refresh or session["refresh"], **body}, format="json")


def _refreshes(refresh):
    return APIClient().post(REFRESH_URL, {"refresh": refresh}, format="json").status_code == 200


@pytest.fixture
def other_user(db):
    return User.objects.create_user(username="otra", email="otra@test.com", password=PASSWORD)


def test_logout_ends_this_session_only(user):
    here, phone = _login(), _login()

    response = _logout(here)

    assert response.status_code == 204
    assert response.content == b""
    assert not _refreshes(here["refresh"])
    assert _client(phone["access"]).get(ME_URL).status_code == 200
    assert _refreshes(phone["refresh"])


def test_all_devices_ends_every_session_at_once_access_tokens_included(user):
    here, phone, laptop = _login(), _login(), _login()

    response = _logout(here, all_devices=True)

    assert response.status_code == 204
    for session in (here, phone, laptop):
        # Not in 25 minutes, when the access token expires: now.
        assert _client(session["access"]).get(ME_URL).status_code == 401
        assert not _refreshes(session["refresh"])
    assert _client(phone["access"]).get(ME_URL).json() == {
        "detail": "La sesión se cerró en todos los dispositivos. Iniciá sesión de nuevo.",
        "code": "session_revoked",
    }


def test_a_login_after_closing_everything_works(user):
    here = _login()
    # The revocation a few seconds ago: the login below is a later second (`iat` has whole seconds).
    earlier = timezone.now() - timedelta(seconds=5)
    with mock.patch("apps.users.tokens.timezone.now", return_value=earlier):
        assert _logout(here, all_devices=True).status_code == 204

    later = _login()

    assert _client(later["access"]).get(ME_URL).status_code == 200
    assert _refreshes(later["refresh"])


def test_a_refresh_without_a_blacklist_row_is_refused_after_closing_everything(user):
    """A refresh no list knows about (issued before token_blacklist): its date refuses it."""
    orphan = RefreshToken.for_user(user)
    OutstandingToken.objects.filter(jti=orphan["jti"]).delete()
    here = _login()

    assert _logout(here, all_devices=True).status_code == 204

    response = APIClient().post(REFRESH_URL, {"refresh": str(orphan)}, format="json")
    assert response.status_code == 401
    assert response.json()["code"] == "session_revoked"


SESSION_REVOKED_BODY = {
    "detail": "La sesión se cerró en todos los dispositivos. Iniciá sesión de nuevo.",
    "code": "session_revoked",
}


def _refresh(refresh):
    return APIClient().post(REFRESH_URL, {"refresh": refresh}, format="json")


def test_the_refresh_of_a_closed_session_says_so_although_it_is_blacklisted(user):
    """
    The other device's access token had expired before the revocation: its first
    answer is the refresh's. revoke_sessions blacklisted that refresh, and the
    blacklist is checked first: it said token_not_valid, and the web app «Tu sesión venció».
    """
    here, phone = _login(), _login()
    assert _logout(here, all_devices=True).status_code == 204
    assert BlacklistedToken.objects.filter(token__jti=RefreshToken(phone["refresh"], verify=False)["jti"]).exists()

    for session in (phone, here):
        response = _refresh(session["refresh"])
        assert response.status_code == 401
        assert response.json() == SESSION_REVOKED_BODY


def _signed_with_another_key(payload):
    import jwt

    return jwt.encode(payload, "otra-clave-que-no-es-la-del-servidor", algorithm="HS256")


@pytest.mark.parametrize("case", ["rotated", "forged", "tampered", "expired", "access", "password_changed"])
def test_a_refresh_refused_for_another_reason_keeps_saying_token_not_valid(user, case):
    """Only a token this server signed, still valid but for the revocation, says session_revoked: nothing else learns it."""
    if case == "rotated":
        # Blacklisted by its rotation, and issued after the revocation: an old copy, not a closed session.
        earlier = timezone.now() - timedelta(seconds=5)
        with mock.patch("apps.users.tokens.timezone.now", return_value=earlier):
            assert _logout(_login(), all_devices=True).status_code == 204
        session = _login()
        assert _refresh(session["refresh"]).status_code == 200
        token = session["refresh"]
    else:
        session = _login()
        assert _logout(_login(), all_devices=True).status_code == 204
        if case == "forged":
            # Everything a real one would have, but not signed by this server.
            token = _signed_with_another_key(RefreshToken(session["refresh"], verify=False).payload)
        elif case == "tampered":
            head, body, signature = session["refresh"].split(".")
            token = f"{head}.{body}.{signature[:-4]}AAAA"
        elif case == "expired":
            old = RefreshToken.for_user(user)
            old.set_iat(at_time=timezone.now() - timedelta(days=5))
            old.set_exp(from_time=timezone.now() - timedelta(days=4))
            old.blacklist()
            token = str(old)
        elif case == "access":
            token = session["access"]
        else:
            user.set_password("una-clave-Nueva-2026")
            user.save()
            token = session["refresh"]

    response = _refresh(token)

    assert response.status_code == 401
    assert response.json()["code"] == "token_not_valid"


def test_the_refresh_of_another_account_is_refused_and_nothing_is_revoked(user, other_user):
    mine, theirs = _login(), _login("otra@test.com")
    my_phone = _login()

    for body in ({}, {"all_devices": True}):
        response = _logout(mine, refresh=theirs["refresh"], **body)
        assert response.status_code == 400
        assert response.json() == {"refresh": ["Ese refresh es de otra cuenta: no se cerró nada."]}

    assert not BlacklistedToken.objects.exists()
    assert User.objects.get(pk=user.pk).sessions_revoked_at is None
    assert _client(my_phone["access"]).get(ME_URL).status_code == 200
    assert _refreshes(theirs["refresh"])
    assert _refreshes(mine["refresh"])


def test_logout_needs_the_session_of_the_account():
    response = APIClient().post(LOGOUT_URL, {"refresh": "x"}, format="json")

    assert response.status_code == 401


@pytest.mark.parametrize("refresh", ["no-es-un-token", "access"])
def test_something_that_is_not_a_refresh_of_this_account_is_a_400(user, refresh):
    session = _login()
    value = session["access"] if refresh == "access" else refresh

    response = _logout(session, refresh=value)

    assert response.status_code == 400
    assert response.json() == {"refresh": ["La sesión no es válida o ya venció."]}
    assert _refreshes(session["refresh"])


def test_an_expired_refresh_is_a_400(user):
    session = _login()
    old = RefreshToken.for_user(user)
    old.set_exp(from_time=timezone.now() - timedelta(days=4))

    assert _logout(session, refresh=str(old)).status_code == 400


def test_a_refresh_renewed_on_the_way_is_already_closed(user):
    """The access token had expired: the client renewed the pair (rotation) and sends the old refresh."""
    first = _login()
    renewed = APIClient().post(REFRESH_URL, {"refresh": first["refresh"]}, format="json").json()

    response = _logout(renewed, refresh=first["refresh"])

    assert response.status_code == 204
    # The web app then closes the renewed one too (useLogout).
    assert _refreshes(renewed["refresh"])
    assert _logout(renewed).status_code == 204
    assert not _refreshes(renewed["refresh"])


def test_logging_out_twice_is_a_204(user):
    session = _login()

    assert _logout(session).status_code == 204
    assert _logout(session).status_code == 204


def test_logout_is_limited_per_user(user):
    sessions = [_login() for _ in range(3)]
    with mock.patch.dict(ScopedRateThrottle.THROTTLE_RATES, {"logout": "2/hour"}):
        answers = [_logout(session).status_code for session in sessions]

    assert answers == [204, 204, 429]


def test_with_redis_down_logout_is_still_limited(user):
    sessions = [_login() for _ in range(3)]
    with mock.patch.dict(ScopedRateThrottle.THROTTLE_RATES, {"logout": "2/hour"}), \
            override_settings(CACHES=REDIS_DOWN), _refused():
        answers = [_logout(session).status_code for session in sessions]

    assert answers == [204, 204, 429]
