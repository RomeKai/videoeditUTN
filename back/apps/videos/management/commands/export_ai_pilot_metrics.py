"""
Exports AI pilot metrics (AICORE-8) as CSV or JSONL.

PRIVACY CONTRACT
    The export reads ONLY numeric/enum/model-name columns of ``AIUsageRecord``
    and a handful of concrete ``VideoProject`` columns (id, status, stage,
    attempts, creation date). It never reads or emits ``title``,
    ``transcript_data``, ``ai_rationale_log``, prompts, reasoning, user email or
    user id. The single value taken from ``VideoProject.metadata`` is the
    numeric ``duration`` key, extracted inside the database (the JSON blob is
    never loaded into Python).

Window
    ``--since`` is inclusive and ``--until`` is EXCLUSIVE, both as dates whose
    boundary is 00:00 UTC. The record level filters ``AIUsageRecord.created_at``;
    the project level filters ``VideoProject.created_at`` and aggregates ALL
    the records of those projects, including projects with no records.

Costs
    ``cost_per_30min_usd = total_cost_usd / source_duration_s * 1800`` where the
    duration is the SOURCE VIDEO length, not ``audio_seconds`` (a billing figure
    that sums chunk durations including overlap). Empty when the duration is
    missing or not positive. ``cost_complete`` is False when any record has an
    unknown (NULL) cost or the project has no records; in that case the total
    and the per-30-min figures are a lower bound and must not be trusted.
    Decimals are exported as plain strings, never as floats.

CSV injection
    Text values exported to CSV that start with ``= + - @ TAB CR`` are prefixed
    with a single quote. JSONL is not opened by spreadsheets and is left raw.
"""

import csv
import json
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, Iterable, Iterator, Optional, Tuple

from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.db.models import Case, Count, FloatField, Max, Q, Sum, When
from django.db.models.fields.json import KeyTextTransform
from django.db.models.functions import Cast

from apps.videos.models import AIUsageRecord, VideoProject

CHUNK_SIZE = 2000
_COST_QUANTUM = Decimal("1E-6")

# Whitelist of AIUsageRecord columns exported at record level. Adding a column
# here is a privacy decision: the test suite checks it stays a concrete,
# non-text subset of the model.
EXPORT_COLUMNS: Tuple[str, ...] = (
    "provider",
    "model",
    "resolved_model",
    "stage",
    "role",
    "pipeline_version",
    "attempt",
    "success",
    "error_code",
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "cache_read_tokens",
    "audio_seconds",
    "latency_seconds",
    "cached",
    "estimated_cost_usd",
    "pricing_version",
    "created_at",
    "project_id",
)

PROJECT_COLUMNS: Tuple[str, ...] = (
    "project_id",
    "pipeline_version",
    "source_duration_s",
    "final_stage",
    "status",
    "attempts",
    "transcription_latency_s",
    "selection_latency_s",
    "fallback_used",
    "cached_any",
    "total_tokens",
    "total_cost_usd",
    "cost_per_30min_usd",
    "cost_complete",
    "records_count",
    "failed_records",
)

_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")
_DURATION_REGEX = r"^[0-9]+(\.[0-9]+)?$"


def sanitize_csv_text(value: str) -> str:
    """Neutralizes spreadsheet formula injection (OWASP CSV injection)."""
    if value.startswith(_FORMULA_PREFIXES):
        return "'" + value
    return value


def _parse_date(option: str, raw: Optional[str]) -> date:
    # Python 3.11 fromisoformat also accepts "20261001" and ISO weeks: require
    # the documented YYYY-MM-DD shape only.
    try:
        if raw is None or len(raw) != 10:
            raise ValueError
        return date.fromisoformat(raw)
    except ValueError:
        raise CommandError(f"--{option} must be a valid date in YYYY-MM-DD format.")


def _utc_midnight(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=timezone.utc)


def _plain(value: Any) -> Any:
    """Typed, JSON-friendly value: Decimal -> plain string, datetime -> ISO UTC."""
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    if value is not None and not isinstance(value, (bool, int, float, str)):
        return str(value)  # UUID
    return value


def _csv_cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return sanitize_csv_text(value)
    return str(value)


class Command(BaseCommand):
    help = (
        "Export AI pilot metrics (privacy-safe: no titles, transcripts, prompts "
        "or reasoning) from AIUsageRecord as CSV or JSONL."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--since", required=True, help="Inclusive start date, YYYY-MM-DD (00:00 UTC).")
        parser.add_argument("--until", help="Exclusive end date, YYYY-MM-DD (00:00 UTC).")
        parser.add_argument("--level", choices=("record", "project"), default="project")
        parser.add_argument("--format", dest="fmt", choices=("csv", "jsonl"), default="csv")
        parser.add_argument("--output", help="Write to this file instead of stdout.")

    def handle(self, *args: Any, **options: Any) -> None:
        since = _parse_date("since", options["since"])
        until = _parse_date("until", options["until"]) if options["until"] else None
        if until is not None and until <= since:
            raise CommandError("--until must be after --since.")
        start = _utc_midnight(since)
        end = _utc_midnight(until) if until else None

        if options["level"] == "record":
            columns = EXPORT_COLUMNS
            rows = self._record_rows(start, end)
        else:
            columns = PROJECT_COLUMNS
            rows = self._project_rows(start, end)

        if options["output"]:
            try:
                with open(options["output"], "w", encoding="utf-8", newline="") as handle:
                    self._write(handle, columns, rows, options["fmt"])
            except OSError as exc:
                raise CommandError(f"Cannot write --output file: {type(exc).__name__}")
        else:
            self._write(self.stdout, columns, rows, options["fmt"])

    # -- querying -----------------------------------------------------------

    def _record_rows(self, start: datetime, end: Optional[datetime]) -> Iterator[Dict[str, Any]]:
        queryset = AIUsageRecord.objects.filter(created_at__gte=start)
        if end:
            queryset = queryset.filter(created_at__lt=end)
        stream = queryset.order_by("created_at", "id").values_list(*EXPORT_COLUMNS).iterator(chunk_size=CHUNK_SIZE)
        for values in stream:
            yield {name: _plain(value) for name, value in zip(EXPORT_COLUMNS, values)}

    def _project_rows(self, start: datetime, end: Optional[datetime]) -> Iterator[Dict[str, Any]]:
        queryset = VideoProject.objects.filter(created_at__gte=start)
        if end:
            queryset = queryset.filter(created_at__lt=end)

        # Single numeric key of the metadata JSON, pulled in the DB. The regex
        # guard keeps non-numeric junk ("abc", null, negatives) from breaking
        # the cast; those rows simply get no duration.
        duration_text = KeyTextTransform("duration", "metadata")
        records = "ai_usage_records"
        stream = (
            queryset.annotate(
                _duration_text=duration_text,
                source_duration=Case(
                    When(_duration_text__regex=_DURATION_REGEX, then=Cast(duration_text, FloatField())),
                    default=None,
                    output_field=FloatField(),
                ),
                records_count=Count(records),
                failed_records=Count(records, filter=Q(**{f"{records}__success": False})),
                fallback_count=Count(records, filter=Q(**{f"{records}__role": AIUsageRecord.Role.FALLBACK})),
                cached_count=Count(records, filter=Q(**{f"{records}__cached": True})),
                null_cost_count=Count(records, filter=Q(**{f"{records}__estimated_cost_usd__isnull": True})),
                version_count=Count(f"{records}__pipeline_version", distinct=True),
                version_max=Max(f"{records}__pipeline_version"),
                tokens_sum=Sum(f"{records}__total_tokens"),
                cost_sum=Sum(f"{records}__estimated_cost_usd"),
                transcription_latency=Sum(
                    f"{records}__latency_seconds",
                    filter=Q(**{f"{records}__stage": AIUsageRecord.Stage.TRANSCRIPTION}),
                ),
                selection_latency=Sum(
                    f"{records}__latency_seconds",
                    filter=Q(**{f"{records}__stage": AIUsageRecord.Stage.SELECTION}),
                ),
            )
            .order_by("created_at", "id")
            .values(
                "id", "pipeline_stage", "status", "pipeline_attempts", "source_duration",
                "records_count", "failed_records", "fallback_count", "cached_count",
                "null_cost_count", "version_count", "version_max", "tokens_sum", "cost_sum",
                "transcription_latency", "selection_latency",
            )
            .iterator(chunk_size=CHUNK_SIZE)
        )
        for row in stream:
            yield self._project_row(row)

    @staticmethod
    def _project_row(row: Dict[str, Any]) -> Dict[str, Any]:
        count = row["records_count"]
        duration = row["source_duration"]
        total_cost: Optional[Decimal] = row["cost_sum"]

        per_30min: Optional[Decimal] = None
        if total_cost is not None and duration and duration > 0:
            per_30min = (total_cost / Decimal(repr(float(duration))) * 1800).quantize(_COST_QUANTUM)

        if row["version_count"] > 1:
            version: Optional[str] = "mixed"
        else:
            version = row["version_max"] if count else None

        return {
            "project_id": _plain(row["id"]),
            "pipeline_version": version,
            "source_duration_s": duration,
            "final_stage": row["pipeline_stage"],
            "status": row["status"],
            "attempts": row["pipeline_attempts"],
            "transcription_latency_s": row["transcription_latency"],
            "selection_latency_s": row["selection_latency"],
            "fallback_used": row["fallback_count"] > 0,
            "cached_any": row["cached_count"] > 0,
            "total_tokens": row["tokens_sum"],
            "total_cost_usd": _plain(total_cost),
            "cost_per_30min_usd": _plain(per_30min),
            "cost_complete": count > 0 and row["null_cost_count"] == 0,
            "records_count": count,
            "failed_records": row["failed_records"],
        }

    # -- writing ------------------------------------------------------------

    @staticmethod
    def _write(sink: Any, columns: Tuple[str, ...], rows: Iterable[Dict[str, Any]], fmt: str) -> None:
        if fmt == "csv":
            writer = csv.writer(sink, lineterminator="\n")
            writer.writerow(columns)
            for row in rows:
                writer.writerow([_csv_cell(row[name]) for name in columns])
        else:
            for row in rows:
                sink.write(json.dumps({name: row[name] for name in columns}, ensure_ascii=False) + "\n")
