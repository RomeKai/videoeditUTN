"""Shared setup of the subscription and payment tests."""
from rest_framework.test import APIClient

from apps.users.models import User, Workspace, WorkspaceMember

# The simulated provider, allowed without DEBUG (backend.settings.ci has DEBUG off).
SIMULATED = {'PAYMENTS_PROVIDER': 'simulated', 'PAYMENTS_ALLOW_SIMULATED': True}
NO_PROVIDER = {'PAYMENTS_PROVIDER': '', 'PAYMENTS_ALLOW_SIMULATED': False}

FRONT = 'http://localhost:3000'


def make_user(name):
    return User.objects.create_user(username=name, email=f'{name}@example.com', password='x')


def owner_with_workspace(name='duena'):
    """A user and the workspace the sign-up signal made for them (on the default plan, Gratis)."""
    user = make_user(name)
    return user, Workspace.objects.get(owner=user)


def join(workspace, user, role=WorkspaceMember.Role.EDITOR, status=WorkspaceMember.Status.ACTIVE):
    return WorkspaceMember.objects.create(workspace=workspace, user=user, role=role, status=status)


def client_for(user, workspace=None):
    client = APIClient()
    client.force_authenticate(user)
    if workspace is not None:
        client.credentials(HTTP_X_WORKSPACE_ID=str(workspace.pk))
    return client


def set_plan(workspace, plan, renews_at=None):
    workspace.subscription_plan = plan
    workspace.plan_renews_at = renews_at
    workspace.save(update_fields=['subscription_plan', 'plan_renews_at'])
