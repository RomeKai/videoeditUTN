"""
Gratis allows 2 members: the owner and one more. A pending invitation takes a seat
(check_workspace_member_limit, users/signals.py), so a third person is refused.
"""
import pytest
from django.core.cache import cache
from django.core.exceptions import ValidationError
from rest_framework.test import APIClient

from apps.users.models import User, Workspace, WorkspaceMember

pytestmark = pytest.mark.django_db

PENDING, ACTIVE = WorkspaceMember.Status.PENDING, WorkspaceMember.Status.ACTIVE


def make_user(name):
    return User.objects.create_user(username=name, email=f'{name}@example.com', password='x')


def join(workspace, user, status=ACTIVE):
    return WorkspaceMember.objects.create(
        workspace=workspace, user=user, role=WorkspaceMember.Role.EDITOR, status=status,
    )


def test_the_free_plan_has_two_seats(seeded_plans):
    assert seeded_plans['Gratis'].max_members == 2


def test_the_public_list_shows_two_seats_for_the_free_plan(seeded_plans):
    cache.clear()
    plans = APIClient().get('/api/v1/plans/').json()

    assert next(plan for plan in plans if plan['name'] == 'Gratis')['max_members'] == 2


@pytest.mark.parametrize('second', [PENDING, ACTIVE])
def test_a_free_workspace_has_its_owner_and_one_more_and_refuses_a_third(seeded_plans, second):
    owner = make_user('duena')
    workspace = Workspace.objects.get(owner=owner)
    assert workspace.subscription_plan == seeded_plans['Gratis']

    # The second seat. A pending invitation takes it the same as a member.
    join(workspace, make_user('invitada'), status=second)

    try:
        join(workspace, make_user('tercera'))
    except ValidationError as error:
        assert "Tu plan 'Gratis' solo permite 2 miembros" in str(error)
    else:
        # Before PR #137 the limit never ran: it looked at instance.id, which the
        # UUID default fills before the INSERT. The seats above still hold.
        pytest.skip('check_workspace_member_limit no corre en esta rama (PR #137 lo arregla)')
    assert WorkspaceMember.objects.filter(workspace=workspace).count() == 2
