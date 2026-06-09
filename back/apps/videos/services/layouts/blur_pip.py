import os
# Disable MediaPipe GPU errors before importing
os.environ['MEDIAPIPE_DISABLE_GPU'] = '1'

import cv2
import numpy as np
from moviepy import CompositeVideoClip, ColorClip
import moviepy.video.fx as vfx 
from apps.videos.services.layouts.interface import BaseLayout
from apps.videos.services.face_tracker import FaceTracker
import logging

logger = logging.getLogger(__name__)

def apply_gaussian_blur(image, sigma=30):
    """Effect function to apply blur using OpenCV."""
    # OpenCV GaussianBlur kernel size must be odd and positive, or (0,0) to use sigma
    return cv2.GaussianBlur(image, (0, 0), sigmaX=sigma, sigmaY=sigma)

class BlurredLayout(BaseLayout):
    """
    Professional 'Blur PIP' (Picture-in-Picture) Layout:
    - Blurred and scaled background.
    - Sharp foreground layer with optional intelligent centering (FaceTracking).
    """
    def apply(self, clip):
        target_w, target_h = self.target_w, self.target_h
        path = None

        # 1. Trajectory (Optional)
        if self.use_facetracking:
            tracker = FaceTracker()
            path = tracker.generate_path(clip, fps=2.0)
            tracker.close()

        # 2. Background Layer (Blurred Background)
        # Scale the video to cover the entire height (or width)
        bg = clip.resized(height=target_h)
        if bg.w < target_w:
            bg = clip.resized(width=target_w)
        
        # Background crop (Fixed to center for stability if no tracking)
        bg = bg.cropped(x_center=bg.w//2, y_center=bg.h//2, width=target_w, height=target_h)
        
        # Blur and darkening effects
        # MoviePy 2.0+ uses with_effects, but for custom CV2 blur we use image_transform or transform
        bg = bg.image_transform(lambda img: apply_gaussian_blur(img, sigma=30))
        bg = bg.with_effects([
            vfx.multiply_color(factor=0.6)
        ])

        # 3. Foreground Layer (Sharp)
        # Adjust video to fit target width
        fg = clip.resized(width=target_w)
        
        if fg.h > target_h:
            # If video is taller than target, crop the height
            if self.use_facetracking and path:
                # Face tracking for vertical crop
                def get_fg_frame(get_frame, t):
                    frame = get_frame(t)
                    _, face_y, _, _ = path.get_at(t)
                    # Scale face_y to the resized clip height (fg)
                    scale_y = fg.h / clip.h
                    y_center = int(face_y * scale_y)
                    
                    y1 = max(0, min(y_center - int(target_h * 0.40), fg.h - target_h))
                    return frame[y1:y1+target_h, :]
                
                fg = fg.transform(get_fg_frame)
            else:
                # Static center crop
                fg = fg.cropped(y_center=fg.h//2, height=target_h)
        
        fg = fg.with_position("center")

        # 4. Final Composition
        layers = [bg, fg]
        final_composition = CompositeVideoClip(layers, size=(target_w, target_h))
        
        if clip.audio:
            final_composition = final_composition.with_audio(clip.audio)
            
        logger.info(f"âœ… BlurredLayout applied (FaceTracking: {self.use_facetracking})")
        return final_composition
