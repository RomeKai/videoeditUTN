"""
The billing_* bodies of front/src/api/contracts/drf/responses.json (the
subscription, the token packages and the payments) are what this backend
answers: the frontend parses them with its zod schemas (drf.test.ts), and this
test checks they still have the shape of the real responses (the same keys,
and text, number, boolean or null in the same places).

When a response changes on purpose, copy the new bodies into the file:
``PRINT_DRF_FIXTURE=1 python -m pytest apps/payments/tests/test_billing_contract_fixture.py -s``
prints them.
"""
import json
import os
from pathlib import Path

import pytest
from django.test import override_settings

from apps.users.models import WorkspaceMember

from .billing_helpers import FRONT, SIMULATED, client_for, join, make_user, owner_with_workspace

pytestmark = pytest.mark.django_db

FIXTURE = Path(__file__).resolve().parents[4] / 'front' / 'src' / 'api' / 'contracts' / 'drf' / 'responses.json'


def shape(value):
    """What the zod schemas read of a body: keys, and the JSON type of each value."""
    if isinstance(value, dict):
        return {key: shape(item) for key, item in value.items()}
    if isinstance(value, list):
        return [shape(value[0])] if value else []
    return type(value).__name__


@pytest.fixture
def real_bodies(seeded_plans):
    plans = seeded_plans
    owner, workspace = owner_with_workspace()
    client = client_for(owner)
    bodies, errors = {}, {}
    with override_settings(**SIMULATED):
        bodies['subscription_free'] = client.get('/api/v1/subscription/').json()
        bodies['token_packages'] = client.get('/api/v1/token-packages/').json()
        purchase = client.post('/api/v1/token-purchases/', {'package_id': 'pack-1000', 'return_url': f'{FRONT}/uso/comprar/resultado'}, format='json').json()
        bodies['token_purchase_pending'] = purchase
        bodies['token_purchase_approved'] = client.post(f"/api/v1/token-purchases/{purchase['id']}/simulate/", {'approved': True}, format='json').json()
        other = client.post('/api/v1/token-purchases/', {'custom_tokens': 2500, 'return_url': f'{FRONT}/uso/comprar/resultado'}, format='json').json()
        bodies['token_purchase_rejected'] = client.post(f"/api/v1/token-purchases/{other['id']}/simulate/", {'approved': False}, format='json').json()
        checkout = client.post('/api/v1/subscription/change/', {'plan_id': str(plans['Pro'].pk), 'return_url': f'{FRONT}/uso/planes'}, format='json').json()
        bodies['change_plan_checkout'] = checkout
        payment_id = checkout['checkout_url'].split('compra=')[1].split('&')[0]
        bodies['plan_payment_pending'] = client.get(f'/api/v1/token-purchases/{payment_id}/').json()
        bodies['plan_payment_approved'] = client.post(f'/api/v1/token-purchases/{payment_id}/simulate/', {'approved': True}, format='json').json()
        bodies['change_plan_period_end'] = client.post('/api/v1/subscription/change/', {'plan_id': str(plans['Gratis'].pk)}, format='json').json()
        bodies['change_plan_cancel'] = client.post('/api/v1/subscription/change/', {'plan_id': str(plans['Pro'].pk)}, format='json').json()

        def error(name, response):
            errors[name] = {'status': response.status_code, 'body': response.json()}

        error('change_plan_400_current', client.post('/api/v1/subscription/change/', {'plan_id': str(plans['Pro'].pk)}, format='json'))
        # «Seguir con …»: what Planes showed (a downgrade to Gratis and a payment for Estudio), canceled.
        client.post('/api/v1/subscription/change/', {'plan_id': str(plans['Gratis'].pk)}, format='json')
        estudio = client.post('/api/v1/subscription/change/', {'plan_id': str(plans['Estudio'].pk), 'return_url': f'{FRONT}/uso/planes'}, format='json').json()
        shown = estudio['subscription']
        keep = {'current_plan_id': shown['plan']['id'], 'payment_id': shown['pending_payment']['id'], 'pending_plan_id': shown['pending_plan']['id']}
        bodies['cancel_pending'] = client.post('/api/v1/subscription/cancel-pending/', keep, format='json').json()
        error('cancel_pending_409_nothing', client.post('/api/v1/subscription/cancel-pending/', keep, format='json'))
        # A stale screen: it still shows Gratis, from before the payment for Pro was approved.
        stale = {'current_plan_id': str(plans['Gratis'].pk), 'payment_id': shown['pending_payment']['id']}
        error('cancel_pending_409_plan_changed', client.post('/api/v1/subscription/cancel-pending/', stale, format='json'))
        # A payment the screen did not show (started in another tab) is never canceled.
        other = client.post('/api/v1/subscription/change/', {'plan_id': str(plans['Estudio'].pk), 'return_url': f'{FRONT}/uso/planes'}, format='json').json()
        unseen = {'current_plan_id': str(plans['Pro'].pk), 'pending_plan_id': str(plans['Gratis'].pk)}
        error('cancel_pending_409_pending_changed', client.post('/api/v1/subscription/cancel-pending/', unseen, format='json'))
        client.post('/api/v1/subscription/cancel-pending/', {**unseen, 'payment_id': other['subscription']['pending_payment']['id']}, format='json')
        workspace.refresh_from_db()  # on Pro now: room for the invitations
        for name in ('uno', 'dos', 'tres'):
            join(workspace, make_user(name), status=WorkspaceMember.Status.PENDING)
        error('change_plan_409_seats', client.post('/api/v1/subscription/change/', {'plan_id': str(plans['Gratis'].pk)}, format='json'))
        error('purchase_400_min', client.post('/api/v1/token-purchases/', {'custom_tokens': 50, 'return_url': f'{FRONT}/uso/comprar/resultado'}, format='json'))
        editor = make_user('editora')
        join(workspace, editor, role=WorkspaceMember.Role.EDITOR)
        error('change_plan_403_role', client_for(editor, workspace).post('/api/v1/subscription/change/', {'plan_id': str(plans['Gratis'].pk)}, format='json'))
    with override_settings(PAYMENTS_PROVIDER=''):
        error('purchase_501_no_provider', client.post('/api/v1/token-purchases/', {'package_id': 'pack-1000', 'return_url': f'{FRONT}/uso/comprar/resultado'}, format='json'))
    return bodies, errors


def test_the_billing_bodies_of_the_fixture_have_the_shape_of_the_real_ones(real_bodies):
    bodies, errors = real_bodies
    if os.environ.get('PRINT_DRF_FIXTURE'):
        print(json.dumps({'bodies': bodies, 'errors': errors}, ensure_ascii=False, indent=2))
    fixture = json.loads(FIXTURE.read_text(encoding='utf-8'))

    for name, body in bodies.items():
        assert shape(fixture[f'billing_{name}']) == shape(body), name
    for name, error in errors.items():
        assert fixture['errors'][f'billing_{name}'] == error, name
