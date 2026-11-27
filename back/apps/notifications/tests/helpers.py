from contextlib import contextmanager
from unittest.mock import patch

from django.contrib.auth import get_user_model

from apps.notifications.models import Notification
from apps.payments.models import SubscriptionPlan
from apps.users.models import Workspace, WorkspaceMember

User = get_user_model()

ACTIVE, PENDING = WorkspaceMember.Status.ACTIVE, WorkspaceMember.Status.PENDING
ADMIN, EDITOR, VIEWER = WorkspaceMember.Role.ADMIN, WorkspaceMember.Role.EDITOR, WorkspaceMember.Role.VIEWER
CREATE = 'apps.notifications.services.Notification.objects.create'


class BellIsBroken(Exception):
    """What the notifications table answers in the tests of a failing notification."""


def make_user(name):
    return User.objects.create_user(username=name, email=f'{name}@test.com', password='pw')


def join(workspace, user, role=EDITOR, status=ACTIVE):
    return WorkspaceMember.objects.create(workspace=workspace, user=user, role=role, status=status)


def team(owner, name='Equipo'):
    """A workspace of ``owner`` on a plan with room for members."""
    plan = SubscriptionPlan.objects.create(
        name=f'Plan de {name}', max_resolution='1080p', max_members=10, has_watermark=False,
    )
    return Workspace.objects.create(name=name, owner=owner, subscription_plan=plan)


def sent(workspace=None):
    """``{username: [(kind, title, body), ...]}`` of every notification, oldest first."""
    rows = Notification.objects.select_related('user').order_by('created_at', 'pk')
    if workspace is not None:
        rows = rows.filter(workspace=workspace)
    found = {}
    for row in rows:
        found.setdefault(row.user.username, []).append((row.kind, row.title, row.body))
    return found


@contextmanager
def broken_bell():
    """Writing a notification fails."""
    with patch(CREATE, side_effect=BellIsBroken('texto que no va al log')) as create:
        yield create
