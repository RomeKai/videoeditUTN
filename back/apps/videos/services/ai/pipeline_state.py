"""
Ingestion pipeline state machine (AICORE-7).

``VideoProject.pipeline_stage`` is the last stage a project finished. Every
transition is a compare-and-set (``UPDATE ... WHERE pipeline_stage = expected``)
so two workers can never both advance the same project: the loser gets
``PipelineConflictError`` and must stop. The DB row lock is only held while a
transition is written, never during transcription, selection or rendering.
"""

import logging
from typing import Dict, FrozenSet, Optional, Tuple

from django.db import models, transaction
from django.utils import timezone

from apps.videos.models import VideoClip, VideoProject

logger = logging.getLogger(__name__)

PipelineStage = VideoProject.PipelineStage


class StageStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    RUNNING = "running", "Running"
    COMPLETED = "completed", "Completed"
    FAILED = "failed", "Failed"


# Stages that do real work, in execution order. Their status is tracked in
# ``pipeline_stage_status``; terminal outcomes live in ``pipeline_stage`` only.
STAGE_ORDER: Tuple[str, ...] = (
    PipelineStage.UPLOADED,
    PipelineStage.AUDIO_EXTRACTED,
    PipelineStage.TRANSCRIBED,
    PipelineStage.CLIPS_SELECTED,
    PipelineStage.RENDER_DISPATCHED,
)
WORK_STAGES: FrozenSet[str] = frozenset(STAGE_ORDER[1:])

TERMINAL_STAGES: FrozenSet[str] = frozenset(
    {PipelineStage.COMPLETED, PipelineStage.PARTIAL}
)

_TRANSITIONS: Dict[str, FrozenSet[str]] = {
    PipelineStage.UPLOADED: frozenset({PipelineStage.AUDIO_EXTRACTED, PipelineStage.FAILED}),
    PipelineStage.AUDIO_EXTRACTED: frozenset({PipelineStage.TRANSCRIBED, PipelineStage.FAILED}),
    PipelineStage.TRANSCRIBED: frozenset({PipelineStage.CLIPS_SELECTED, PipelineStage.FAILED}),
    PipelineStage.CLIPS_SELECTED: frozenset({PipelineStage.RENDER_DISPATCHED, PipelineStage.FAILED}),
    PipelineStage.RENDER_DISPATCHED: frozenset(
        {PipelineStage.COMPLETED, PipelineStage.PARTIAL, PipelineStage.FAILED}
    ),
    PipelineStage.COMPLETED: frozenset(),
    PipelineStage.PARTIAL: frozenset(),
    # A failed project may be reopened at any work stage to resume from there.
    PipelineStage.FAILED: frozenset(STAGE_ORDER),
}


class PipelineStateError(Exception):
    """Base class for pipeline state machine errors."""


class InvalidTransitionError(PipelineStateError):
    """The requested transition is not part of the transition table."""


class PipelineConflictError(PipelineStateError):
    """Another worker already advanced this project (compare-and-set lost)."""


def can_transition(current: str, new: str) -> bool:
    return new in _TRANSITIONS.get(current, frozenset())


def advance(project_id, expected: str, new: str) -> None:
    """
    Atomically move ``expected`` -> ``new``. Marks ``new`` as completed when it
    is a work stage. Raises ``PipelineConflictError`` if the project is no
    longer at ``expected`` (another worker got there first).
    """
    if not can_transition(expected, new):
        raise InvalidTransitionError(f"Invalid pipeline transition {expected} -> {new}")

    with transaction.atomic():
        row = (
            VideoProject.objects.select_for_update()
            .filter(pk=project_id, pipeline_stage=expected)
            .values("pipeline_stage_status")
            .first()
        )
        if row is None:
            raise PipelineConflictError(
                f"Project {project_id} is no longer at stage {expected}"
            )

        statuses = dict(row["pipeline_stage_status"] or {})
        if new in WORK_STAGES:
            statuses[str(new)] = StageStatus.COMPLETED.value

        updated = VideoProject.objects.filter(pk=project_id, pipeline_stage=expected).update(
            pipeline_stage=new, pipeline_stage_status=statuses
        )
        if updated == 0:
            raise PipelineConflictError(
                f"Project {project_id} is no longer at stage {expected}"
            )

    logger.info(
        "pipeline.advanced project_id=%s from=%s to=%s",
        project_id, expected, new,
        extra={"project_id": str(project_id), "stage": new},
    )


def advance_to(project_id, current: str, target: str) -> str:
    """Walks ``current`` -> ``target`` one CAS at a time. Returns the final stage."""
    index = STAGE_ORDER.index(current)
    for stage in STAGE_ORDER[index + 1:STAGE_ORDER.index(target) + 1]:
        advance(project_id, current, stage)
        current = stage
    return current


def set_stage_status(
    project_id, stage: str, status: str, expected_stage: Optional[str] = None
) -> None:
    """
    Records pending/running/completed/failed for one stage without touching the others.
    A completed stage is never downgraded (e.g. by a worker that lost the race).
    When ``expected_stage`` is given the write only happens while the project is
    still at that stage; otherwise ``PipelineConflictError`` is raised (the caller
    lost ownership and must not touch the row).
    """
    with transaction.atomic():
        project = VideoProject.objects.select_for_update().get(pk=project_id)
        if expected_stage is not None and project.pipeline_stage != expected_stage:
            raise PipelineConflictError(
                f"Project {project_id} is no longer at stage {expected_stage}"
            )
        statuses = dict(project.pipeline_stage_status or {})
        if statuses.get(str(stage)) == StageStatus.COMPLETED:
            return
        statuses[str(stage)] = str(status)
        VideoProject.objects.filter(pk=project_id).update(pipeline_stage_status=statuses)


def reopen(project_id, stage: str) -> None:
    """Moves a FAILED project back to ``stage`` so the pipeline can resume from it."""
    advance(project_id, PipelineStage.FAILED, stage)
    # mark_failed set the user-facing status to FAILED; without resetting it a
    # resume past the early stages would never reach AWAITING_APPROVAL/COMPLETED.
    status = (
        VideoProject.Status.RENDERING
        if stage == PipelineStage.RENDER_DISPATCHED
        else VideoProject.Status.INGESTING
    )
    VideoProject.objects.filter(pk=project_id).update(
        pipeline_error_code="", pipeline_error_at=None, status=status
    )


def mark_failed(project_id, expected_stage: Optional[str] = None) -> None:
    """
    Terminal failure: stage and user-facing status. Finished projects are left alone.
    When ``expected_stage`` is given and the project moved on (another worker owns
    it now), nothing is written.
    """
    with transaction.atomic():
        project = VideoProject.objects.select_for_update().get(pk=project_id)
        if project.pipeline_stage in TERMINAL_STAGES:
            return
        if expected_stage is not None and project.pipeline_stage != expected_stage:
            logger.warning(
                "pipeline.mark_failed_skipped project_id=%s expected=%s actual=%s",
                project_id, expected_stage, project.pipeline_stage,
            )
            return
        VideoProject.objects.filter(pk=project_id).update(
            pipeline_stage=PipelineStage.FAILED, status=VideoProject.Status.FAILED
        )


def record_error(project_id, code: str) -> None:
    VideoProject.objects.filter(pk=project_id).update(
        pipeline_error_code=code[:100], pipeline_error_at=timezone.now()
    )


def infer_completed_stage(project: VideoProject) -> str:
    """
    Furthest stage already satisfied by persisted data. Lets projects created
    before pipeline state existed skip work (and paid AI calls) they already did.
    """
    if VideoClip.objects.filter(project=project).exists():
        return PipelineStage.CLIPS_SELECTED
    if project.transcript_data:
        return PipelineStage.TRANSCRIBED
    return PipelineStage.UPLOADED


def last_completed_stage(project: VideoProject) -> str:
    """Resume point after a failure: the last work stage recorded as completed."""
    statuses = project.pipeline_stage_status or {}
    resume: Optional[str] = PipelineStage.UPLOADED
    for stage in STAGE_ORDER[1:]:
        if statuses.get(stage) != StageStatus.COMPLETED:
            break
        resume = stage
    return resume
