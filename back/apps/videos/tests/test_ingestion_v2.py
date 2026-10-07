import os
import shutil
import tempfile
from unittest.mock import patch, MagicMock
from django.test import TestCase, override_settings
from django.conf import settings
from rest_framework.test import APITestCase
from rest_framework import status
from django.urls import reverse
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.videos.models import VideoProject, VideoClip
from apps.videos.utils.ffmpeg_utils import FFmpegManager
from apps.videos.tasks import process_initial_ingestion
from apps.videos.services.ai.contracts import (
    AIExecutionResult,
    ClipSelectionResult,
    ProviderUsage,
    TranscriptionResult,
    ViralClip,
    WordTimestamp,
)
from apps.videos.tests.fakes import FakeRedis
from apps.users.models import Workspace

User = get_user_model()

class IngestionV2Tests(TestCase):
    """
    Tests for the Ingestion Worker V2 components (FFmpeg and Celery Task).
    """

    def setUp(self):
        self.user = User.objects.create_user(username='testworker', email='worker@test.com', password='password')
        self.workspace = Workspace.objects.create(name="Test Workspace", owner=self.user)
        self.workspace.members.add(self.user)
        
        # Create a dummy video file for testing
        self.temp_dir = tempfile.mkdtemp()
        self.dummy_video = os.path.join(self.temp_dir, "dummy.mp4")
        # We don't need a real video for FFmpegManager if we mock the subprocess, 
        # but for a real integration test we'd need a tiny valid mp4.
        # For now, let's mock the subprocess call in FFmpegManager.
        with open(self.dummy_video, "wb") as f:
            f.write(b"dummy content")

    def tearDown(self):
        shutil.rmtree(self.temp_dir)

    @patch('subprocess.run')
    def test_ffmpeg_manager_proxy_generation(self, mock_run):
        """Test that FFmpegManager calls the correct command."""
        mock_run.return_value = MagicMock(returncode=0)
        output_path = os.path.join(self.temp_dir, "proxy.mp4")
        
        FFmpegManager.generate_web_proxy(self.dummy_video, output_path)
        
        self.assertTrue(mock_run.called)
        args, kwargs = mock_run.call_args
        command = args[0]
        self.assertIn('ffmpeg', command)
        self.assertIn('-vf', command)
        self.assertIn('scale=-2:480', command)
        self.assertIn('+faststart', command)

    @override_settings(AI_CORE_V2_ENABLED=True)
    @patch('apps.videos.services.ai.project_lock.get_redis_client', return_value=FakeRedis())
    @patch('apps.videos.services.storage_service.CloudflareR2Manager.upload_video')
    @patch('apps.videos.services.transcription_engine.TranscriptionEngine.transcribe_detailed')
    @patch('apps.videos.services.selection_engine.SelectionEngine.select_viral_clips_detailed')
    @patch('apps.videos.utils.ffmpeg_utils.FFmpegManager.generate_web_proxy')
    @patch('apps.videos.tasks.VideoFileClip', create=True)
    def test_process_initial_ingestion_task(self, mock_vfc, mock_proxy, mock_select, mock_transcribe, mock_upload, _mock_redis):
        """Test the full Celery task pipeline."""
        # Setup mocks
        mock_upload.return_value = "r2_key_test"
        mock_transcribe.return_value = AIExecutionResult(
            data=TranscriptionResult(
                full_text="Hello world",
                words=[WordTimestamp(text="Hello world", start=0.0, end=2.0)],
            ),
            usage=ProviderUsage(provider="groq", model="whisper"),
        )
        mock_select.return_value = AIExecutionResult(
            data=ClipSelectionResult(clips=[
                ViralClip(title="Clip 1", start=0.0, end=2.0, virality_score=90, reasoning="Test")
            ]),
            usage=ProviderUsage(provider="litellm", model="gemini"),
        )

        # Mock MoviePy VideoFileClip
        mock_clip_instance = MagicMock()
        mock_clip_instance.duration = 10.0
        mock_clip_instance.size = [1920, 1080]
        mock_clip_instance.__enter__.return_value = mock_clip_instance
        mock_vfc.return_value = mock_clip_instance

        # Create project
        project = VideoProject.objects.create(
            workspace=self.workspace,
            uploaded_by=self.user,
            title="Test Ingestion",
            source_file=SimpleUploadedFile("test.mp4", b"content")
        )
        
        # Run task synchronously
        process_initial_ingestion(project.id)
        
        project.refresh_from_db()
        
        # Assertions
        self.assertEqual(project.status, VideoProject.Status.AWAITING_APPROVAL)
        self.assertEqual(project.original_r2_key, "r2_key_test")
        self.assertEqual(project.proxy_r2_key, "r2_key_test")
        self.assertEqual(project.transcript_data[0]['text'], "Hello world")
        self.assertEqual(project.clips.count(), 1)
        self.assertTrue(mock_proxy.called)


class PaperEditAPITests(APITestCase):
    """
    Tests for the Paper Edit API endpoint.
    """

    def setUp(self):
        self.user = User.objects.create_user(username='testapi', email='api@test.com', password='password')
        self.workspace = Workspace.objects.create(name="Test Workspace API", owner=self.user)
        self.workspace.members.add(self.user)
        self.client.force_authenticate(user=self.user)
        
        self.project = VideoProject.objects.create(
            workspace=self.workspace,
            uploaded_by=self.user,
            title="Paper Edit Project",
            proxy_r2_key="proxy_key",
            transcript_data=[{"text": "Sample text", "start": 0.0, "end": 5.0}],
            status=VideoProject.Status.AWAITING_APPROVAL
        )
        VideoClip.objects.create(project=self.project, title="AI Suggestion", start_time=0, end_time=2)

    @patch('apps.videos.services.storage_service.CloudflareR2Manager.generate_presigned_url')
    def test_paper_edit_data_endpoint(self, mock_url):
        """Test retrieving data for the paper edit interface."""
        mock_url.return_value = "https://presigned-url.com/proxy.mp4"
        
        url = reverse('project-paper-edit-data', args=[self.project.id])
        res = self.client.get(url)
        
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['proxy_url'], "https://presigned-url.com/proxy.mp4")
        self.assertEqual(res.data['transcript'][0]['text'], "Sample text")
        self.assertEqual(len(res.data['ai_suggestions']), 1)
        self.assertEqual(str(res.data['project_id']), str(self.project.id))

    def test_approve_paper_edit_endpoint(self):
        """Test approving segments via the Paper Edit API."""
        url = reverse('project-approve-paper-edit', args=[self.project.id])
        payload = {
            "approved_segments": [
                {"start": 0.0, "end": 2.0, "text": "Sample text"}
            ]
        }
        
        # We need to mock the render task call to avoid starting a real celery task
        with patch('apps.videos.tasks.render_video_segments.delay') as mock_render:
            with patch('apps.payments.services.wallet_service.reserve_funds') as mock_reserve:
                mock_reserve.return_value = MagicMock(id=123)
                res = self.client.post(url, payload, format='json')
        
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.project.refresh_from_db()
        self.assertEqual(self.project.status, VideoProject.Status.RENDERING)
        self.assertEqual(self.project.metadata['approved_segments'][0]['start'], 0.0)
