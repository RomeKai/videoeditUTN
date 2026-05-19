import subprocess
import logging
import os
import shutil

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

        logger.info(f"🎞️ [FFMPEG] Starting proxy generation: {input_path} -> {output_path}")
        
        try:
            # Use subprocess.run with capture_output=True to handle errors better
            result = subprocess.run(command, check=True, capture_output=True, text=True)
            logger.info("✅ [FFMPEG] Proxy generated successfully.")
            return output_path
        except subprocess.CalledProcessError as e:
            error_msg = e.stderr or "Unknown error"
            logger.error(f"❌ [FFMPEG] Failed to generate proxy: {error_msg}")
            raise RuntimeError(f"FFmpeg proxy generation failed: {error_msg}")
        except Exception as e:
            logger.error(f"❌ [FFMPEG] Unexpected error: {str(e)}")
            raise e

    @staticmethod
    def cleanup_local_file(file_path: str):
        """Removes a local file if it exists."""
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
                logger.info(f"🧹 [FFMPEG] Cleaned up local file: {file_path}")
        except Exception as e:
            logger.warning(f"⚠️ [FFMPEG] Could not cleanup file {file_path}: {e}")
