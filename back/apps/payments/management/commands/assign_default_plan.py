"""
Lists the workspaces without a plan and, with ``--apply``, puts them on the default plan.

Why they exist
    Until the default plan existed, ``create_default_workspace`` looked for a
    plan nobody created, so every workspace started with ``subscription_plan``
    NULL. Without a plan ``PricingEngine`` checks nothing (duration, 4K, SEO)
    and no member can be invited. New workspaces start on
    ``settings.DEFAULT_PLAN_NAME`` (Gratis); the old ones keep NULL until this
    command runs. A migration does not do it on purpose: it would put limits on
    live accounts in the middle of a deploy.

Safety
    Dry run by default (``--dry-run`` says it explicitly): nothing is written.
    With ``--apply`` each workspace is updated only if it still has no plan, so
    a plan assigned meanwhile (in the admin, for example) is kept. Members are
    never removed: a workspace with more members than the plan allows keeps
    them (``over_member_limit=yes``), but cannot add more.

Output
    One line per workspace with ids and numbers only, never its name (it holds
    the username): ``workspace``, ``owner``, ``members``, ``created``,
    ``over_member_limit`` and ``result`` (``dry_run``, ``assigned`` or
    ``skipped``). Then a ``summary`` line. Without the default plan the command
    lists the workspaces and ends with an error (code 1).
"""

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Count

from apps.payments.services.plans import get_default_plan
from apps.users.models import Workspace


class Command(BaseCommand):
    help = (
        "Lists the workspaces without a plan and, with --apply, assigns them "
        "the plan named by DEFAULT_PLAN_NAME."
    )

    def add_arguments(self, parser):
        mode = parser.add_mutually_exclusive_group()
        mode.add_argument(
            "--dry-run", action="store_true",
            help="Only list the workspaces (the default).",
        )
        mode.add_argument(
            "--apply", action="store_true",
            help="Assign the default plan to the workspaces listed.",
        )

    def handle(self, *args, **options):
        apply = options["apply"]
        plan = get_default_plan()

        workspaces = list(
            Workspace.objects.filter(subscription_plan__isnull=True)
            .annotate(member_count=Count("members"))
            .order_by("created_at", "id")
            .values("id", "owner_id", "member_count", "created_at")
        )

        assigned = skipped = over_limit = 0
        for row in workspaces:
            over = plan is not None and row["member_count"] > plan.max_members
            over_limit += over
            result = "dry_run"
            if apply and plan is not None:
                updated = Workspace.objects.filter(pk=row["id"], subscription_plan__isnull=True).update(
                    subscription_plan=plan
                )
                result = "assigned" if updated else "skipped"
                assigned += updated
                skipped += not updated
            self.stdout.write(
                f"workspace={row['id']} owner={row['owner_id']} members={row['member_count']} "
                f"created={row['created_at']:%Y-%m-%d} over_member_limit={'yes' if over else 'no'} "
                f"result={result}"
            )

        self.stdout.write(
            f"summary mode={'apply' if apply else 'dry_run'} plan={plan.pk if plan else 'missing'} "
            f"without_plan={len(workspaces)} assigned={assigned} skipped={skipped} "
            f"over_member_limit={over_limit}"
        )
        if plan is None:
            raise CommandError(
                "No plan is named like settings.DEFAULT_PLAN_NAME: run the payments migrations "
                "or fix the setting. Nothing was assigned."
            )
