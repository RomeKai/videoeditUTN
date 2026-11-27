"""
One account per mailbox whatever the spelling of the email's domain
(apps/users/emails.py): full-width letters, Unicode and its IDNA form.
"""
from io import StringIO
from unittest import mock

import pytest
from django.contrib import admin
from django.core import mail
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import RequestFactory
from rest_framework.test import APIClient
from rest_framework.throttling import ScopedRateThrottle

from apps.users.emails import InvalidEmailDomain, normalize_email
from apps.users.models import User

from .clocks import frozen_clocks

pytestmark = pytest.mark.django_db

LOGIN_URL = "/api/token/"
ME_URL = "/api/v1/me/"
INVALID_DOMAIN = "El dominio del email no es válido. Revisá que esté bien escrito."
FULL_WIDTH = "tester@ｔｅｓｔ.com"
# Django's EmailValidator takes it, but "℀" is "a/c" in NFKC: not a host name.
UNENCODABLE = "tester@℀.com"


def _user(username, email):
    return User.objects.create_user(username=username, email=email, password="password")


@pytest.mark.parametrize("typed, stored", [
    ("tester@ｔｅｓｔ.com", "tester@test.com"),
    ("tester@ＴＥＳＴ．ＣＯＭ", "tester@test.com"),
    ("tester@test。com", "tester@test.com"),
    ("Ana@Mañana.COM", "Ana@xn--maana-pta.com"),
    ("Ana@XN--MAANA-PTA.com", "Ana@xn--maana-pta.com"),
    # "ñ" as n + a combining tilde: the same domain.
    ("Ana@mañana.com", "Ana@xn--maana-pta.com"),
    ("  tester@test.com ", "tester@test.com"),
    # IDNA 2008: "ß" and the final sigma are letters of their own, not "ss" and "σ".
    ("ana@straße.de", "ana@xn--strae-oqa.de"),
    ("ana@XN--STRAE-OQA.de", "ana@xn--strae-oqa.de"),
    ("ana@strasse.de", "ana@strasse.de"),
    ("ana@βόλος.gr", "ana@xn--nxasmm1c.gr"),
    ("ana@βόλοσ.gr", "ana@xn--nxasmq6b.gr"),
    # A zero-width joiner where the script uses one is part of the name.
    ("ana@क्\u200dष.com", "ana@xn--11b2ezcw70k.com"),
    ("tester@[127.0.0.1]", "tester@[127.0.0.1]"),
    ("sin-arroba", "sin-arroba"),
    ("", ""),
    (None, ""),
])
def test_the_domain_is_normalized(typed, stored):
    assert normalize_email(typed) == stored


@pytest.mark.parametrize("email", [UNENCODABLE, "a@b..com", "a@-b.com", "a@b_c.com", "a@b c.com", f"a@{'ñ' * 64}.com", "a@xn--zz.com", "a@a\u200db.com"])
def test_a_domain_that_cannot_be_encoded_is_refused(email):
    with pytest.raises(InvalidEmailDomain):
        normalize_email(email)


# 178 characters as typed (EmailField takes it), 300 with its domain in punycode.
LONG_DOMAIN = "ana@" + ".".join(["中文测试域名" * 3] * 9) + ".com"
# The domain fits; the whole email does not once it is encoded.
LONG_EMAIL = "a" * 220 + "@" + "中文测试域名" * 3 + ".com"
TOO_LONG = "El email es demasiado largo. Probá con una dirección más corta."


@pytest.mark.parametrize("email", [LONG_DOMAIN, LONG_EMAIL])
def test_an_email_that_does_not_fit_once_encoded_is_refused(email):
    assert len(email) <= 254

    with pytest.raises(InvalidEmailDomain) as error:
        normalize_email(email)

    assert error.value.message == TOO_LONG


def test_an_email_of_254_characters_is_kept():
    email = "a" * 245 + "@test.com"

    assert normalize_email(email) == email


def test_the_local_part_is_left_as_typed():
    assert normalize_email("Ｔester@test.com") == "Ｔester@test.com"


# --- storage ---

def test_a_full_width_domain_cannot_be_a_second_account(user):
    with pytest.raises(IntegrityError), transaction.atomic():
        _user("otro", FULL_WIDTH)

    assert User.objects.count() == 1


def test_the_unicode_and_the_idna_form_of_a_domain_are_one_account():
    first = _user("ana", "ana@mañana.com")

    with pytest.raises(IntegrityError), transaction.atomic():
        _user("ana2", "ana@xn--maana-pta.com")

    assert User.objects.get(pk=first.pk).email == "ana@xn--maana-pta.com"


def test_saving_stores_both_emails_normalized(user):
    user.email = "otra@ｔｅｓｔ.com"
    user.pending_email = "Nueva@Mañana.com"
    user.save()

    user.refresh_from_db()
    assert (user.email, user.pending_email) == ("otra@test.com", "Nueva@xn--maana-pta.com")


def test_an_account_cannot_be_created_with_a_domain_that_cannot_be_encoded():
    with pytest.raises(InvalidEmailDomain):
        _user("raro", UNENCODABLE)

    assert not User.objects.exists()


# --- login ---

@pytest.mark.parametrize("typed", [FULL_WIDTH, "TESTER@ＴＥＳＴ.COM", "tester@test。com"])
def test_the_login_finds_the_account_by_any_spelling_of_the_domain(user, typed):
    response = APIClient().post(LOGIN_URL, {"email": typed, "password": "password"}, format="json")

    assert response.status_code == 200


def test_the_login_with_a_unicode_domain_signs_into_the_account_stored_in_idna():
    _user("ana", "ana@mañana.com")

    response = APIClient().post(LOGIN_URL, {"email": "ana@MAÑANA.com", "password": "password"}, format="json")

    assert response.status_code == 200


def test_the_login_with_a_domain_that_cannot_be_encoded_is_a_401(user):
    response = APIClient().post(LOGIN_URL, {"email": UNENCODABLE, "password": "password"}, format="json")

    # Like any email without an account: the login never says why.
    assert response.status_code == 401


def test_failures_with_another_spelling_of_the_domain_lock_the_same_account(user):
    api = APIClient(REMOTE_ADDR="10.0.20.1")
    # Ten failures inside one window: a clock that does not move (they could cross its edge).
    with frozen_clocks(), mock.patch.dict(ScopedRateThrottle.THROTTLE_RATES, {"login": "1000/min"}):
        failures = [
            api.post(LOGIN_URL, {"email": FULL_WIDTH, "password": "otra"}, format="json").status_code
            for _ in range(10)
        ]
        blocked = api.post(LOGIN_URL, {"email": "tester@test.com", "password": "password"}, format="json")

    assert failures == [401] * 10
    assert blocked.status_code == 429


# --- PATCH /me ---

def _api(user):
    api = APIClient()
    api.force_authenticate(user=user)
    return api


def test_another_spelling_of_a_taken_email_is_taken_for_an_email_change(user):
    other = _user("otro", "otro@test.com")

    response = _api(other).patch(ME_URL, {"email": FULL_WIDTH, "current_password": "password"}, format="json")

    assert response.status_code == 400
    assert response.json() == {"email": ["Ya existe una cuenta con este email."]}
    assert mail.outbox == []


def test_an_email_change_keeps_the_normalized_email_and_sends_the_link_there(user):
    response = _api(user).patch(ME_URL, {"email": "Nueva@Mañana.com", "current_password": "password"}, format="json")

    assert response.status_code == 200
    assert response.json()["pending_email"] == "Nueva@xn--maana-pta.com"
    assert [message.to for message in mail.outbox] == [["Nueva@xn--maana-pta.com"]]


def test_an_email_change_to_a_domain_that_cannot_be_encoded_is_a_400(user):
    response = _api(user).patch(ME_URL, {"email": UNENCODABLE, "current_password": "password"}, format="json")

    assert response.status_code == 400
    assert response.json() == {"email": [INVALID_DOMAIN]}
    user.refresh_from_db()
    assert user.pending_email is None
    assert mail.outbox == []


@pytest.mark.parametrize("email", [LONG_DOMAIN, LONG_EMAIL])
def test_an_email_change_that_does_not_fit_once_encoded_is_a_400(user, email):
    response = _api(user).patch(ME_URL, {"email": email, "current_password": "password"}, format="json")

    assert response.status_code == 400
    assert response.json() == {"email": [TOO_LONG]}
    user.refresh_from_db()
    assert user.pending_email is None
    assert mail.outbox == []


def test_the_sharp_s_domain_is_not_the_account_of_the_double_s_domain():
    _user("ss", "ana@strasse.de")
    sharp = _user("sharp", "ana@straße.de")

    assert User.objects.get(pk=sharp.pk).email == "ana@xn--strae-oqa.de"
    response = APIClient().post(LOGIN_URL, {"email": "ana@xn--strae-oqa.de", "password": "password"}, format="json")
    assert response.status_code == 200
    with pytest.raises(IntegrityError), transaction.atomic():
        _user("sharp2", "ana@XN--strae-oqa.de")


def test_the_own_email_in_another_spelling_cancels_the_pending_change(user):
    User.objects.filter(pk=user.pk).update(pending_email="nueva@test.com")

    # No password: it is not a change.
    response = _api(user).patch(ME_URL, {"email": FULL_WIDTH}, format="json")

    assert response.status_code == 200
    assert response.json()["pending_email"] is None
    assert mail.outbox == []


# --- admin ---

def _admin_form(user, **changes):
    model_admin = admin.site._registry[User]
    request = RequestFactory().get("/")
    request.user = User(username="staff", is_staff=True, is_superuser=True)
    form_class = model_admin.get_form(request, obj=user, change=True)
    data = {name: value for name, value in form_class(instance=user).initial.items() if value is not None}
    data.update({"date_joined_0": "2026-01-01", "date_joined_1": "10:00:00", **changes})
    return form_class(data, instance=user)


# Django's own clean already applies NFKC to the USERNAME_FIELD (full-width letters);
# the IDNA form and the error in Spanish are what apps.users adds.
def test_the_admin_stores_the_email_normalized(user):
    form = _admin_form(user, email="Otra@Mañana.com")

    assert form.is_valid(), form.errors
    form.save()
    assert User.objects.get(pk=user.pk).email == "Otra@xn--maana-pta.com"


def test_the_admin_refuses_a_domain_that_cannot_be_encoded(user):
    form = _admin_form(user, email=UNENCODABLE)

    assert not form.is_valid()
    assert form.errors["email"] == [INVALID_DOMAIN]


def test_the_admin_refuses_another_spelling_of_a_taken_email(user):
    _user("ana", "ana@xn--maana-pta.com")

    form = _admin_form(user, email="ana@mañana.com")

    assert not form.is_valid()
    assert User.objects.get(pk=user.pk).email == "tester@test.com"


def test_full_clean_reports_the_pending_email_too(user):
    user.pending_email = UNENCODABLE

    with pytest.raises(ValidationError) as error:
        user.full_clean()

    assert error.value.message_dict == {"pending_email": [INVALID_DOMAIN]}


# --- accounts stored before the domain was normalized ---

def _stored_as(username, email):
    """A row as it was written before: straight to the table, past User.save."""
    created = _user(username, f"{username}@temporal.test")
    User.objects.filter(pk=created.pk).update(email=email)
    return created


def test_the_command_reports_the_accounts_by_count_and_id_never_by_email(user):
    collides = _stored_as("choca", FULL_WIDTH)
    invalid = _stored_as("raro", UNENCODABLE)
    pending = _stored_as("pendiente", "ana@mañana.com")
    User.objects.filter(pk=user.pk).update(pending_email="Nueva@Mañana.com")
    User.objects.filter(pk=invalid.pk).update(pending_email=UNENCODABLE)
    out = StringIO()

    call_command("check_email_domains", stdout=out)

    assert out.getvalue().splitlines() == [
        "Cuentas con el dominio del email sin normalizar: 4",
        "Otra cuenta ya tiene el mismo email normalizado: 1",
        f"  {collides.pk}",
        "El dominio no se puede codificar: 1",
        f"  {invalid.pk}",
        "Sin normalizar y sin choque (se normaliza al guardar la cuenta): 1",
        f"  {pending.pk}",
        "El email pendiente de verificar no está normalizado o no se puede codificar: 2",
        f"  {user.pk}",
        f"  {invalid.pk}",
    ]
    assert "@" not in out.getvalue()
    # It only reports.
    assert User.objects.get(pk=collides.pk).email == FULL_WIDTH
    assert User.objects.get(pk=pending.pk).email == "ana@mañana.com"


def test_the_command_with_nothing_to_report(user):
    out = StringIO()

    call_command("check_email_domains", stdout=out)

    assert out.getvalue().splitlines()[0] == "Cuentas con el dominio del email sin normalizar: 0"


def test_an_account_left_with_a_colliding_email_can_still_be_used_and_saved_in_part(user):
    collides = _stored_as("choca", FULL_WIDTH)
    collides.refresh_from_db()

    # What the views do (update_fields): the email is not rewritten, nothing breaks.
    collides.first_name = "Ana"
    collides.save(update_fields=["first_name"])

    assert User.objects.get(pk=collides.pk).email == FULL_WIDTH


# --- an account stored before with a domain that cannot be encoded keeps working ---

@pytest.fixture
def legacy():
    created = _stored_as("raro", UNENCODABLE)
    created.refresh_from_db()
    return created


def test_a_legacy_account_with_an_invalid_domain_still_signs_in(legacy):
    response = APIClient().post(LOGIN_URL, {"email": UNENCODABLE, "password": "password"}, format="json")

    assert response.status_code == 200


def test_failed_logins_of_a_legacy_account_are_counted(legacy):
    api = APIClient(REMOTE_ADDR="10.0.21.1")
    # Ten failures inside one window: a clock that does not move (they could cross its edge).
    with frozen_clocks(), mock.patch.dict(ScopedRateThrottle.THROTTLE_RATES, {"login": "1000/min"}):
        failures = [
            api.post(LOGIN_URL, {"email": UNENCODABLE, "password": "otra"}, format="json").status_code
            for _ in range(10)
        ]
        blocked = api.post(LOGIN_URL, {"email": UNENCODABLE, "password": "password"}, format="json")

    assert failures == [401] * 10
    assert blocked.status_code == 429


def test_a_legacy_account_with_an_invalid_domain_can_change_its_name(legacy):
    response = _api(legacy).patch(ME_URL, {"name": "Ana Paz"}, format="json")

    assert response.status_code == 200
    assert response.json()["name"] == "Ana Paz"
    assert response.json()["email"] == UNENCODABLE


def test_a_legacy_account_with_an_invalid_domain_can_change_its_password(legacy):
    response = _api(legacy).post(
        "/api/v1/me/password/", {"current_password": "password", "new_password": "una-clave-Nueva-2026"}, format="json",
    )

    assert response.status_code == 200
    legacy.refresh_from_db()
    assert legacy.check_password("una-clave-Nueva-2026")


def test_a_legacy_account_with_an_invalid_domain_can_move_to_a_valid_email(legacy):
    response = _api(legacy).patch(ME_URL, {"email": "raro@test.com", "current_password": "password"}, format="json")

    assert response.status_code == 200
    assert response.json()["pending_email"] == "raro@test.com"


def test_a_link_for_a_legacy_pending_email_with_an_invalid_domain_is_a_400(user):
    from apps.users.email_verification import make_token

    User.objects.filter(pk=user.pk).update(pending_email=UNENCODABLE)
    user.refresh_from_db()
    token = make_token(user)

    response = _api(user).post("/api/v1/auth/verify-email/", {"token": token}, format="json")

    assert response.status_code == 400
    assert response.json() == {"token": [INVALID_DOMAIN]}
    user.refresh_from_db()
    assert (user.email, user.pending_email, user.is_email_verified) == ("tester@test.com", UNENCODABLE, False)


def test_a_full_save_of_a_legacy_account_with_an_invalid_domain_is_refused(legacy):
    # Writing the email again needs a valid one; the partial saves above do not write it.
    with pytest.raises(InvalidEmailDomain):
        legacy.save()
