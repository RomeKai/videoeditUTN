"""
Finds paper-edit renders whose render reservation stayed RESERVED and, with
``--apply``, recovers them: re-queued, charged (delivered but not charged) or,
after losing their worker too many times, failed with ``render_stuck`` and
the reservation returned. A render that never starts is only reported.

The rules live in tasks/render_recovery.py, shared with the Celery task that
CELERY_BEAT_SCHEDULE runs every 15 minutes. Without a beat process, run this
from cron with ``--apply`` (for example every 15 minutes). Both can run at the
same time: each project is decided again under its project lock.

Safety
    Dry run by default: without ``--apply`` nothing is written and no lock is
    taken. A project whose lock is held, or that started, was approved or was
    re-queued within RENDER_STUCK_AFTER_SECONDS, is never touched.

Output
    One line per candidate (RENDERING or COMPLETED with its render
    reservation RESERVED): ids, counters, idle minutes, the decision and the
    result. No user text. Ends with a
    ``summary`` line, and with an error (exit code 1) if a project could not
    be processed, so that cron notices.
"""

from django.core.management.base import BaseCommand, CommandError

from apps.videos.tasks.render_recovery import ERROR, recover_stuck_renders_now


def _value(row, key):
    return "-" if row.get(key) is None else row[key]


class Command(BaseCommand):
    help = (
        "Reports paper-edit renders whose tokens stayed reserved and, with --apply, "
        "re-queues, charges or fails (refunding) each one."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply", action="store_true",
            help="Recover the stuck renders. Without it the command only reports (dry run).",
        )

    def handle(self, *args, **options):
        apply = options["apply"]
        rows = recover_stuck_renders_now(apply=apply)
        totals = {}
        for row in rows:
            totals[row["action"]] = totals.get(row["action"], 0) + 1
            idle_min = row["idle_s"] / 60 if row.get("idle_s") is not None else None
            self.stdout.write(
                f"project={row['project']} unfinished_starts={_value(row, 'starts')} "
                f"requeues={_value(row, 'requeues')} "
                f"idle_min={'-' if idle_min is None else f'{idle_min:.0f}'} "
                f"action={row['action']} reason={row['reason']} result={row['result']}"
            )

        errors = totals.get(ERROR, 0)
        self.stdout.write(
            f"summary mode={'apply' if apply else 'dry_run'} candidates={len(rows)} "
            f"requeue={totals.get('requeue', 0)} charge={totals.get('charge', 0)} "
            f"fail={totals.get('fail', 0)} report={totals.get('report', 0)} "
            f"skip={totals.get('skip', 0)} errors={errors}"
        )
        if errors:
            raise CommandError(f"{errors} project(s) could not be processed.")
