"""One account per email whatever its case: login, storage and the race of two confirmations."""
from unittest import mock

import pytest
from django.core import mail
from django.db import IntegrityError, transaction
from rest_framework.test import APIClient

from apps.users.email_verification import make_token
from apps.users.models import User

pytestmark = pytest.mark.django_db

VERIFY_URL = "/api/v1/auth/verify-email/"


def _user(username, email):
    return User.objects.create_user(username=username, email=email, password="password")


def test_the_login_ignores_the_case_of_the_email(user):
    response = APIClient().post("/api/token/", {"email": "TESTER@Test.COM", "password": "password"}, format="json")

    assert response.status_code == 200


def test_the_domain_is_stored_in_lower_case():
    created = _user("mixto", "Mixto@Ejemplo.COM")
    created.email = "Otro@Dominio.ORG"
    created.save()

    assert User.objects.get(pk=created.pk).email == "Otro@dominio.org"


def test_two_accounts_cannot_share_an_email_by_case(user):
    with pytest.raises(IntegrityError), transaction.atomic():
        User.objects.filter(pk=_user("otro", "otro@test.com").pk).update(email="TESTER@test.com")


def _pending(user, email):
    User.objects.filter(pk=user.pk).update(pending_email=email)
    user.refresh_from_db()
    return make_token(user)


def _confirm(user, token):
    api = APIClient()
    api.force_authenticate(user=user)
    return api.post(VERIFY_URL, {"token": token}, format="json")


def test_two_confirmations_of_the_same_email_in_a_race_end_in_a_400(user):
    other = _user("otro", "otro@test.com")
    first, second = _pending(user, "Bob@test.com"), _pending(other, "bob@test.com")
    assert _confirm(user, first).status_code == 200

    # The second request read "free" before the first one committed: the constraint decides.
    with mock.patch("apps.users.views.email_taken", return_value=False):
        response = _confirm(other, second)

    assert response.status_code == 400
    assert response.json() == {"token": ["Ya existe una cuenta con este email."]}
    other.refresh_from_db()
    assert (other.email, other.pending_email) == ("otro@test.com", "bob@test.com")
    assert mail.outbox == []


# Postgres's UPPER maps "ſ" (long s) to "S" and "ı" (dotless i) to "I"; Python's lower() keeps them.
UNICODE_VARIANTS = ["teſter@test.com", "tester@teſt.com", "TEſTER@TEST.COM"]


@pytest.mark.parametrize("variant", UNICODE_VARIANTS)
def test_a_unicode_case_variant_cannot_be_a_second_account(user, variant):
    with pytest.raises(IntegrityError), transaction.atomic():
        User.objects.filter(pk=_user("otro", "otro@test.com").pk).update(email=variant)


def test_a_dotless_i_variant_cannot_be_a_second_account():
    _user("kiss", "kiss@test.com")

    with pytest.raises(IntegrityError), transaction.atomic():
        _user("kiss2", "kıss@test.com")


def test_characters_upper_does_not_fold_stay_separate_accounts_and_the_login_works():
    # The Kelvin sign lowers to "k" but UPPER keeps it: for the login it is another email.
    _user("kiss", "kiss@test.com")
    kelvin = _user("kelvin", "Kiss@test.com")

    response = APIClient().post("/api/token/", {"email": "kiss@test.com", "password": "password"}, format="json")

    assert response.status_code == 200
    assert kelvin.pk != User.objects.get(email="kiss@test.com").pk


def test_a_variant_of_another_account_is_taken_for_an_email_change(user):
    other = _user("otro", "otro@test.com")
    api = APIClient()
    api.force_authenticate(user=other)

    response = api.patch("/api/v1/me/", {"email": "teſter@test.com", "current_password": "password"}, format="json")

    assert response.status_code == 400
    assert response.json() == {"email": ["Ya existe una cuenta con este email."]}
