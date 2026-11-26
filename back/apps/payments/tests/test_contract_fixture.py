"""
The wallet bodies of front/src/api/contracts/drf/responses.json are what this
backend answers: the frontend parses them with its zod schemas (drf.test.ts),
and this test checks they still have the shape of the real responses (the
same keys, and text, number, boolean or null in the same places).

When a response changes on purpose, copy the new bodies into the file:
``PRINT_DRF_FIXTURE=1 python -m pytest apps/payments/tests/test_contract_fixture.py -s``
prints them (``real_bodies``).
"""
import json
import os
from decimal import Decimal
from pathlib import Path

import pytest
from django.urls import reverse
from rest_framework.test import APITestCase

from apps.payments.models import Transaction, Wallet
from apps.payments.services import wallet_service
from apps.payments.tests.test_wallet_api import (
    COMPLETED, DEPOSIT, FAILED, PENDING, RENDER, RESERVED, WalletHarness, local, movement, plan,
)
from apps.users.models import WorkspaceMember
from apps.videos.tests.test_active_workspace import EDITOR, header, join

FIXTURE = Path(__file__).resolve().parents[4] / 'front' / 'src' / 'api' / 'contracts' / 'drf' / 'responses.json'
BODIES = (
    'pricing', 'estimate_file', 'estimate_link', 'wallet', 'wallet_usage', 'transactions_page', 'transactions_export',
)
ERRORS = ('estimate_403_plan', 'estimate_400_link', 'transactions_403_role', 'usage_400_month')


def shape(value):
    """What the zod schemas read of a body: keys, and the JSON type of each value."""
    if isinstance(value, dict):
        return {key: shape(item) for key, item in value.items()}
    if isinstance(value, list):
        return [shape(value[0])] if value else []
    return type(value).__name__ if value is not None else 'null'


class ContractFixtureHarness(WalletHarness):
    def real_bodies(self):
        """The bodies of every wallet endpoint, and their errors, for one workspace with movements."""
        self.home.subscription_plan = plan(max_video_duration_seconds=600)
        self.home.save(update_fields=['subscription_plan'])
        interview = self.project(title='Entrevista con Ana')
        podcast = self.project(title='Podcast 12')
        movement(self.home, '5000', DEPOSIT, at=local(2026, 10, 10), description='Compra de 5000 tokens', user=self.alice)
        movement(
            self.home, '12.5', at=local(2026, 10, 11), settled=local(2026, 10, 11, 12, 20), project=interview, user=self.alice,
            description='Procesamiento: Entrevista con Ana',
        )
        movement(
            self.home, '7.25', status=FAILED, at=local(2026, 10, 12), project=podcast, category=RENDER,
            description='Render Final (Paper Edit): Podcast 12', reserved='7.25',
        )
        movement(self.home, '1000', DEPOSIT, PENDING, at=local(2026, 10, 13), description='Compra de 1000 tokens')
        wallet = Wallet.objects.get(workspace=self.home)
        wallet_service.reserve_funds(
            wallet.id, Decimal('3.5'), user=self.alice, description='Render de clip: Gancho',
            project=interview, category=Transaction.Category.RENDER,
        )

        def body(response, status=200):
            self.assertEqual(response.status_code, status, response.content[:300])
            return response.json()

        ws = header(self.home)
        bodies = {
            'pricing': body(self.client.get(reverse('pricing'))),
            'estimate_file': body(self.client.post(
                reverse('pricing-estimate'), {'source': 'file', 'duration_seconds': 181.9, 'render_resolution': '1080p', 'use_seo': True},
                format='json', **ws,
            )),
            'estimate_link': body(self.client.post(reverse('pricing-estimate'), {'source': 'link'}, format='json', **ws)),
            'wallet': body(self.client.get(reverse('wallet'), **ws)),
            'wallet_usage': body(self.client.get(reverse('wallet-usage'), {'month': '2026-10'}, **ws)),
            'transactions_page': body(self.client.get(reverse('transaction-list'), {'page_size': 3}, **ws)),
            'transactions_export': self.client.get(reverse('transaction-export'), {'month': '2026-10'}, **ws).content.decode(),
        }
        errors = {
            'estimate_403_plan': self.client.post(
                reverse('pricing-estimate'), {'source': 'file', 'duration_seconds': 1800}, format='json', **ws,
            ),
            'estimate_400_link': self.client.post(
                reverse('pricing-estimate'), {'source': 'link', 'render_resolution': '1080p'}, format='json', **ws,
            ),
            'usage_400_month': self.client.get(reverse('wallet-usage'), {'month': 'octubre'}, **ws),
        }
        WorkspaceMember.objects.filter(workspace=self.team, user=self.alice).delete()
        join(self.team, self.alice, EDITOR)
        errors['transactions_403_role'] = self.client.get(reverse('transaction-list'), **header(self.team))
        bodies['errors'] = {name: {'status': response.status_code, 'body': response.json()} for name, response in errors.items()}
        return bodies


@pytest.mark.skipif(not FIXTURE.exists(), reason='front/ is not next to back/ (an image built from back/ alone)')
class ContractFixtureTests(ContractFixtureHarness, APITestCase):
    def test_the_fixture_has_the_shape_of_the_real_responses(self):
        real = self.real_bodies()
        if os.environ.get('PRINT_DRF_FIXTURE'):
            print(json.dumps(real, ensure_ascii=False, indent=2))
        fixture = json.loads(FIXTURE.read_text(encoding='utf-8'))

        for name in BODIES:
            with self.subTest(body=name):
                self.assertEqual(shape(fixture[name]), shape(real[name]))
        for name in ERRORS:
            with self.subTest(error=name):
                self.assertEqual(fixture['errors'][name]['status'], real['errors'][name]['status'])
                self.assertEqual(shape(fixture['errors'][name]['body']), shape(real['errors'][name]['body']))
        self.assertEqual(fixture['transactions_export'].split('\n')[0], real['transactions_export'].split('\n')[0])
