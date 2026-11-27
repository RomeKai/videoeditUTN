"""
The social-accounts bodies of front/src/api/contracts/drf/responses.json
(`social_accounts*`, `social_connect` and the `social_*` errors) are what this
backend answers, with the provider replaced: the frontend parses them with its
zod schemas (drf.test.ts). Only the ids are normalised.

When a response changes on purpose, copy the new bodies into the file:
``PRINT_DRF_FIXTURE=1 python -m pytest apps/videos/tests/test_social_accounts_contract_fixture.py -s``
"""
import json
import os
from datetime import datetime, timezone as dt_timezone
from unittest.mock import patch

import pytest
from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse
from rest_framework.test import APIClient, APITestCase

from apps.integrations.social import Account, CreatedProfile, RetryableError
from apps.payments.models import SubscriptionPlan
from apps.payments.tests.test_contract_fixture import FIXTURE, normalised
from apps.users.models import User, Workspace, WorkspaceMember

PROVIDER = "apps.integrations.social.ayrshare.AyrshareProvider"
BODIES = ('social_accounts_none', 'social_accounts', 'social_connect')
ERRORS = (
    'social_400_return_url', 'social_403_connect', 'social_403_plan', 'social_403_email', 'social_409_profiles_limit',
    'social_503_connect',
)
LINK = "https://profile.ayrshare.com?session=ayr_ls_kJ8mQ2vX9pLn"


@pytest.mark.skipif(not FIXTURE.exists(), reason='front/ is not next to back/ (an image built from back/ alone)')
@override_settings(FRONTEND_URL='https://app.onecreator.test')
class SocialAccountsFixtureTests(APITestCase):
    maxDiff = None

    def real_bodies(self):
        cache.clear()
        ana = User.objects.create_user(username='ana', email='ana@test.com', password='pw', is_email_verified=True)
        home = Workspace.objects.create(name='Estudio Norte', owner=ana, subscription_plan=SubscriptionPlan.objects.get(name='Pro'))
        carla = User.objects.create_user(username='carla', email='carla@test.com', password='pw')
        WorkspaceMember.objects.create(workspace=home, user=carla, role='editor', status='active')
        def call(method, url, body=None, user=ana, workspace=home):
            api = APIClient()
            api.force_authenticate(user)
            return getattr(api, method)(url, body or {}, format='json', HTTP_X_WORKSPACE_ID=str(workspace.pk))

        def err(response, status):
            assert response.status_code == status, (response.status_code, response.content)
            return {'status': status, 'body': response.json()}

        none = call('get', reverse('social-account-list')).json()
        with patch(f'{PROVIDER}.create_profile', return_value=CreatedProfile('KEY', 'ref-1')), \
                patch(f'{PROVIDER}.connect_url', return_value=LINK):
            connect = call('post', reverse('social-account-connect'), {
                'platform': 'TIKTOK', 'return_url': 'https://app.onecreator.test/ajustes/redes',
            }).json()
        linked = [
            Account('TIKTOK', 'connected', '@estudionorte', datetime(2026, 11, 2, 2, 11, 53, tzinfo=dt_timezone.utc)),
            Account('INSTAGRAM_REELS', 'expired', '@estudio.norte', datetime(2026, 10, 9, 20, 36, 58, tzinfo=dt_timezone.utc)),
            Account('YOUTUBE_SHORTS', 'disconnected'),
        ]
        with patch(f'{PROVIDER}.accounts', return_value=linked):
            accounts = call('get', reverse('social-account-list')).json()
        errors = {
            'social_400_return_url': err(call('post', reverse('social-account-connect'), {
                'platform': 'TIKTOK', 'return_url': 'https://otro.test/ajustes',
            }), 400),
            'social_403_connect': err(call('post', reverse('social-account-connect'), {'platform': 'TIKTOK'}, user=carla), 403),
        }
        with patch(f'{PROVIDER}.connect_url', side_effect=RetryableError(detail='ayrshare http=502')):
            errors['social_503_connect'] = err(call('post', reverse('social-account-connect'), {'platform': 'INSTAGRAM_REELS'}), 503)
        free = Workspace.objects.create(name='Gratis', owner=ana, subscription_plan=SubscriptionPlan.objects.get(name='Gratis'))
        errors['social_403_plan'] = err(call('post', reverse('social-account-connect'), {'platform': 'TIKTOK'}, workspace=free), 403)
        lucia = User.objects.create_user(username='lucia', email='lucia@test.com', password='pw')
        WorkspaceMember.objects.create(workspace=home, user=lucia, role='admin', status='active')
        errors['social_403_email'] = err(call('post', reverse('social-account-connect'), {'platform': 'TIKTOK'}, user=lucia), 403)
        other = Workspace.objects.create(name='Otro', owner=ana, subscription_plan=SubscriptionPlan.objects.get(name='Pro'))
        with override_settings(SOCIAL_MAX_PROFILES=1):
            errors['social_409_profiles_limit'] = err(
                call('post', reverse('social-account-connect'), {'platform': 'TIKTOK'}, workspace=other), 409,
            )
        return {'social_accounts_none': none, 'social_accounts': accounts, 'social_connect': connect, 'errors': errors}

    def test_the_fixture_has_the_values_of_the_real_responses(self):
        real = self.real_bodies()
        if os.environ.get('PRINT_DRF_FIXTURE'):
            print(json.dumps(real, ensure_ascii=False, indent=2))
        fixture = json.loads(FIXTURE.read_text(encoding='utf-8'))

        def part(source):
            return {
                **{name: source.get(name) for name in BODIES},
                'errors': {name: source['errors'].get(name) for name in ERRORS},
            }

        expected, actual = normalised(part(fixture)), normalised(part(real))
        for name in BODIES:
            with self.subTest(body=name):
                self.assertEqual(expected[name], actual[name])
        for name in ERRORS:
            with self.subTest(error=name):
                self.assertEqual(expected['errors'][name], actual['errors'][name])
