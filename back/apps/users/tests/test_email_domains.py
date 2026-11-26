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
    ("tester@[127.0.0.1]", "tester@[127.0.0.1]"),
    ("sin-arroba", "sin-arroba"),
    ("", ""),
    (None, ""),
])
def test_the_domain_is_normalized(typed, stored):
    assert normalize_email(typed) == stored


@pytest.mark.parametrize("email", [UNENCODABLE, "a@b..com", "a@-b.com", "a@b_c.com", "a@b c.com", f"a@{'ñ' * 64}.com"])
def test_a_domain_that_cannot_be_encoded_is_refused(email):
    with pytest.raises(InvalidEmailDomain):
        normalize_email(email)


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
    with mock.patch.dict(ScopedRateThrottle.THROTTLE_RATES, {"login": "1000/min"}):
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
    out = StringIO()

    call_command("check_email_domains", stdout=out)

    assert out.getvalue().splitlines() == [
        "Cuentas con el dominio del email sin normalizar: 3",
        "Otra cuenta ya tiene el mismo email normalizado: 1",
        f"  {collides.pk}",
        "El dominio no se puede codificar: 1",
        f"  {invalid.pk}",
        "Sin normalizar y sin choque (se normaliza al guardar la cuenta): 1",
        f"  {pending.pk}",
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
