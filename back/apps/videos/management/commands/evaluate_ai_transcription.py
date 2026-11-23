"""
Evaluates the AI pilot's transcription against human references (AICORE-15).

Computes the two automatic G1 metrics of the pilot (runbook §1.1, AICORE-9):
corpus WER (sum of errors / sum of reference words, ≤ 15 %) and the median
timestamp error of the sentence anchors (≤ 300 ms), per project and
aggregated per pipeline version (V2 and legacy process the same videos).

INPUT (``--references DIR``, kept OUTSIDE the repository, runbook §3)
    manifest.csv              header ``video_id,project_id``; a video may map to
                              several projects (one per engine)
    <video_id>.txt            human-corrected transcript, UTF-8
    <video_id>.anchors.csv    optional; header ``time_seconds,text`` with the
                              first words of each annotated sentence

    ``video_id`` is an operator label: letters, digits, ``-`` and ``_`` only.

NORMALIZATION
    Runbook §3.3 (see ``services/ai/evaluation.py``). ``--accents`` is
    mandatory because §3.3 requires recording that decision before evaluating.

PRIVACY CONTRACT
    The output has a fixed list of columns: ids, static status codes and
    numbers. No reference, anchor or transcript text is ever written or logged.
"""

import csv
import re
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from django.core.management.base import BaseCommand, CommandError, CommandParser

from apps.videos.models import AIUsageRecord, VideoProject
from apps.videos.services.ai.evaluation import (
    AnchorReport,
    Token,
    WerResult,
    anchor_errors,
    normalize_text,
    normalize_tokens,
    word_error_rate,
)

WER_THRESHOLD = 0.15
TIMESTAMP_THRESHOLD_MS = 300.0
ALL = "ALL"

COLUMNS = (
    "video_id",
    "project_id",
    "pipeline_version",
    "status",
    "reference_words",
    "hypothesis_words",
    "substitutions",
    "deletions",
    "insertions",
    "wer",
    "anchors_total",
    "anchors_matched",
    "anchors_unmatched",
    "timestamp_error_median_ms",
    "timestamp_error_p90_ms",
)

_VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")


class Command(BaseCommand):
    help = "Computes WER and timestamp error of pilot projects against human references."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--references", required=True, help="Directory with manifest.csv and references.")
        parser.add_argument(
            "--accents", required=True, choices=("keep", "strip"),
            help="Runbook §3.3 decision: keep or strip accent marks (ñ is always kept).",
        )
        parser.add_argument(
            "--window", type=float, default=5.0,
            help="Seconds around an anchor in which its words are searched (default 5).",
        )
        parser.add_argument("--output", help="Write the CSV to this file instead of stdout.")

    def handle(self, *args: Any, **options: Any) -> None:
        directory = Path(options["references"])
        if not directory.is_dir():
            raise CommandError("--references must be an existing directory.")
        if options["window"] <= 0:
            raise CommandError("--window must be positive.")
        keep_accents = options["accents"] == "keep"

        manifest = _read_manifest(directory / "manifest.csv")
        rows: List[Dict[str, Any]] = []
        totals: Dict[str, Tuple[WerResult, AnchorReport]] = {}
        references: Dict[str, Optional[Tuple[List[str], List[Tuple[float, str]]]]] = {}

        for video_id, project_id in manifest:
            if video_id not in references:
                references[video_id] = _read_reference(directory, video_id, keep_accents)
            row, wer, anchors = self._evaluate(
                video_id, project_id, references[video_id], keep_accents, options["window"]
            )
            rows.append(row)
            if wer is not None:
                base_wer, base_anchors = totals.get(
                    row["pipeline_version"], (WerResult(0, 0, 0, 0, 0), AnchorReport())
                )
                totals[row["pipeline_version"]] = (_add(base_wer, wer), base_anchors.merge(anchors))

        for version in sorted(totals):
            wer, anchors = totals[version]
            rows.append(_row(ALL, "", version, "ok", wer, anchors))

        if options["output"]:
            try:
                with open(options["output"], "w", encoding="utf-8", newline="") as handle:
                    _write_csv(handle, rows)
            except OSError as exc:
                raise CommandError(f"Cannot write --output file: {type(exc).__name__}")
            summary = self.stdout
        else:
            _write_csv(self.stdout, rows)
            summary = self.stderr
        for version in sorted(totals):
            summary.write(_summary_line(version, *totals[version]))

    def _evaluate(self, video_id, project_id, reference, keep_accents, window):
        project = VideoProject.objects.filter(pk=project_id).only("id", "transcript_data").first()
        if project is None:
            return _status_row(video_id, project_id, "unknown", "project_not_found"), None, None
        version = _pipeline_version(project_id)
        if reference is None:
            return _status_row(video_id, project_id, version, "missing_reference"), None, None

        hypothesis = normalize_tokens(_hypothesis_tokens(project.transcript_data), keep_accents)
        if not hypothesis:
            return _status_row(video_id, project_id, version, "no_transcript"), None, None

        reference_words, anchors = reference
        wer = word_error_rate(reference_words, [token.text for token in hypothesis])
        report = anchor_errors(anchors, hypothesis, keep_accents, window_s=window)
        return _row(video_id, project_id, version, "ok", wer, report), wer, report


# ---------------------------------------------------------------------------
# Input
# ---------------------------------------------------------------------------

def _read_manifest(path: Path) -> List[Tuple[str, str]]:
    if not path.is_file():
        raise CommandError("manifest.csv not found in --references.")
    entries: List[Tuple[str, str]] = []
    with open(path, encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None or {"video_id", "project_id"} - set(reader.fieldnames):
            raise CommandError("manifest.csv must have the header: video_id,project_id")
        for line, record in enumerate(reader, start=2):
            video_id = (record.get("video_id") or "").strip()
            raw_project = (record.get("project_id") or "").strip()
            if not _VIDEO_ID.fullmatch(video_id):
                raise CommandError(f"manifest.csv line {line}: invalid video_id (letters, digits, - and _).")
            try:
                project_id = str(uuid.UUID(raw_project))
            except ValueError:
                raise CommandError(f"manifest.csv line {line}: project_id is not a UUID.") from None
            entries.append((video_id, project_id))
    if not entries:
        raise CommandError("manifest.csv has no rows.")
    return entries


def _read_reference(directory: Path, video_id: str, keep_accents: bool):
    text_path = directory / f"{video_id}.txt"
    if not text_path.is_file():
        return None
    words = normalize_text(text_path.read_text(encoding="utf-8-sig"), keep_accents)

    anchors: List[Tuple[float, str]] = []
    anchors_path = directory / f"{video_id}.anchors.csv"
    if anchors_path.is_file():
        with open(anchors_path, encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames is None or {"time_seconds", "text"} - set(reader.fieldnames):
                raise CommandError(f"{video_id}.anchors.csv must have the header: time_seconds,text")
            for line, record in enumerate(reader, start=2):
                try:
                    seconds = float((record.get("time_seconds") or "").strip())
                except ValueError:
                    raise CommandError(
                        f"{video_id}.anchors.csv line {line}: time_seconds is not a number."
                    ) from None
                if seconds < 0:
                    raise CommandError(f"{video_id}.anchors.csv line {line}: negative time_seconds.")
                anchors.append((seconds, record.get("text") or ""))
    return words, anchors


def _hypothesis_tokens(transcript_data) -> List[Token]:
    tokens = []
    for item in transcript_data or []:
        if not isinstance(item, dict):
            continue
        try:
            start = float(item.get("start"))
        except (TypeError, ValueError):
            start = None
        tokens.append(Token(str(item.get("text") or ""), start))
    return tokens


def _pipeline_version(project_id: str) -> str:
    versions = set(
        AIUsageRecord.objects.filter(
            project_id=project_id, stage=AIUsageRecord.Stage.TRANSCRIPTION
        ).values_list("pipeline_version", flat=True)
    )
    if len(versions) == 1:
        return versions.pop()
    return "mixed" if versions else "unknown"


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def _add(a: WerResult, b: WerResult) -> WerResult:
    return WerResult(
        a.reference_words + b.reference_words,
        a.hypothesis_words + b.hypothesis_words,
        a.substitutions + b.substitutions,
        a.deletions + b.deletions,
        a.insertions + b.insertions,
    )


def _fmt(value: Optional[float], digits: int) -> str:
    return "" if value is None else f"{value:.{digits}f}"


def _row(video_id, project_id, version, status, wer: WerResult, anchors: AnchorReport) -> Dict[str, Any]:
    return {
        "video_id": video_id,
        "project_id": project_id,
        "pipeline_version": version,
        "status": status,
        "reference_words": wer.reference_words,
        "hypothesis_words": wer.hypothesis_words,
        "substitutions": wer.substitutions,
        "deletions": wer.deletions,
        "insertions": wer.insertions,
        "wer": _fmt(wer.wer, 4),
        "anchors_total": anchors.total,
        "anchors_matched": anchors.matched,
        "anchors_unmatched": anchors.unmatched,
        "timestamp_error_median_ms": _fmt(anchors.median_ms, 1),
        "timestamp_error_p90_ms": _fmt(anchors.p90_ms, 1),
    }


def _status_row(video_id, project_id, version, status) -> Dict[str, Any]:
    row = {column: "" for column in COLUMNS}
    row.update(video_id=video_id, project_id=project_id, pipeline_version=version, status=status)
    return row


def _write_csv(handle, rows: Sequence[Dict[str, Any]]) -> None:
    writer = csv.DictWriter(handle, fieldnames=COLUMNS, extrasaction="raise")
    writer.writeheader()
    writer.writerows(rows)


def _summary_line(version: str, wer: WerResult, anchors: AnchorReport) -> str:
    def verdict(ok: Optional[bool]) -> str:
        return "sin datos" if ok is None else ("cumple" if ok else "NO cumple")

    wer_ok = None if wer.wer is None else wer.wer <= WER_THRESHOLD
    ts_ok = None if anchors.median_ms is None else anchors.median_ms <= TIMESTAMP_THRESHOLD_MS
    wer_text = "-" if wer.wer is None else f"{wer.wer:.2%}"
    ts_text = "-" if anchors.median_ms is None else f"{anchors.median_ms:.0f} ms"
    return (
        f"[{version}] WER {wer_text} (umbral {WER_THRESHOLD:.0%}: {verdict(wer_ok)}) | "
        f"error mediano de timestamps {ts_text} (umbral {TIMESTAMP_THRESHOLD_MS:.0f} ms: "
        f"{verdict(ts_ok)}) | anclas sin match {anchors.unmatched}/{anchors.total}\n"
    )
