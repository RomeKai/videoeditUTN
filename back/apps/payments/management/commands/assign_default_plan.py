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
    With ``--apply`` one UPDATE per batch of ``BATCH_SIZE`` workspaces assigns
    the plan only where there is still none, so a plan assigned meanwhile (in
    the admin, for example) is kept and reported as ``skipped``. Members are
    never removed: a workspace with more members than the plan allows keeps
    them (``over_member_limit=yes``), but cannot add more. Without the default
    plan (missing or inactive) nothing is assigned.

Output
    One line per workspace with ids and numbers only, never its name (it holds
    the username): ``workspace``, ``owner``, ``members``, ``created``,
    ``over_member_limit`` and ``result`` (``dry_run``, ``assigned`` or
    ``skipped``). Then a ``summary`` line. Without the default plan the command
    lists the workspaces and ends with an error (code 1).
"""

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Count

from apps.payments.models import SubscriptionPlan
from apps.payments.services.plans import get_default_plan_id
from apps.users.models import Workspace

# Workspaces per UPDATE: bounds the `IN (...)` list and the time a batch holds its row locks.
BATCH_SIZE = 500


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
        plan_id = get_default_plan_id()
        plan = SubscriptionPlan.objects.filter(pk=plan_id).first() if plan_id else None

        workspaces = list(
            Workspace.objects.filter(subscription_plan__isnull=True)
            .annotate(member_count=Count("members"))
            .order_by("created_at", "id")
            .values("id", "owner_id", "member_count", "created_at")
        )

        assigned_ids = set()
        if apply and plan is not None:
            ids = [row["id"] for row in workspaces]
            for start in range(0, len(ids), BATCH_SIZE):
                batch = ids[start:start + BATCH_SIZE]
                Workspace.objects.filter(pk__in=batch, subscription_plan__isnull=True).update(subscription_plan=plan)
                # The ones of the batch on the plan now; any other got a plan meanwhile.
                assigned_ids.update(
                    Workspace.objects.filter(pk__in=batch, subscription_plan=plan).values_list("pk", flat=True)
                )

        over_limit = 0
        for row in workspaces:
            over = plan is not None and row["member_count"] > plan.max_members
            over_limit += over
            if not apply or plan is None:
                result = "dry_run"
            else:
                result = "assigned" if row["id"] in assigned_ids else "skipped"
            self.stdout.write(
                f"workspace={row['id']} owner={row['owner_id']} members={row['member_count']} "
                f"created={row['created_at']:%Y-%m-%d} over_member_limit={'yes' if over else 'no'} "
                f"result={result}"
            )

        assigned = len(assigned_ids)
        skipped = len(workspaces) - assigned if apply and plan is not None else 0
        self.stdout.write(
            f"summary mode={'apply' if apply else 'dry_run'} plan={plan.pk if plan else 'missing'} "
            f"without_plan={len(workspaces)} assigned={assigned} skipped={skipped} "
            f"over_member_limit={over_limit}"
        )
        if plan is None:
            raise CommandError(
                "No active plan is named like settings.DEFAULT_PLAN_NAME: run the payments migrations "
                "or fix the setting. Nothing was assigned."
            )
