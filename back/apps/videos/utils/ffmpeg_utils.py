import subprocess
import logging
import os
import shutil
from typing import Optional, List, Tuple

logger = logging.getLogger(__name__)

class FFmpegManager:
    """
    Utility class for FFmpeg operations, optimized for web proxy generation.
    """

    @staticmethod
    def generate_web_proxy(input_path: str, output_path: str) -> str:
        """
        Generates an ultra-lightweight web proxy (<480p) optimized for streaming.
        
        Parameters:
        - Resolution: max 480p (scaled maintaining aspect ratio).
        - Video Codec: libx264, profile main, preset fast, crf 28.
        - Audio Codec: aac, bitrate 96k.
        - Optimization: -movflags +faststart (for immediate playback).
        """
        if not os.path.exists(input_path):
            raise FileNotFoundError(f"Input file not found: {input_path}")

        # Command for generating web proxy
        # -vf scale=-2:480 ensures height is 480 and width is proportional (must be even for libx264)
        command = [
            'ffmpeg', '-y',
            '-i', input_path,
            '-vf', 'scale=-2:480',
            '-vcodec', 'libx264',
            '-profile:v', 'main',
            '-preset', 'fast',
            '-crf', '28',
            '-acodec', 'aac',
            '-b:a', '96k',
            '-movflags', '+faststart',
            output_path
        ]

        logger.info(f"ðŸŽžï¸ [FFMPEG] Starting proxy generation: {input_path} -> {output_path}")
        
        try:
            # Use subprocess.run with capture_output=True to handle errors better
            result = subprocess.run(command, check=True, capture_output=True, text=True)
            logger.info("âœ… [FFMPEG] Proxy generated successfully.")
            return output_path
        except subprocess.CalledProcessError as e:
            error_msg = e.stderr or "Unknown error"
            logger.error(f"âŒ [FFMPEG] Failed to generate proxy: {error_msg}")
            raise RuntimeError(f"FFmpeg proxy generation failed: {error_msg}")
        except Exception as e:
            logger.error(f"âŒ [FFMPEG] Unexpected error: {str(e)}")
            raise e

    @staticmethod
    def cleanup_local_file(file_path: str):
        """Removes a local file if it exists."""
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
                logger.info(f"ðŸ§¹ [FFMPEG] Cleaned up local file: {file_path}")
        except Exception as e:
            logger.warning(f"âš ï¸  [FFMPEG] Could not cleanup file {file_path}: {e}")

    @staticmethod
    def extract_flac_audio(
        input_path: str,
        output_path: str,
        start_time: Optional[float] = None,
        duration: Optional[float] = None
    ) -> str:
        """
        Extracts speech-optimized mono 16kHz FLAC audio from a media file.
        Uses: -vn -ac 1 -ar 16000 -c:a flac

        Args:
            input_path: Source video or audio file path.
            output_path: Target .flac file path.
            start_time: Optional start offset in seconds.
            duration: Optional segment duration in seconds.
        """
        if not os.path.exists(input_path):
            raise FileNotFoundError(f"Input file not found: {input_path}")

        command = ['ffmpeg', '-y']

        # Fast seeking with -ss before -i if start_time provided
        if start_time is not None and start_time > 0:
            command.extend(['-ss', str(start_time)])

        command.extend(['-i', input_path])

        if duration is not None and duration > 0:
            command.extend(['-t', str(duration)])

        # Audio extraction flags: no video, 1 audio channel, 16kHz sample rate, flac codec
        command.extend(['-vn', '-ac', '1', '-ar', '16000', '-c:a', 'flac', output_path])

        logger.info(f"🎙️ [FFMPEG] Extracting FLAC: {input_path} (start={start_time}, dur={duration}) -> {output_path}")

        try:
            subprocess.run(command, check=True, capture_output=True, text=True)
            return output_path
        except subprocess.CalledProcessError as e:
            error_msg = e.stderr or "Unknown error"
            logger.error(f"❌ [FFMPEG] Failed to extract FLAC audio: {error_msg}")
            raise RuntimeError(f"FFmpeg audio extraction failed: {error_msg}")
        except Exception as e:
            logger.error(f"❌ [FFMPEG] Unexpected error extracting audio: {str(e)}")
            raise e

    @staticmethod
    def get_media_duration(file_path: str) -> float:
        """
        Retrieves the exact media duration in seconds using ffprobe.
        """
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"File not found: {file_path}")

        command = [
            'ffprobe',
            '-v', 'error',
            '-show_entries', 'format=duration',
            '-of', 'default=noprint_wrappers=1:nokey=1',
            file_path
        ]

        try:
            result = subprocess.run(command, check=True, capture_output=True, text=True)
            duration_str = result.stdout.strip()
            return float(duration_str)
        except (subprocess.CalledProcessError, ValueError) as e:
            logger.error(f"❌ [FFMPEG] Failed to get duration for {file_path}: {e}")
            raise RuntimeError(f"Could not determine media duration for {file_path}: {e}")

    @staticmethod
    def remove_silences_from_video(input_path: str, output_path: str, noise_threshold: int = -30, duration: float = 0.5) -> str:
        """
        Applies a 'Jump Cut' effect by removing silences from the video.
        Uses the silenceremove filter from FFmpeg.
        
        noise_threshold: dB level to consider as silence (default -30dB).
        duration: minimum silence duration in seconds to trigger removal (default 0.5s).
        """
        if not os.path.exists(input_path):
            raise FileNotFoundError(f"Input file not found: {input_path}")

        # silenceremove=stop_periods=-1:stop_duration=0.5:stop_threshold=-30dB
        # -periods: 0 (start), -1 (anywhere)
        # -duration: time to detect
        # -threshold: volume level
        command = [
            'ffmpeg', '-y',
            '-i', input_path,
            '-af', f'silenceremove=stop_periods=-1:stop_duration={duration}:stop_threshold={noise_threshold}dB',
            '-vcodec', 'copy', # Copy video stream to avoid re-encoding if possible
            output_path
        ]

        logger.info(f"✂️ [FFMPEG] Removing silences: {input_path}")
        
        try:
            subprocess.run(command, check=True, capture_output=True, text=True)
            logger.info("✅ [FFMPEG] Silences removed successfully.")
            return output_path
        except subprocess.CalledProcessError as e:
            logger.error(f"❌ [FFMPEG] Failed to remove silences: {e.stderr}")
            # Fallback: if silenceremove fails, return original
            return input_path
