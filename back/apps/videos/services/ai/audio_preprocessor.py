"""
Audio Preprocessor for AI Core.

Converts input video/audio to 16 kHz mono FLAC and divides large files
into overlapping windows without loading full files into system memory.
"""

import logging
import os
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Generator, List, Optional, Tuple

from apps.videos.utils.ffmpeg_utils import FFmpegManager

logger = logging.getLogger(__name__)


@dataclass
class AudioChunk:
    """
    Metadata and filesystem reference for an extracted audio chunk.
    """
    file_path: str
    start_time: float
    end_time: float
    duration: float
    chunk_index: int
    size_bytes: int
    is_single_chunk: bool = False


class AudioPreprocessor:
    """
    Extracts speech-optimized mono 16 kHz FLAC audio and slices it
    into 600-second windows with 2-second overlap if size exceeds 24 MB.
    Guarantees cleanup of all temporary files even on unhandled exceptions.
    """

    MAX_SINGLE_CHUNK_SIZE_BYTES: int = 24 * 1024 * 1024  # 24 MB
    WINDOW_DURATION_SECONDS: float = 600.0  # 10 minutes
    OVERLAP_SECONDS: float = 2.0  # 2 seconds overlap for boundary continuity

    def __init__(self, ffmpeg_manager: Optional[FFmpegManager] = None):
        self.ffmpeg = ffmpeg_manager or FFmpegManager()

    @staticmethod
    def calculate_windows(
        total_duration: float,
        window_duration: float = 600.0,
        overlap: float = 2.0
    ) -> List[Tuple[float, float]]:
        """
        Calculates (start, end) time ranges for slicing audio.

        Args:
            total_duration: Total audio duration in seconds.
            window_duration: Size of each window in seconds (default: 600s).
            overlap: Overlap duration in seconds between consecutive windows (default: 2s).

        Returns:
            List of (start_seconds, end_seconds) tuples.
        """
        if total_duration <= 0:
            return []

        if total_duration <= window_duration:
            return [(0.0, round(total_duration, 3))]

        windows: List[Tuple[float, float]] = []
        step = window_duration - overlap
        if step <= 0:
            raise ValueError("Window duration must be strictly greater than overlap.")

        start = 0.0
        while start < total_duration:
            end = min(start + window_duration, total_duration)
            windows.append((round(start, 3), round(end, 3)))
            if end >= total_duration:
                break
            start += step

        return windows

    @contextmanager
    def process(self, input_path: str) -> Generator[List[AudioChunk], None, None]:
        """
        Context manager that preprocesses media into FLAC chunk(s).
        All generated temporary files on disk are automatically deleted on context exit.

        Usage:
            preprocessor = AudioPreprocessor()
            with preprocessor.process("video.mp4") as chunks:
                for chunk in chunks:
                    send_to_groq_whisper(chunk.file_path)
        """
        temp_files_to_cleanup: List[str] = []

        try:
            if not os.path.exists(input_path):
                raise FileNotFoundError(f"Input media file not found: {input_path}")

            # 1. Extract base audio to 16kHz mono FLAC
            base_temp = tempfile.NamedTemporaryFile(
                prefix="aicore_base_",
                suffix=".flac",
                delete=False
            )
            base_temp.close()
            temp_files_to_cleanup.append(base_temp.name)

            self.ffmpeg.extract_flac_audio(input_path=input_path, output_path=base_temp.name)

            file_size = os.path.getsize(base_temp.name)
            total_duration = self.ffmpeg.get_media_duration(base_temp.name)

            # 2. Files under 24 MB are processed as a single chunk
            if file_size < self.MAX_SINGLE_CHUNK_SIZE_BYTES:
                logger.info(
                    f"📦 [AudioPreprocessor] Audio is {file_size / (1024*1024):.2f} MB (< 24 MB). "
                    f"Using single chunk (duration: {total_duration:.2f}s)."
                )
                single_chunk = AudioChunk(
                    file_path=base_temp.name,
                    start_time=0.0,
                    end_time=total_duration,
                    duration=total_duration,
                    chunk_index=0,
                    size_bytes=file_size,
                    is_single_chunk=True,
                )
                yield [single_chunk]

            else:
                # 3. Files >= 24 MB are split into 600s windows with 2s overlap
                logger.info(
                    f"✂️ [AudioPreprocessor] Audio is {file_size / (1024*1024):.2f} MB (>= 24 MB). "
                    f"Splitting into {self.WINDOW_DURATION_SECONDS}s windows with {self.OVERLAP_SECONDS}s overlap."
                )
                windows = self.calculate_windows(
                    total_duration=total_duration,
                    window_duration=self.WINDOW_DURATION_SECONDS,
                    overlap=self.OVERLAP_SECONDS,
                )

                chunks: List[AudioChunk] = []
                for idx, (w_start, w_end) in enumerate(windows):
                    chunk_temp = tempfile.NamedTemporaryFile(
                        prefix=f"aicore_chunk_{idx}_",
                        suffix=".flac",
                        delete=False
                    )
                    chunk_temp.close()
                    temp_files_to_cleanup.append(chunk_temp.name)

                    w_duration = w_end - w_start
                    self.ffmpeg.extract_flac_audio(
                        input_path=base_temp.name,
                        output_path=chunk_temp.name,
                        start_time=w_start,
                        duration=w_duration,
                    )

                    chunk_size = os.path.getsize(chunk_temp.name)
                    chunks.append(
                        AudioChunk(
                            file_path=chunk_temp.name,
                            start_time=w_start,
                            end_time=w_end,
                            duration=w_duration,
                            chunk_index=idx,
                            size_bytes=chunk_size,
                            is_single_chunk=False,
                        )
                    )

                logger.info(f"✅ [AudioPreprocessor] Successfully generated {len(chunks)} audio chunks.")
                yield chunks

        finally:
            # 4. Guarantee deletion of all temporary files regardless of success or failure
            for path in temp_files_to_cleanup:
                try:
                    if os.path.exists(path):
                        os.remove(path)
                        logger.debug(f"🧹 [AudioPreprocessor] Removed temporary file: {path}")
                except Exception as cleanup_err:
                    logger.warning(f"⚠️ [AudioPreprocessor] Could not delete temp file {path}: {type(cleanup_err).__name__}")

    @classmethod
    @contextmanager
    def process_audio(
        cls,
        input_path: str,
        ffmpeg_manager: Optional[FFmpegManager] = None
    ) -> Generator[List[AudioChunk], None, None]:
        """
        Class-level convenience context manager.
        """
        instance = cls(ffmpeg_manager=ffmpeg_manager)
        with instance.process(input_path) as chunks:
            yield chunks
