import os
# Disable MediaPipe GPU errors before importing
os.environ['MEDIAPIPE_DISABLE_GPU'] = '1'

from moviepy import CompositeVideoClip, ColorClip
from apps.videos.services.layouts.camera import Box, ManualCamera, covers, frame_box, manual_box, place, preset_region
from apps.videos.services.layouts.interface import BaseLayout
from apps.videos.services.face_tracker import FaceTracker
import logging

logger = logging.getLogger(__name__)


def camera_size(frame_w, frame_h, aspect, face_h):
    """
    Crop size of the camera pane: 2.2 face heights (45 % of the frame height
    when the size is unknown), with the pane's aspect, inside the frame.
    """
    src_crop_h = int(face_h * 2.2) if face_h > 0 else int(frame_h * 0.45)
    src_crop_w = int(src_crop_h * aspect)
    if src_crop_w > frame_w:
        src_crop_w = frame_w
        src_crop_h = int(src_crop_w / aspect)
    if src_crop_h > frame_h:
        src_crop_h = frame_h
        src_crop_w = int(src_crop_h * aspect)
    return src_crop_w, src_crop_h


def face_y_offset(face_h, frame_h):
    """Share of the crop above the face: a small face sits a bit lower."""
    return 0.40 if face_h / frame_h < 0.15 else 0.35


def tracked_box(face_x, face_y, w, h, y_offset, frame_w, frame_h):
    """The camera crop around a face, inside the frame."""
    x1 = max(0, min(int(face_x - (w // 2)), frame_w - w))
    y1 = max(0, min(int(face_y - int(h * y_offset)), frame_h - h))
    return Box(x1, y1, w, h)


def static_box(region, w, h, frame_w, frame_h):
    """A fixed crop centred on the region (and on the frame height), inside the frame."""
    return place(region.x1 + region.w // 2, frame_h // 2, w, h, frame_box(frame_w, frame_h))


class GamingLayout(BaseLayout):
    """
    Split-screen layout for gaming content: camera on top (35 % of the
    height), gameplay below (65 %).

    Camera pane, by priority (see camera.py):
    1. Manual camera set (``manual_camera_x/y/zoom`` changed from the
       defaults): a fixed crop of the automatic size (``camera_size``)
       divided by the zoom, centred on (x, y). Faces are not tracked.
    2. ``camera_selection_position`` picks the region: left half, right
       half, or the whole frame for ``center``. With face tracking the crop
       follows the main face found in that region; without it the crop is
       fixed and centred on the region.

    Gameplay pane: from a wide source, a vertical slice at
    ``gameplay_position`` (left, center or right).
    """
    def apply(self, clip, camera_pos='top'):
        target_w, target_h = self.target_w, self.target_h

        # Reels Proportion: 35% Camera, 65% Gameplay
        cam_h = int(target_h * 0.35)
        game_h = target_h - cam_h

        # 1. Camera Layer
        cam = self._camera(clip, target_w / cam_h)
        cam = cam.resized(new_size=(target_w, cam_h))
        cam = cam.with_position(('center', 'top'))

        # 2. Gameplay Layer
        game_target_aspect = target_w / game_h
        source_aspect = clip.w / clip.h

        # A narrow vertical slice is only needed from a wider horizontal
        # source (e.g. a mobile game inside a 16:9 stream).
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

            if self.gameplay_pos == 'left':
                best_x_center = game_crop_w // 2
            elif self.gameplay_pos == 'right':
                best_x_center = clip.w - (game_crop_w // 2)
            else:
                best_x_center = clip.w // 2

        # Apply the final calculated center
        game = clip.cropped(x_center=best_x_center, y_center=clip.h//2, width=game_crop_w, height=game_crop_h)
        game = game.resized(new_size=(target_w, game_h))
        game = game.with_position(('center', 'bottom'))

        # 3. Composition
        bg = ColorClip(size=(target_w, target_h), color=(0,0,0), duration=clip.duration)
        final_composition = CompositeVideoClip([bg, game, cam], size=(target_w, target_h))
        
        if clip.audio:
            final_composition = final_composition.with_audio(clip.audio)

        return final_composition

    def _camera(self, clip, aspect):
        """The camera crop of the source (not resized yet)."""
        manual = ManualCamera.from_coords(self.manual_camera_coords)
        if manual is not None:
            # Zoom 1 is the automatic crop: turning the manual camera on only moves it.
            base_w, base_h = camera_size(clip.w, clip.h, aspect, clip.h // 3)
            box = manual_box(manual, frame_box(clip.w, clip.h), base_w, base_h)
            logger.info("layout.split_camera mode=manual")
            return _crop(clip, box)

        region = preset_region(self.camera_selection_pos, clip.w, clip.h)
        path = None
        if self.use_facetracking:
            path = self._face_path(clip, region)
            _, face_h = path.get_average_size()
        else:
            face_h = clip.h // 3

        src_crop_w, src_crop_h = camera_size(clip.w, clip.h, aspect, face_h)

        if path is not None:
            y_offset = face_y_offset(face_h, clip.h)

            def get_face_frame(get_frame, t):
                frame = get_frame(t)
                face_x, face_y, _, _ = path.get_at(t)
                box = tracked_box(
                    face_x + region.x1, face_y + region.y1, src_crop_w, src_crop_h, y_offset, clip.w, clip.h,
                )
                return frame[box.y1:box.y1 + box.h, box.x1:box.x1 + box.w]
            return clip.transform(get_face_frame)
        return _crop(clip, static_box(region, src_crop_w, src_crop_h, clip.w, clip.h))

    def _face_path(self, clip, region):
        """Path of the main face inside the region, in the region's coordinates."""
        source = clip if covers(region, clip.w, clip.h) else _crop(clip, region)
        tracker = FaceTracker()
        path = tracker.generate_path(source, fps=self.tracking_fps)
        tracker.close()
        return path


def _crop(clip, box):
    return clip.cropped(x1=box.x1, y1=box.y1, width=box.w, height=box.h)
