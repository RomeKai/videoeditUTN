from moviepy.video.io.VideoFileClip import VideoFileClip

def preparar_video_viral(input_path: str, output_path: str) -> None:
    """
    Prepares a video for viral sharing by resizing it to a vertical format.
    - Resizes the video to 1080x1920 resolution.
    - Ensures strict even-numbered dimensions for compatibility.
    - Uses H.264 codec for encoding.
    
    Parameters:
        input_path (str): The path to the input video file.
        output_path (str): The path where the output video will be saved.
    """
    # Load the video file
    with VideoFileClip(input_path) as video:
        # Resize video to fit vertical format
        resized_video = video.resize(newsize=(1080, 1920))  # Ensures even resolution

        # Write the output file using H.264 codec
        resized_video.write_videofile(output_path, codec='libx264')

# No need to explicitly close video due to 'with' context manager
