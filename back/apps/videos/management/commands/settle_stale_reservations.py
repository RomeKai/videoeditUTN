"""
Finds token reservations left RESERVED and, with ``--apply``, settles them.

Why they exist
    Reservations made before charging existed, a charge or refund that failed
    on a database error, or a render whose worker died: the pipeline settles
    every reservation itself, so one still RESERVED after a day is stuck.

Rules (the same the pipeline applies, see tasks/ingestion.py and tasks/render.py)
    Creation reservation, ``metadata['reservation_id']``:
        project at clips_selected or later, or with clips  -> commit
        project failed without clips                       -> rollback
        anything else (still ingesting)                    -> report only
    Creation reservation of a whole-video project (single_edit priced for
    its whole duration), settled from where it ended, with the pipeline's own
    rule (ingestion._whole_video_settlement):
        completed, partial or failed with a clip completed or published -> commit
        completed, partial or failed with a clip still rendering        -> report only
        completed, partial or failed otherwise                          -> rollback
        any other stage (clips_selected, render_dispatched...)          -> report only
    Render reservation of an approval, ``metadata['render_reservation_id']``,
    approved clip by clip (``render_clip_ids``); auto-render reservation,
    ``metadata['auto_render_reservation_id']`` ("Renderizar sin revisar");
    and reservation of a render asked for after the project ended (a clip, a
    retry or a rerender), ``metadata['clip_render_reservation_id']``. All
    three with the pipeline's own rule (ingestion._clip_render_settlement):
        completed, partial or failed with clips delivered  -> commit the share
                                                              of their seconds
        completed, partial or failed, nothing delivered    -> rollback
        an auto-render still at clips_selected (reserved,
        never dispatched)                                  -> rollback
        a clip still rendering, or any other stage         -> report only
    Render reservation of an approval from before (one export of every
    segment, no ``render_clip_ids``):
        project with final_export_r2_key (delivered)       -> commit
        project failed                                     -> rollback
        anything else (still rendering)                    -> report only
    Not linked to any project                              -> report only
    A render stuck in RENDERING is left to recover_stuck_renders, which
    re-queues it, fails it (refunded) or only reports it, and which also
    charges a delivered render left unpaid (the same rule as here).

Safety
    Dry run by default: without ``--apply`` nothing is written. Only
    transactions older than ``--min-age-hours`` (24 by default) are read.
    With ``--apply`` the decision is taken again under the project's row lock
    and settled with ``wallet_service.commit_reservation``,
    ``commit_partial_reservation`` (clip by clip: the share delivered) or
    ``rollback_reservation``, which lock the transaction and re-check that it
    is still RESERVED: a reservation the pipeline settled meanwhile is skipped.

Output
    One line per transaction with ids, amounts, states and the decision. No
    user text: never the transaction description (it holds the project title)
    nor anything from the project besides its id and states.
"""

from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError, transaction
from django.utils import timezone

from apps.payments.models import Transaction
from apps.videos.models import VideoClip, VideoProject
from apps.videos.services.ai.pipeline_state import STAGE_ORDER, TERMINAL_STAGES
from apps.videos.tasks.ingestion import (
    APPROVED_RENDER, AUTO_RENDER, AUTO_RENDER_RESERVATION_KEY, CLIP_RENDER, _clip_render_charge,
    _clip_render_settlement, _is_whole_video, _paid_clip_renders, _restore_rerendered_clips,
    _whole_video_settlement,
)

COMMIT, ROLLBACK, REPORT = "commit", "rollback", "report"
_WHOLE_VIDEO_ACTIONS = {"commit": COMMIT, "rollback": ROLLBACK, "wait": REPORT}

# The metadata key that links each kind of reservation to its project.
_LINK_KEYS = (
    ("creation", "reservation_id"),
    ("render", "render_reservation_id"),
    ("auto_render", AUTO_RENDER_RESERVATION_KEY),
    ("clip_render", CLIP_RENDER.reservation_key),
)
# Only what the rules read. ``metadata`` is read for the rules (whole video),
# never printed.
_PROJECT_FIELDS = ("id", "project_type", "pipeline_stage", "status", "final_export_r2_key", "metadata")


def _linked_project(transaction_id):
    """(kind, project) of the project this reservation belongs to, or (None, None)."""
    for kind, key in _LINK_KEYS:
        project = (
            VideoProject.objects.filter(**{f"metadata__{key}": str(transaction_id)})
            .order_by("created_at")
            .only(*_PROJECT_FIELDS)
            .first()
        )
        if project is not None:
            return kind, project
    return None, None


def _clips_delivered(stage):
    """The ingestion delivered its work: clips_selected or a later stage."""
    if stage in TERMINAL_STAGES:
        return True
    return stage in STAGE_ORDER and STAGE_ORDER.index(stage) >= STAGE_ORDER.index(
        VideoProject.PipelineStage.CLIPS_SELECTED
    )


def _paid_clip_render(kind, project):
    """The clip-by-clip render a reservation of ``kind`` pays for (auto-render, approval or later render), or None."""
    paid = {"auto_render": AUTO_RENDER, "render": APPROVED_RENDER, "clip_render": CLIP_RENDER}.get(kind)
    return paid if paid in _paid_clip_renders(project.metadata or {}) else None


def decide(kind, project):
    """(action, reason) for a reservation of ``kind`` linked to ``project`` (a VideoProject)."""
    if project is None:
        return REPORT, "unlinked"
    if kind == "creation":
        if _is_whole_video(project):
            # Paid when the video is delivered, never at clips_selected.
            outcome, reason = _whole_video_settlement(project)
            return _WHOLE_VIDEO_ACTIONS[outcome], reason
        if _clips_delivered(project.pipeline_stage):
            return COMMIT, "clips_selected"
        if VideoClip.objects.filter(project_id=project.pk).exists():
            return COMMIT, "has_clips"
        if project.pipeline_stage == VideoProject.PipelineStage.FAILED:
            return ROLLBACK, "failed_without_clips"
        return REPORT, "in_progress"
    if kind == "auto_render" and project.pipeline_stage == VideoProject.PipelineStage.CLIPS_SELECTED:
        # Reserved and never dispatched (the dispatch moves the project to
        # render_dispatched before queueing): it pays for nothing.
        return ROLLBACK, "not_dispatched"
    paid = _paid_clip_render(kind, project)
    if paid is not None:
        outcome, _delivered = _clip_render_settlement(project, paid)
        if outcome == "charge":
            return COMMIT, "clips_delivered"
        if outcome == "rollback":
            return ROLLBACK, "nothing_delivered"
        return REPORT, f"{kind}_in_progress"
    if project.final_export_r2_key:
        # The export was saved: delivered, even if a late error marked it FAILED.
        return COMMIT, "render_delivered"
    if project.status == VideoProject.Status.FAILED:
        return ROLLBACK, "render_failed"
    return REPORT, "render_in_progress"


def _settle(transaction_id, kind, project_id):
    """
    Decides again under the project's row lock (a stage moving meanwhile waits
    for it) and settles. Returns (action, reason, result); result is
    ``committed``, ``rolled_back``, ``skipped`` (the decision is now to report)
    or ``already_settled`` (the pipeline settled it first).
    """
    from apps.payments.services.wallet_service import (
        PaymentError, commit_partial_reservation, commit_reservation, rollback_reservation,
    )

    with transaction.atomic():
        project = (
            VideoProject.objects.select_for_update().filter(pk=project_id)
            .only(*_PROJECT_FIELDS).first()
        )
        action, reason = decide(kind, project)
        if action == REPORT:
            return action, reason, "skipped"
        try:
            with transaction.atomic():
                paid = _paid_clip_render(kind, project)
                if action == COMMIT and paid is not None:
                    # Only the share of the seconds delivered, as the pipeline does.
                    _outcome, delivered = _clip_render_settlement(project, paid)
                    amount = Transaction.objects.values_list("amount", flat=True).get(pk=transaction_id)
                    commit_partial_reservation(
                        transaction_id, _clip_render_charge(project, amount, delivered, paid),
                    )
                elif action == COMMIT:
                    commit_reservation(transaction_id)
                else:
                    rollback_reservation(transaction_id)
        except PaymentError:
            # Re-checked under the transaction's lock: no longer RESERVED.
            if Transaction.objects.filter(pk=transaction_id).exclude(
                status=Transaction.Status.RESERVED
            ).exists():
                return action, reason, "already_settled"
            raise
    if kind == "clip_render":
        # Settled: the clips it never rendered are the user's again, as they were.
        _restore_rerendered_clips(project_id)
    return action, reason, "committed" if action == COMMIT else "rolled_back"


class Command(BaseCommand):
    help = (
        "Reports token reservations left RESERVED for longer than --min-age-hours "
        "and, with --apply, commits or rolls them back by the state of their project."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply", action="store_true",
            help="Settle the reservations. Without it the command only reports (dry run).",
        )
        parser.add_argument(
            "--min-age-hours", type=float, default=24.0,
            help="Only reservations at least this old (default: 24).",
        )

    def handle(self, *args, **options):
        # Deferred: wallet_service imports the tasks package at load time.
        from apps.payments.services.wallet_service import PaymentError

        apply = options["apply"]
        min_age_hours = options["min_age_hours"]
        if min_age_hours < 0:
            raise CommandError("--min-age-hours must not be negative.")

        now = timezone.now()
        stale = (
            Transaction.objects.filter(
                status=Transaction.Status.RESERVED,
                created_at__lte=now - timedelta(hours=min_age_hours),
            )
            .order_by("created_at", "id")
            .values("id", "amount", "created_at")
        )

        totals = {COMMIT: 0, ROLLBACK: 0, REPORT: 0}
        errors = 0
        # Read whole before settling: a stuck reservation is the exception, so
        # the list is short, and no cursor stays open across the settles.
        for row in list(stale):
            kind, project = _linked_project(row["id"])
            action, reason = decide(kind, project)
            result = "dry_run" if action != REPORT else "reported"
            if apply and action != REPORT:
                try:
                    action, reason, result = _settle(row["id"], kind, project.pk)
                except (PaymentError, DatabaseError) as exc:
                    # One failure never stops the sweep; the class name only.
                    errors += 1
                    result = f"error={type(exc).__name__}"
            totals[action] += 1
            age_h = (now - row["created_at"]).total_seconds() / 3600
            self.stdout.write(
                f"transaction={row['id']} amount={row['amount']} age_h={age_h:.1f} "
                f"kind={kind or '-'} project={project.pk if project else '-'} "
                f"stage={project.pipeline_stage if project else '-'} "
                f"status={project.status if project else '-'} "
                f"action={action} reason={reason} result={result}"
            )

        self.stdout.write(
            f"summary mode={'apply' if apply else 'dry_run'} min_age_hours={min_age_hours:g} "
            f"commit={totals[COMMIT]} rollback={totals[ROLLBACK]} report={totals[REPORT]} "
            f"errors={errors}"
        )
        if errors:
            raise CommandError(f"{errors} reservation(s) could not be settled.")
