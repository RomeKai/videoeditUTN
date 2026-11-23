"""AICORE-9: per-user V2 rollout, startup check of the AI configuration (ADR-012 D-C3)
and the reasoning level recorded per project for the AICORE-14 comparison."""

import csv
from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch

from django.core.management import call_command
from django.test import SimpleTestCase, TestCase, override_settings

from apps.videos.checks import check_ai_configuration
from apps.videos.models import AIUsageRecord, VideoProject
from apps.videos.services.ai.pricing import has_current_price
from apps.videos.services.ai.rollout import v2_enabled_for
from apps.videos.services.transcription_engine import TranscriptionBackend, TranscriptionEngine
from apps.videos.tests.test_ingestion_pipeline import IngestionHarness, selection_result

# The legacy implementation itself, not the dispatcher in front of it: patching the
# dispatcher would hide a dispatcher that re-reads the master switch.
LEGACY_IMPL = "apps.videos.services.selection_engine.SelectionEngine._select_legacy"
UPLOADER = "8f14e45f-ceea-467f-a0e6-9c3f5f2b4a11"


def project_by(uploader):
    return SimpleNamespace(uploaded_by_id=uploader)


class RolloutDecisionTests(SimpleTestCase):
    @override_settings(AI_CORE_V2_ENABLED=False, AI_CORE_V2_ALLOWLIST=[UPLOADER])
    def test_master_switch_off_is_the_kill_switch(self):
        self.assertFalse(v2_enabled_for(project_by(UPLOADER)))

    @override_settings(AI_CORE_V2_ENABLED=True, AI_CORE_V2_ALLOWLIST=[])
    def test_empty_allowlist_means_everyone(self):
        self.assertTrue(v2_enabled_for(project_by(UPLOADER)))
        self.assertTrue(v2_enabled_for(project_by(None)))

    @override_settings(AI_CORE_V2_ENABLED=True, AI_CORE_V2_ALLOWLIST=[f" {UPLOADER.upper()} "])
    def test_allowlist_matches_the_uploader_only(self):
        self.assertTrue(v2_enabled_for(project_by(UPLOADER)))
        self.assertFalse(v2_enabled_for(project_by("00000000-0000-0000-0000-000000000000")))
        self.assertFalse(v2_enabled_for(project_by(None)))

    @override_settings(AI_CORE_V2_ENABLED=True, AI_CORE_V2_ALLOWLIST=f"x, {UPLOADER}")
    def test_a_comma_separated_string_also_works(self):
        self.assertTrue(v2_enabled_for(project_by(UPLOADER)))

    @override_settings(AI_CORE_V2_ENABLED=True, AI_CORE_V2_ALLOWLIST=[UPLOADER.replace("-", "")])
    def test_any_uuid_spelling_matches_the_uploader(self):
        import uuid

        self.assertTrue(v2_enabled_for(project_by(uuid.UUID(UPLOADER))))

    @override_settings(AI_CORE_V2_ENABLED=True, TRANSCRIPTION_BACKEND="groq")
    def test_the_engine_follows_the_project_decision_not_the_master_switch(self):
        self.assertIs(TranscriptionEngine.resolve_backend(False), TranscriptionBackend.LOCAL)
        self.assertIs(TranscriptionEngine.resolve_backend(True), TranscriptionBackend.GROQ)
        self.assertIs(TranscriptionEngine.resolve_backend(), TranscriptionBackend.GROQ)  # master


class RolloutPipelineTests(IngestionHarness, TestCase):
    def versions(self, project):
        return dict(AIUsageRecord.objects.filter(project=project).values_list("stage", "pipeline_version"))

    def test_a_user_outside_the_allowlist_runs_the_legacy_path(self):
        project = self.make_project()
        legacy = [{"start": 0.0, "end": 5.0, "title": "t", "virality_score": 80, "reasoning": "r"}]
        with self.settings(AI_CORE_V2_ALLOWLIST=["00000000-0000-0000-0000-000000000000"]), \
                patch(LEGACY_IMPL, return_value=legacy) as legacy_select, \
                patch.object(TranscriptionEngine, "resolve_backend", wraps=TranscriptionEngine.resolve_backend) as resolve:
            self.run_task(project)

        resolve.assert_any_call(False)  # the transcription asked for the legacy backend
        legacy_select.assert_called_once()
        self.mock_select.assert_not_called()
        self.assertEqual(self.versions(project)["selection"], AIUsageRecord.PipelineVersion.LEGACY)

    def test_an_allowlisted_user_runs_v2(self):
        project = self.make_project()
        with self.settings(AI_CORE_V2_ALLOWLIST=[str(self.user.pk)]), \
                patch.object(TranscriptionEngine, "resolve_backend", wraps=TranscriptionEngine.resolve_backend) as resolve:
            self.run_task(project)

        resolve.assert_any_call(True)
        self.mock_select.assert_called_once()
        self.assertEqual(self.versions(project)["selection"], AIUsageRecord.PipelineVersion.V2)
        self.assertEqual(self.versions(project)["transcription"], AIUsageRecord.PipelineVersion.V2)


class EngineRoutingTests(SimpleTestCase):
    """The engine itself follows the project decision (not only the task that builds it)."""

    @override_settings(AI_CORE_V2_ENABLED=True, TRANSCRIPTION_BACKEND="groq")
    def test_a_project_outside_v2_transcribes_locally_even_with_the_master_switch_on(self):
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".mp4") as media, \
                patch.object(TranscriptionEngine, "_transcribe_local_raw", return_value={"text": "", "segments": []}) as local, \
                patch.object(TranscriptionEngine, "_transcribe_groq") as groq:
            TranscriptionEngine(v2_enabled=False).transcribe(media.name)
            local.assert_called_once()
            groq.assert_not_called()

            local.reset_mock()
            try:
                TranscriptionEngine(v2_enabled=True).transcribe(media.name)
            except Exception:
                pass  # the mocked Groq result is not a real one; only the routing matters
            groq.assert_called_once()
            local.assert_not_called()


class ReasoningEffortRecordTests(IngestionHarness, TestCase):
    def export_rows(self):
        out = StringIO()
        call_command("export_ai_pilot_metrics", "--since=2000-01-01", "--level=project", stdout=out)
        return {row["project_id"]: row for row in csv.DictReader(StringIO(out.getvalue()))}

    def test_the_selection_records_the_level_it_ran_with(self):
        for setting, expected in (("low", "low"), ("", "default")):
            with self.subTest(setting=setting), self.settings(AI_LLM_REASONING_EFFORT=setting):
                project = self.make_project()
                self.mock_select.return_value = selection_result()
                self.run_task(project)
                project.refresh_from_db()
                self.assertEqual(project.metadata["selection_reasoning_effort"], expected)
                self.assertEqual(self.export_rows()[str(project.pk)]["reasoning_effort"], expected)

    def test_a_model_that_cannot_reason_records_default(self):
        with self.settings(AI_LLM_REASONING_EFFORT="low"), \
                patch("apps.videos.services.ai.llm_options.reasoning_kwargs", return_value={}):
            project = self.make_project()
            self.run_task(project)
        project.refresh_from_db()
        self.assertEqual(project.metadata["selection_reasoning_effort"], "default")

    def test_the_export_only_shows_known_levels(self):
        project = self.make_project(metadata={"selection_reasoning_effort": "=HYPERLINK(\"x\")"})
        other = self.make_project()
        rows = self.export_rows()
        self.assertEqual(rows[str(project.pk)]["reasoning_effort"], "")
        self.assertEqual(rows[str(other.pk)]["reasoning_effort"], "")  # never selected


class StartupCheckTests(SimpleTestCase):
    def ids(self):
        return [error.id for error in check_ai_configuration()]

    def test_the_default_configuration_passes(self):
        with self.settings(AI_CORE_V2_ENABLED=True, AI_CAPACITY_ENABLED=False, AI_LLM_REASONING_EFFORT=""):
            self.assertEqual(self.ids(), [])

    def test_a_moving_alias_fails(self):
        with self.settings(AI_DEFAULT_LLM_MODEL="gemini/gemini-flash-latest"):
            self.assertIn("videos.E001", self.ids())

    def test_a_model_without_a_price_fails(self):
        with self.settings(AI_FALLBACK_LLM_MODEL="gemini/gemini-9-ultra"):
            self.assertIn("videos.E002", self.ids())

    def test_the_transcription_model_is_checked_only_when_groq_runs(self):
        with self.settings(AI_DEFAULT_TRANSCRIPTION_MODEL="whisper-unknown", TRANSCRIPTION_BACKEND="groq"):
            with self.settings(AI_CORE_V2_ENABLED=False):
                self.assertNotIn("videos.E002", self.ids())
            with self.settings(AI_CORE_V2_ENABLED=True):
                self.assertIn("videos.E002", self.ids())
            with self.settings(AI_CORE_V2_ENABLED=True, TRANSCRIPTION_BACKEND="local"):
                self.assertNotIn("videos.E002", self.ids())

    def test_an_unknown_transcription_backend_fails(self):
        with self.settings(AI_CORE_V2_ENABLED=True, TRANSCRIPTION_BACKEND="nope"):
            self.assertIn("videos.E003", self.ids())

    def test_an_invalid_reasoning_level_fails(self):
        with self.settings(AI_LLM_REASONING_EFFORT="max"):
            self.assertIn("videos.E004", self.ids())

    def test_capacity_needs_limits_for_every_model_the_pipeline_reserves(self):
        limits = {
            "gemini/gemini-3.8-flash": {"tokens_per_minute": 1000, "requests_per_minute": 10},
            "groq/whisper-large-v3-turbo": {"audio_seconds_per_hour": 5760, "requests_per_minute": 16},
        }
        common = dict(
            AI_CAPACITY_ENABLED=True, AI_CORE_V2_ENABLED=True, TRANSCRIPTION_BACKEND="groq",
            AI_DEFAULT_LLM_MODEL="gemini/gemini-3.8-flash",
            AI_FALLBACK_LLM_MODEL="gemini/gemini-3.5-flash-lite",
        )
        with self.settings(AI_CAPACITY_LIMITS=limits, **common), \
                patch("apps.videos.services.ai.llm_credentials.resolve_api_key", return_value=None):
            self.assertEqual(self.ids(), [])  # a fallback without key is never reserved
        with self.settings(AI_CAPACITY_LIMITS=limits, GEMINI_API_KEY="k", **common):
            self.assertEqual(self.ids(), ["videos.E005"])  # the fallback has a key: needs limits
        typo = {**limits, "gemini/gemini-3.8-flash": {"tokens_per_min": 1000}}
        with self.settings(AI_CAPACITY_LIMITS=typo, **common), \
                patch("apps.videos.services.ai.llm_credentials.resolve_api_key", return_value=None):
            self.assertEqual(self.ids(), ["videos.E005"])

    def test_capacity_limits_that_are_not_an_object_are_an_error_not_a_crash(self):
        for limits in ([1], "x", None):
            with self.subTest(limits=limits), self.settings(AI_CAPACITY_ENABLED=True, AI_CAPACITY_LIMITS=limits):
                self.assertIn("videos.E005", self.ids())

    def test_allowlist_entries_must_be_user_ids(self):
        with self.settings(AI_CORE_V2_ALLOWLIST=[UPLOADER, "franco"]):
            self.assertIn("videos.E006", self.ids())
        with self.settings(AI_CORE_V2_ALLOWLIST=[UPLOADER, UPLOADER.replace("-", "")]):
            self.assertNotIn("videos.E006", self.ids())

    def test_has_current_price_never_logs_and_respects_periods(self):
        from datetime import date

        with self.assertNoLogs("apps.videos.services.ai.pricing"):
            self.assertTrue(has_current_price("gemini/gemini-3.8-flash"))
            self.assertTrue(has_current_price("whisper-large-v3-turbo", "asr"))
            self.assertFalse(has_current_price("gemini/gemini-flash-latest"))
            # Before the first price period of the model: no current price.
            self.assertFalse(has_current_price("gemini/gemini-3.8-flash", on=date(2026, 1, 1)))
            self.assertTrue(has_current_price("gemini/gemini-3.8-flash", on=date(2027, 6, 1)))
