"""Simultaneous failed logins cannot all slip under the per-account limit."""
import threading
from unittest import mock

from django.db import connection
from django.test import TransactionTestCase, override_settings
from rest_framework.test import APIClient
from rest_framework.throttling import ScopedRateThrottle

from apps.core.testing import clear_local_caches
from apps.users.models import User


@override_settings(LOGIN_FAILURE_RATES={"account_ip": "50/15m", "account": "3/h"})
class ConcurrentLoginFailuresTests(TransactionTestCase):
    def setUp(self):
        clear_local_caches("default")
        User.objects.create_user(username="c", email="c@test.com", password="password")

    def test_only_the_limit_reaches_the_password_check(self):
        barrier = threading.Barrier(12)
        answers = []

        def attempt(n):
            try:
                barrier.wait(timeout=10)
                answers.append(
                    APIClient(REMOTE_ADDR=f"10.6.0.{n}")
                    .post("/api/token/", {"email": "c@test.com", "password": "x"}, format="json")
                    .status_code
                )
            finally:
                connection.close()

        with mock.patch.dict(ScopedRateThrottle.THROTTLE_RATES, {"login": "1000/min"}):
            threads = [threading.Thread(target=attempt, args=(n,)) for n in range(12)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

        assert sorted(answers) == [401] * 3 + [429] * 9
