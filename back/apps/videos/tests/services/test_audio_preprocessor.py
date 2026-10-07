"""
Unit tests for AudioPreprocessor and FFmpeg audio extraction.

Validates:
1. Extraction flags: -vn -ac 1 -ar 16000 -c:a flac.
2. Single-chunk behavior for files < 24 MB.
3. 600-second window slicing with 2-second overlap for files >= 24 MB.
4. Guaranteed cleanup of temporary files on normal execution and exceptions.
"""

import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

# Ensure back root is on sys.path
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
BACK_DIR = os.path.abspath(os.path.join(CURRENT_DIR, "..", "..", "..", ".."))
if BACK_DIR not in sys.path:
    sys.path.insert(0, BACK_DIR)

from apps.videos.services.ai.audio_preprocessor import AudioChunk, AudioPreprocessor
from apps.videos.utils.ffmpeg_utils import FFmpegManager


class TestFFmpegAudioExtractionCommands(unittest.TestCase):
    """Validates FFmpeg CLI commands and audio conversion parameters."""

    @patch("subprocess.run")
    @patch("os.path.exists", return_value=True)
    def test_extract_flac_audio_command_flags(self, mock_exists, mock_run):
        mock_run.return_value = MagicMock(returncode=0)

        output_path = "output.flac"
        input_path = "input_video.mp4"

        FFmpegManager.extract_flac_audio(input_path=input_path, output_path=output_path)

        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]

        # Verify exact acceptance criteria flags: -vn -ac 1 -ar 16000 -c:a flac
        self.assertIn("-vn", cmd)
        self.assertIn("-ac", cmd)
        self.assertEqual(cmd[cmd.index("-ac") + 1], "1")
        self.assertIn("-ar", cmd)
        self.assertEqual(cmd[cmd.index("-ar") + 1], "16000")
        self.assertIn("-c:a", cmd)
        self.assertEqual(cmd[cmd.index("-c:a") + 1], "flac")
        self.assertEqual(cmd[-1], output_path)

    @patch("subprocess.run")
    @patch("os.path.exists", return_value=True)
    def test_extract_flac_audio_with_time_bounds(self, mock_exists, mock_run):
        mock_run.return_value = MagicMock(returncode=0)

        FFmpegManager.extract_flac_audio(
            input_path="input.mp4",
            output_path="chunk.flac",
            start_time=598.0,
            duration=600.0
        )

        cmd = mock_run.call_args[0][0]
        self.assertIn("-ss", cmd)
        self.assertEqual(cmd[cmd.index("-ss") + 1], "598.0")
        self.assertIn("-t", cmd)
        self.assertEqual(cmd[cmd.index("-t") + 1], "600.0")


class TestAudioWindowCalculation(unittest.TestCase):
    """Validates 600s windowing logic and 2s overlap guarantees."""

    def test_duration_under_600s(self):
        windows = AudioPreprocessor.calculate_windows(total_duration=350.0)
        self.assertEqual(len(windows), 1)
        self.assertEqual(windows[0], (0.0, 350.0))

    def test_duration_exact_600s(self):
        windows = AudioPreprocessor.calculate_windows(total_duration=600.0)
        self.assertEqual(len(windows), 1)
        self.assertEqual(windows[0], (0.0, 600.0))

    def test_duration_multi_window_overlap(self):
        # 1300 seconds -> requires 3 windows: [0, 600], [598, 1198], [1196, 1300]
        windows = AudioPreprocessor.calculate_windows(
            total_duration=1300.0,
            window_duration=600.0,
            overlap=2.0
        )

        self.assertEqual(len(windows), 3)
        self.assertEqual(windows[0], (0.0, 600.0))
        self.assertEqual(windows[1], (598.0, 1198.0))
        self.assertEqual(windows[2], (1196.0, 1300.0))

        # Check 2.0s overlap between window 0 and 1
        overlap_0_1 = windows[0][1] - windows[1][0]
        self.assertAlmostEqual(overlap_0_1, 2.0)

        # Check 2.0s overlap between window 1 and 2
        overlap_1_2 = windows[1][1] - windows[2][0]
        self.assertAlmostEqual(overlap_1_2, 2.0)

    def test_invalid_or_zero_duration(self):
        self.assertEqual(AudioPreprocessor.calculate_windows(0.0), [])
        self.assertEqual(AudioPreprocessor.calculate_windows(-50.0), [])


class TestAudioPreprocessorChunking(unittest.TestCase):
    """Tests file size thresholding (<24MB vs >=24MB) and chunk emissions."""

    def setUp(self):
        self.mock_ffmpeg = MagicMock(spec=FFmpegManager)
        self.preprocessor = AudioPreprocessor(ffmpeg_manager=self.mock_ffmpeg)

    @patch("os.path.exists", return_value=True)
    def test_file_under_24mb_emits_single_chunk(self, mock_exists):
        fake_size = 15 * 1024 * 1024  # 15 MB (< 24 MB)
        fake_duration = 420.0  # 7 minutes

        self.mock_ffmpeg.get_media_duration.return_value = fake_duration

        with patch("os.path.getsize", return_value=fake_size):
            with self.preprocessor.process("source_video.mp4") as chunks:
                self.assertEqual(len(chunks), 1)
                chunk = chunks[0]
                self.assertTrue(chunk.is_single_chunk)
                self.assertEqual(chunk.start_time, 0.0)
                self.assertEqual(chunk.end_time, fake_duration)
                self.assertEqual(chunk.duration, fake_duration)
                self.assertEqual(chunk.size_bytes, fake_size)
                self.assertEqual(chunk.chunk_index, 0)

        # Ensure base extraction occurred
        self.mock_ffmpeg.extract_flac_audio.assert_called_once()

    @patch("os.path.exists", return_value=True)
    def test_file_over_24mb_splits_into_overlapping_chunks(self, mock_exists):
        fake_size = 32 * 1024 * 1024  # 32 MB (>= 24 MB)
        fake_duration = 1500.0  # 25 minutes

        self.mock_ffmpeg.get_media_duration.return_value = fake_duration

        with patch("os.path.getsize", return_value=fake_size):
            with self.preprocessor.process("heavy_podcast.mp4") as chunks:
                # 1500s / 598s step => 3 windows: [0, 600], [598, 1198], [1196, 1500]
                self.assertEqual(len(chunks), 3)

                # Window 0
                self.assertEqual(chunks[0].start_time, 0.0)
                self.assertEqual(chunks[0].end_time, 600.0)
                self.assertEqual(chunks[0].chunk_index, 0)
                self.assertFalse(chunks[0].is_single_chunk)

                # Window 1
                self.assertEqual(chunks[1].start_time, 598.0)
                self.assertEqual(chunks[1].end_time, 1198.0)
                self.assertEqual(chunks[1].chunk_index, 1)

                # Window 2
                self.assertEqual(chunks[2].start_time, 1196.0)
                self.assertEqual(chunks[2].end_time, 1500.0)
                self.assertEqual(chunks[2].chunk_index, 2)

                # Verify 2s overlaps
                self.assertAlmostEqual(chunks[0].end_time - chunks[1].start_time, 2.0)
                self.assertAlmostEqual(chunks[1].end_time - chunks[2].start_time, 2.0)

        # Base extraction + 3 chunk slice extractions = 4 calls
        self.assertEqual(self.mock_ffmpeg.extract_flac_audio.call_count, 4)

    def test_missing_input_file_raises_error(self):
        with self.assertRaises(FileNotFoundError):
            with self.preprocessor.process("non_existent_file.mp4"):
                pass


class TestTemporaryFileCleanup(unittest.TestCase):
    """Validates that temporary files are deleted under all circumstances."""

    def test_cleanup_guaranteed_on_exception(self):
        mock_ffmpeg = MagicMock(spec=FFmpegManager)
        preprocessor = AudioPreprocessor(ffmpeg_manager=mock_ffmpeg)

        created_files = []

        def side_effect_extract(input_path, output_path, **kwargs):
            # Create a real small file to track its lifecycle
            with open(output_path, "wb") as f:
                f.write(b"RIFF_FLAC_DUMMY_CONTENT")
            created_files.append(output_path)
            return output_path

        mock_ffmpeg.extract_flac_audio.side_effect = side_effect_extract
        mock_ffmpeg.get_media_duration.return_value = 100.0

        # Create a real dummy input file
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as dummy_input:
            dummy_input_path = dummy_input.name
            dummy_input.write(b"dummy video data")

        try:
            with self.assertRaises(RuntimeError):
                with preprocessor.process(dummy_input_path) as chunks:
                    self.assertEqual(len(chunks), 1)
                    # Verify file exists during context
                    self.assertTrue(os.path.exists(chunks[0].file_path))
                    raise RuntimeError("Simulated crash in worker pipeline!")

            # After exception handled, all created temp files must be removed
            for path in created_files:
                self.assertFalse(
                    os.path.exists(path),
                    f"Temporary file was not cleaned up after exception: {path}"
                )
        finally:
            if os.path.exists(dummy_input_path):
                os.remove(dummy_input_path)

    def test_cleanup_on_successful_completion(self):
        mock_ffmpeg = MagicMock(spec=FFmpegManager)
        preprocessor = AudioPreprocessor(ffmpeg_manager=mock_ffmpeg)

        created_files = []

        def side_effect_extract(input_path, output_path, **kwargs):
            with open(output_path, "wb") as f:
                f.write(b"RIFF_FLAC_DUMMY_CONTENT")
            created_files.append(output_path)
            return output_path

        mock_ffmpeg.extract_flac_audio.side_effect = side_effect_extract
        mock_ffmpeg.get_media_duration.return_value = 120.0

        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as dummy_input:
            dummy_input_path = dummy_input.name
            dummy_input.write(b"dummy video data")

        try:
            with preprocessor.process(dummy_input_path) as chunks:
                self.assertEqual(len(chunks), 1)
                self.assertTrue(os.path.exists(chunks[0].file_path))

            # Context exited normally: temporary files must be cleaned up
            for path in created_files:
                self.assertFalse(
                    os.path.exists(path),
                    f"Temporary file was not cleaned up on normal exit: {path}"
                )
        finally:
            if os.path.exists(dummy_input_path):
                os.remove(dummy_input_path)


if __name__ == "__main__":
    unittest.main()
