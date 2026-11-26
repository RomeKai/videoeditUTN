"""
assign_default_plan: lists the workspaces without a plan (dry run by default) and,
with --apply, puts them on the default plan. No migration does it: it would put
limits on live accounts in the middle of a deploy.
"""
from io import StringIO

import pytest
from django.core.management import CommandError, call_command
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.payments.models import SubscriptionPlan
from apps.users.models import User, Workspace, WorkspaceMember

pytestmark = pytest.mark.django_db


def workspace_of(username):
    user = User.objects.create_user(username=username, email=f'{username}@example.com', password='x')
    return Workspace.objects.get(owner=user)


def without_plan(*workspaces):
    Workspace.objects.filter(pk__in=[workspace.pk for workspace in workspaces]).update(subscription_plan=None)


def run(*args):
    out = StringIO()
    call_command('assign_default_plan', *args, stdout=out)
    return out.getvalue()


def plan_of(workspace):
    return Workspace.objects.get(pk=workspace.pk).subscription_plan


@pytest.fixture
def old_workspaces(seeded_plans):
    """Two workspaces from before the default plan, and one already on Pro."""
    first, second, on_pro = workspace_of('antiguo1'), workspace_of('antiguo2'), workspace_of('conpro')
    without_plan(first, second)
    Workspace.objects.filter(pk=on_pro.pk).update(subscription_plan=seeded_plans['Pro'])
    return first, second, on_pro


@pytest.mark.parametrize('args', [(), ('--dry-run',)])
def test_the_dry_run_lists_the_workspaces_without_a_plan_and_writes_nothing(old_workspaces, args):
    first, second, on_pro = old_workspaces

    output = run(*args)

    assert f'workspace={first.pk}' in output
    assert f'workspace={second.pk}' in output
    assert f'workspace={on_pro.pk}' not in output
    assert 'summary mode=dry_run' in output
    assert 'without_plan=2 assigned=0' in output
    assert plan_of(first) is None and plan_of(second) is None


def test_apply_puts_them_on_the_free_plan_and_keeps_the_others(old_workspaces, seeded_plans):
    first, second, on_pro = old_workspaces

    output = run('--apply')

    assert plan_of(first) == seeded_plans['Gratis']
    assert plan_of(second) == seeded_plans['Gratis']
    assert plan_of(on_pro) == seeded_plans['Pro']
    assert 'summary mode=apply' in output
    assert 'without_plan=2 assigned=2 skipped=0' in output
    assert run().count('workspace=') == 0  # nothing left


def test_a_workspace_at_the_free_plan_seats_is_not_flagged(seeded_plans):
    pair = workspace_of('pareja')
    guest = User.objects.create_user(username='otra', email='otra@example.com', password='x')
    WorkspaceMember.objects.create(
        workspace=pair, user=guest, role=WorkspaceMember.Role.EDITOR, status=WorkspaceMember.Status.PENDING,
    )
    without_plan(pair)

    assert f'workspace={pair.pk} owner={pair.owner_id} members=2 ' in run()
    assert 'over_member_limit=no' in run()


def test_the_output_has_ids_and_numbers_but_no_workspace_name(old_workspaces):
    first, _, _ = old_workspaces

    output = run()

    assert first.name not in output  # "Espacio de <username>"
    assert f'owner={first.owner_id}' in output
    assert 'members=1' in output


def test_a_workspace_with_more_members_than_the_plan_allows_is_flagged(seeded_plans):
    team = workspace_of('equipo')
    # Members join while it is on a plan that allows them, then it loses the plan.
    Workspace.objects.filter(pk=team.pk).update(subscription_plan=seeded_plans['Estudio'])
    team.refresh_from_db()
    for name in ('invitado1', 'invitado2'):  # the owner and two more: Gratis has 2 seats
        guest = User.objects.create_user(username=name, email=f'{name}@example.com', password='x')
        WorkspaceMember.objects.create(
            workspace=team, user=guest, role=WorkspaceMember.Role.EDITOR, status=WorkspaceMember.Status.ACTIVE,
        )
    without_plan(team)

    output = run('--apply')

    assert f'workspace={team.pk} owner={team.owner_id} members=3' in output
    assert 'over_member_limit=yes' in output
    assert WorkspaceMember.objects.filter(workspace=team).count() == 3  # nobody is removed
    assert plan_of(team) == seeded_plans['Gratis']


def test_without_the_default_plan_it_lists_and_fails(old_workspaces, settings):
    first, _, _ = old_workspaces
    settings.DEFAULT_PLAN_NAME = 'No existe'

    out = StringIO()
    with pytest.raises(CommandError):
        call_command('assign_default_plan', '--apply', stdout=out)

    assert f'workspace={first.pk}' in out.getvalue()
    assert 'plan=missing' in out.getvalue()
    assert plan_of(first) is None


def test_dry_run_and_apply_cannot_go_together(old_workspaces):
    with pytest.raises(CommandError):
        run('--dry-run', '--apply')


def test_apply_does_not_run_one_update_per_workspace(seeded_plans):
    """The queries of --apply do not grow with the number of workspaces."""
    def queries_to_apply(count, prefix):
        without_plan(*[workspace_of(f'{prefix}{index}') for index in range(count)])
        with CaptureQueriesContext(connection) as context:
            run('--apply')
        return len(context.captured_queries)

    assert queries_to_apply(2, 'pocos') == queries_to_apply(6, 'muchos')


def test_apply_goes_in_batches(seeded_plans, monkeypatch):
    from apps.payments.management.commands import assign_default_plan

    monkeypatch.setattr(assign_default_plan, 'BATCH_SIZE', 2)
    workspaces = [workspace_of(f'lote{index}') for index in range(5)]
    without_plan(*workspaces)

    output = run('--apply')

    assert output.count('result=assigned') == 5
    assert 'without_plan=5 assigned=5 skipped=0' in output
    assert all(plan_of(workspace) == seeded_plans['Gratis'] for workspace in workspaces)


def test_an_inactive_default_plan_is_not_assigned(old_workspaces, seeded_plans):
    first, _, _ = old_workspaces
    SubscriptionPlan.objects.filter(name='Gratis').update(is_active=False)

    with pytest.raises(CommandError):
        run('--apply')

    assert plan_of(first) is None
