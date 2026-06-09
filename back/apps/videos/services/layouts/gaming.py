import os
# Disable MediaPipe GPU errors before importing
os.environ['MEDIAPIPE_DISABLE_GPU'] = '1'

from moviepy import CompositeVideoClip, ColorClip
from apps.videos.services.layouts.interface import BaseLayout
from apps.videos.services.face_tracker import FaceTracker
from apps.videos.services.saliency_tracker import GameplaySaliencyDetector
import logging

logger = logging.getLogger(__name__)

class GamingLayout(BaseLayout):
    """
    Split-screen layout optimized for gaming content (Camera top, Gameplay bottom).
    - Intelligent FaceTracking for the camera.
    - Smart Saliency for the gameplay focus.
    """
    def apply(self, clip, camera_pos='top'):
        target_w, target_h = self.target_w, self.target_h
        
        # Reels Proportion: 35% Camera, 65% Gameplay
        cam_h = int(target_h * 0.35) 
        game_h = target_h - cam_h
        
        # 1. Face Tracking (Camera Layer)
        path = None
        if self.use_facetracking:
            tracker = FaceTracker()
            path = tracker.generate_path(clip, fps=2.0)
            tracker.close()
            avg_w, avg_h = path.get_average_size()
        else:
            avg_w, avg_h = clip.w // 3, clip.h // 3

        cam_target_aspect = target_w / cam_h
        
        face_relative_height = avg_h / clip.h
        if face_relative_height < 0.15:
            multiplier = 1.6
            y_offset = 0.40
        else:
            multiplier = 2.0
            y_offset = 0.35
            
        src_crop_h = int(avg_h * 2.2) if avg_h > 0 else int(clip.h * 0.45)
        src_crop_w = int(src_crop_h * cam_target_aspect)
        
        if src_crop_w > clip.w:
            src_crop_w = clip.w
            src_crop_h = int(src_crop_w / cam_target_aspect)
        if src_crop_h > clip.h:
            src_crop_h = clip.h
            src_crop_w = int(src_crop_h * cam_target_aspect)

        if self.use_facetracking and path:
            def get_face_frame(get_frame, t):
                frame = get_frame(t)
                face_x, face_y, _, _ = path.get_at(t)
                x1 = max(0, min(int(face_x - (src_crop_w // 2)), clip.w - src_crop_w))
                y1 = max(0, min(int(face_y - int(src_crop_h * y_offset)), clip.h - src_crop_h))
                return frame[y1:y1+src_crop_h, x1:x1+src_crop_w]
            cam = clip.transform(get_face_frame)
        else:
            cam = clip.cropped(x_center=clip.w//2, y_center=clip.h//2, width=src_crop_w, height=src_crop_h)

        cam = cam.resized(width=target_w, height=cam_h)
        cam = cam.with_position(('center', 'top'))

        # 2. Gameplay Tracking (Automation Priority)
        game_target_aspect = target_w / game_h
        source_aspect = clip.w / clip.h
        
        # Determine if we should use Saliency or a fixed center
        # Saliency is only needed when we are taking a narrow vertical slice 
        # from a wider horizontal source (e.g. Mobile game inside 16:9 stream)
        is_narrow_slice_needed = source_aspect > game_target_aspect
        
        if not is_narrow_slice_needed:
            # Case A: Source is already vertical or square. Just fit to width.
            game_crop_w = clip.w
            game_crop_h = int(clip.w / game_target_aspect)
            best_x_center = clip.w // 2
        else:
            # Case B: Source is horizontal (16:9). We need a vertical slice (9:16).
            game_crop_h = clip.h
            game_crop_w = int(clip.h * game_target_aspect)
            
            # --- PRIORITY 1: User Manual Override ---
            if self.gameplay_pos == 'left':
                best_x_center = game_crop_w // 2
            elif self.gameplay_pos == 'right':
                best_x_center = clip.w - (game_crop_w // 2)
            elif self.gameplay_pos == 'center':
                best_x_center = clip.w // 2
            else:
                # --- PRIORITY 2: Automatic Saliency (Smart Tracking) ---
                # We analyze the video to see if there is a specific 'slice' with motion
                saliency_detector = GameplaySaliencyDetector(frame_skip=1.0)
                best_x_center = saliency_detector.find_best_x_center(clip)

        # Apply the final calculated center
        game = clip.cropped(x_center=best_x_center, y_center=clip.h//2, width=game_crop_w, height=game_crop_h)
        game = game.resized(width=target_w, height=game_h)
        game = game.with_position(('center', 'bottom'))

        # 3. Composition
        bg = ColorClip(size=(target_w, target_h), color=(0,0,0), duration=clip.duration)
        final_composition = CompositeVideoClip([bg, game, cam], size=(target_w, target_h))
        
        if clip.audio:
            final_composition = final_composition.with_audio(clip.audio)
            
        return final_composition
