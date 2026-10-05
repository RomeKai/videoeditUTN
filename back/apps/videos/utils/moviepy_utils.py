# --- Rendering Constants ---
RENDER_FPS = 24
RENDER_CODEC = 'libx264'
RENDER_AUDIO_CODEC = 'aac'
RENDER_PRESET = 'medium'
FFMPEG_PARAMS = ['-pix_fmt', 'yuv420p']


def ensure_even(val: float | int) -> int:
    """
    Ensures dimension is an even integer to prevent H.264 / libx264 codec crashes.
    Odd dimensions cause ffmpeg error: 'width not divisible by 2'.
    """
    val_int = int(round(val))
    return val_int if val_int % 2 == 0 else val_int + 1


def preparar_video_viral(input_path: str, output_path: str) -> None:
    """
    Prepares a video for viral sharing by resizing it to a vertical format.
    - Resizes the video to 1080x1920 resolution.
    - Ensures strict even-numbered dimensions for compatibility.
    - Uses H.264 codec for encoding.
    """
    from moviepy import VideoFileClip

    with VideoFileClip(input_path) as video:
        resized_video = video.resized(new_size=(1080, 1920))
        resized_video.write_videofile(
            output_path,
            codec=RENDER_CODEC,
            fps=RENDER_FPS,
            preset=RENDER_PRESET,
            ffmpeg_params=FFMPEG_PARAMS,
        )

