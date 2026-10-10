import cv2
import numpy as np
import logging
import os

logger = logging.getLogger(__name__)

class GameplaySaliencyDetector:
    """
    Analyzes video motion to find the primary gameplay area (Smart Saliency).
    Uses frame difference on downscaled proxies to minimize CPU usage.
    """
    def __init__(self, frame_skip=1.0, proxy_height=360):
        self.frame_skip = frame_skip  # Analyze 1 frame per second
        self.proxy_height = proxy_height

    def find_best_x_center(self, clip) -> int:
        """
        Returns the optimal x_center for the gameplay crop based on motion density.
        Focuses only on the bottom 2/3 of the video to ignore streamer camera.
        """
        try:
            width, height = clip.size
            duration = clip.duration
            
            # 1. Aspect Ratio Check: If video is already narrow, just return center
            if width / height < 1.0:
                return width // 2

            # 2. Define ROI (Region of Interest): Bottom 65% of the screen
            roi_y_start = int(height * 0.35)
            
            # 3. Analyze frames at 1 FPS (more samples for longer clips)
            sample_interval = 1.0 / self.frame_skip
            times_to_check = np.arange(0, duration, sample_interval)
            
            if len(times_to_check) < 2:
                return width // 2
            
            motion_map = np.zeros(width, dtype=np.float32)
            prev_frame_gray = None
            
            # Optimization: Downscale factor for calculation speed
            scale = self.proxy_height / height
            scaled_w = int(width * scale)
            scaled_h = int(height * scale)
            scaled_roi_y = int(roi_y_start * scale)

            for t in times_to_check:
                frame = clip.get_frame(t)
                gray = cv2.cvtColor(frame, cv2.COLOR_RGB2GRAY)
                gray = cv2.resize(gray, (scaled_w, scaled_h))
                roi_gray = gray[scaled_roi_y:, :]

                if prev_frame_gray is not None:
                    diff = cv2.absdiff(prev_frame_gray, roi_gray)
                    _, thresh = cv2.threshold(diff, 25, 255, cv2.THRESH_BINARY)
                    
                    x_motion = np.sum(thresh, axis=0)
                    x_motion_full = np.interp(
                        np.linspace(0, scaled_w, width), 
                        np.arange(scaled_w), 
                        x_motion
                    )
                    cv2.accumulateWeighted(x_motion_full.reshape(1, -1), motion_map.reshape(1, -1), 0.1)

                prev_frame_gray = roi_gray

            # 4. Find the horizontal "Center of Mass"
            total_motion = np.sum(motion_map)
            if total_motion == 0:
                return width // 2
            
            indices = np.arange(width)
            best_x = int(np.sum(indices * motion_map) / total_motion)
            
            # 5. Stability Heuristic: 
            # If the detected center is very close to the geometric center, 
            # or if the motion is very spread out (standard 16:9 game), 
            # snap to the exact center for a cleaner look.
            center_dist = abs(best_x - width // 2)
            if center_dist < (width * 0.05): # Less than 5% deviation
                return width // 2
                
            # Safety bounds: keep at least 10% away from edges
            margin = int(width * 0.1)
            best_x = max(margin, min(width - margin, best_x))
            
            logger.info(f"âœ… SaliencyDetector: Detected stable gameplay x_center at {best_x}px (diff: {center_dist}px)")
            return best_x

        except Exception as e:
            logger.error(f"Error in SaliencyDetector: {type(e).__name__}")
            return clip.w // 2
