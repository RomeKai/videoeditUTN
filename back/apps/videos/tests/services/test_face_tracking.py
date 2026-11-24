"""CORE-01 (#10): multi-face tracking with stable ids, EMA smoothing, gap interpolation."""

from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
from django.test import SimpleTestCase

from apps.videos.services.face_tracking import (
    Detection,
    FaceTracks,
    IoUTracker,
    sampling_fps,
)
from apps.videos.services.tracking_utils import EMASmoothing, TrackingPoint, interpolate_gaps

FPS = 2.0


def face(t, cx, cy=200, size=80, score=0.9):
    return Detection(t=t, x=cx - size / 2, y=cy - size / 2, w=size, h=size, score=score)


def run(frames, **tracker_kwargs):
    tracker = IoUTracker(**tracker_kwargs)
    for i, detections in enumerate(frames):
        tracker.update(i / FPS, detections)
    return tracker


def jitter(i, amplitude=6):
    return amplitude * ((i * 37) % 7 - 3) / 3


class IoUTrackerTests(SimpleTestCase):
    def test_two_people_keep_their_ids_through_jitter_and_missed_frames(self):
        frames = []
        for i in range(20):
            t = i / FPS
            left = face(t, 200 + jitter(i))
            right = face(t, 600 + jitter(i + 3))
            # Right person turns away for two samples; the detector lists faces in any order.
            frame = [left] if i in (7, 8) else ([right, left] if i % 2 else [left, right])
            frames.append(frame)

        tracks = run(frames).tracks()

        self.assertEqual([track.id for track in tracks], [0, 1])
        self.assertTrue(all(d.center[0] < 400 for d in tracks[0].detections))
        self.assertTrue(all(d.center[0] > 400 for d in tracks[1].detections))
        self.assertEqual((len(tracks[0].detections), len(tracks[1].detections)), (20, 18))

    def test_fast_movement_without_overlap_keeps_the_id(self):
        frames = [[face(i / FPS, 100 + 50 * i)] for i in range(10)]  # 0.6 face sizes per sample

        tracks = run(frames).tracks()

        self.assertEqual(len(tracks), 1)
        self.assertEqual(len(tracks[0].detections), 10)

    def test_a_face_gone_for_too_long_comes_back_with_a_new_id(self):
        frames = [[face(i / FPS, 300)] for i in range(4)] + [[] for _ in range(6)] + \
                 [[face((10 + i) / FPS, 300)] for i in range(4)]

        tracks = run(frames, max_missed=4).tracks()

        self.assertEqual([track.id for track in tracks], [0, 1])

    def test_a_short_absence_keeps_the_id(self):
        frames = [[face(i / FPS, 300)] for i in range(4)] + [[] for _ in range(3)] + \
                 [[face((7 + i) / FPS, 300)] for i in range(4)]

        self.assertEqual([track.id for track in run(frames, max_missed=4).tracks()], [0])

    def test_a_one_frame_false_positive_is_dropped(self):
        frames = [[face(i / FPS, 300)] for i in range(6)]
        frames[3] = frames[3] + [face(1.5, 900, size=30)]

        self.assertEqual(len(run(frames).tracks()), 1)

    def test_a_person_who_enters_later_gets_the_next_id(self):
        frames = [[face(i / FPS, 200)] + ([face(i / FPS, 700)] if i >= 10 else []) for i in range(16)]

        tracks = run(frames).tracks()

        self.assertEqual([track.id for track in tracks], [0, 1])
        self.assertAlmostEqual(tracks[1].start, 5.0)

    def test_a_much_bigger_box_nearby_is_not_the_same_face(self):
        frames = [[face(0, 300, size=40)], [face(0.5, 310, size=200)], [face(1.0, 300, size=40)]]

        tracks = run(frames, min_hits=1).tracks()

        self.assertEqual(len(tracks), 2)


class PrimaryAndPathTests(SimpleTestCase):
    def test_the_main_face_is_the_most_present_and_largest(self):
        frames = []
        for i in range(20):
            t = i / FPS
            frame = [face(t, 600, size=160)]  # host: always there, big
            if i % 3 == 0:
                frame.append(face(t, 150, size=60, score=0.99))  # guest: small, intermittent
            frames.append(frame)
        tracker = run(frames, max_missed=4)
        faces = FaceTracks(tracker.tracks(), tracker.sample_times, (1280, 720))

        self.assertEqual(faces.primary().id, 0)
        self.assertEqual(faces.path_for(faces.primary()).get_at(5.0)[0], 600)

    def test_gaps_are_interpolated_not_held(self):
        detections = [face(0.0, 100), face(0.5, 100), face(2.0, 400), face(2.5, 400)]
        tracker = IoUTracker(max_missed=4, max_center_shift=5.0)
        times = [0.0, 0.5, 1.0, 1.5, 2.0, 2.5]
        by_time = {d.t: d for d in detections}
        for t in times:
            tracker.update(t, [by_time[t]] if t in by_time else [])
        track = tracker.tracks()[0]

        raw = interpolate_gaps(track.points(), times)

        self.assertEqual([p.t for p in raw], times)
        self.assertEqual([p.x for p in raw], [100, 100, 200, 300, 400, 400])

    def framing(self, frames):
        tracker = run(frames, max_missed=3)
        return FaceTracks(tracker.tracks(), tracker.sample_times, (1920, 1080)).framing_path()

    def test_a_jump_cut_moves_the_camera_to_where_the_person_is_now(self):
        frames = [[face(i / FPS, 400 if i < 10 else 1500, size=150)] for i in range(20)]

        path = self.framing(frames)

        self.assertEqual(path.get_at(2.0)[0], 400)
        self.assertEqual(path.get_at(4.5)[0], 400)  # no pan before the cut
        self.assertEqual(path.get_at(5.0)[0], 1500)
        self.assertEqual(path.get_at(9.5)[0], 1500)

    def test_a_multicam_podcast_frames_whoever_is_on_screen(self):
        # Host 0-3 s, guest 3-6.5 s, host again 6.5-10 s (one camera per person).
        frames = []
        for i in range(20):
            t = i / FPS
            frames.append([face(t, 600, size=200)] if t < 3 or t >= 6.5 else [face(t, 1400, size=200)])

        path = self.framing(frames)

        for t, expected in ((1.0, 600), (4.0, 1400), (6.0, 1400), (8.0, 600), (9.5, 600)):
            self.assertEqual(path.get_at(t)[0], expected, t)

    def test_with_both_on_screen_the_main_face_keeps_the_camera(self):
        frames = [[face(i / FPS, 600, size=200)] + ([face(i / FPS, 1400, size=120)] if i >= 5 else [])
                  for i in range(20)]

        path = self.framing(frames)

        self.assertEqual({x for x in path.xs}, {600})

    def test_no_faces_means_no_primary(self):
        self.assertIsNone(FaceTracks([], [], (10, 10)).primary())


class EMASmoothingTests(SimpleTestCase):
    def points(self, xs):
        return [TrackingPoint(i * 0.5, x, 100, 50, 50) for i, x in enumerate(xs)]

    def test_constant_stays_constant(self):
        smoothed = EMASmoothing().smooth(self.points([300] * 10))
        self.assertEqual({p.x for p in smoothed}, {300})

    def test_jitter_is_reduced(self):
        xs = [300 + (15 if i % 2 else -15) for i in range(30)]
        smoothed = [p.x for p in EMASmoothing(alpha=0.3).smooth(self.points(xs))]
        self.assertLess(np.std(smoothed[5:-5]), np.std(xs) / 3)

    def test_the_two_way_version_does_not_lag(self):
        xs = list(range(0, 400, 10))  # a face panning at constant speed
        one_way = [p.x for p in EMASmoothing(alpha=0.3, bidirectional=False).smooth(self.points(xs))]
        two_way = [p.x for p in EMASmoothing(alpha=0.3).smooth(self.points(xs))]
        middle = slice(10, 30)
        lag_one = np.mean(np.array(xs[middle]) - np.array(one_way[middle]))
        lag_two = np.mean(np.abs(np.array(xs[middle]) - np.array(two_way[middle])))
        self.assertGreater(lag_one, 15)
        self.assertLess(lag_two, 2)

    def test_the_ends_of_a_steady_pan_stay_on_the_face(self):
        xs = list(range(0, 400, 10))
        smoothed = [p.x for p in EMASmoothing(alpha=0.3).smooth(self.points(xs))]
        self.assertLessEqual(abs(smoothed[0] - xs[0]), 2)
        self.assertLessEqual(abs(smoothed[-1] - xs[-1]), 2)

    def test_short_paths_are_padded_safely(self):
        for n in (3, 4, 5):
            smoothed = EMASmoothing(alpha=0.1).smooth(self.points([100] * n))
            self.assertEqual([p.x for p in smoothed], [100] * n)

    def test_alpha_is_validated(self):
        with self.assertRaises(ValueError):
            EMASmoothing(alpha=0)


class SamplingTests(SimpleTestCase):
    def test_faster_styles_sample_more_often(self):
        self.assertGreater(sampling_fps("dynamic"), sampling_fps("vlog"))
        self.assertGreater(sampling_fps("vlog"), sampling_fps("minimalist"))
        self.assertEqual(sampling_fps(None), 2.0)
        self.assertEqual(sampling_fps("unknown"), 2.0)

    def test_layouts_track_at_the_rate_of_the_edit(self):
        from moviepy import ColorClip

        from apps.videos.services.layouts import get_layout_strategy
        from apps.videos.services.tracking_utils import TrackingPath

        clip = ColorClip(size=(320, 180), color=(0, 0, 0), duration=1)
        path = TrackingPath([TrackingPoint(0, 160, 90, 40, 40)])
        for style, expected in (("dynamic", 3.0), ("minimalist", 1.5), (None, 2.0)):
            with self.subTest(style=style), \
                    patch("apps.videos.services.layouts.standard.FaceTracker") as tracker_class:
                tracker_class.return_value.generate_path.return_value = path
                layout = get_layout_strategy("fill", 180, 320, use_facetracking=True, editing_style=style)
                layout.apply(clip)
                self.assertEqual(tracker_class.return_value.generate_path.call_args.kwargs["fps"], expected)


class _ScriptedDetector:
    """Stands in for MediaPipe: returns the faces scripted for each call, in order."""

    def __init__(self, script):
        self.script = list(script)

    def detect(self, image):
        boxes = self.script.pop(0) if self.script else []
        return SimpleNamespace(detections=[
            SimpleNamespace(
                bounding_box=SimpleNamespace(origin_x=cx - size // 2, origin_y=60, width=size, height=size),
                categories=[SimpleNamespace(score=score)],
            )
            for cx, size, score in boxes
        ])


class FaceTrackerTests(SimpleTestCase):
    def clip(self, duration=4):
        from moviepy import ColorClip

        return ColorClip(size=(640, 360), color=(20, 20, 20), duration=duration)

    def test_the_path_follows_the_main_face_not_the_best_scored_one(self):
        from apps.videos.services.face_tracker import FaceTracker

        # Host on the right, big; a guest on the left scores higher every other sample.
        script = [[(480, 120, 0.80)] + ([(120, 60, 0.99)] if i % 2 == 0 else []) for i in range(16)]
        tracker = FaceTracker(detector=_ScriptedDetector(script))

        path = tracker.generate_path(self.clip(), fps=4.0)

        xs = {tracker_x for tracker_x in path.xs}
        self.assertEqual(xs, {480})  # the old max(score) path jumped between 120 and 480

    def test_track_faces_reports_both_people(self):
        from apps.videos.services.face_tracker import FaceTracker

        script = [[(480, 120, 0.8), (120, 100, 0.9)] for _ in range(16)]
        faces = FaceTracker(detector=_ScriptedDetector(script)).track_faces(self.clip(), fps=4.0)

        self.assertEqual(len(faces.tracks), 2)
        self.assertEqual(len(faces.sample_times), 16)
        self.assertEqual(faces.frame_size, (640, 360))

    def test_without_a_detector_the_path_is_the_center(self):
        from apps.videos.services.face_tracker import FaceTracker

        tracker = FaceTracker.__new__(FaceTracker)
        tracker.detector = None
        self.assertEqual(tracker.generate_path(self.clip()).get_at(1.0)[:2], (320, 180))

    def test_no_face_found_is_the_center(self):
        from apps.videos.services.face_tracker import FaceTracker

        tracker = FaceTracker(detector=_ScriptedDetector([]))
        self.assertEqual(tracker.generate_path(self.clip(), fps=2.0).get_at(1.0)[:2], (320, 180))

    def test_a_tracker_that_fails_every_frame_says_so_loudly(self):
        from apps.videos.services.face_tracker import FaceTracker

        class Broken:
            def detect(self, frame):
                raise OSError("libGLESv2.so.2: cannot open shared object file")

        with self.assertLogs("apps.videos.services.face_tracker", level="WARNING") as logs:
            path = FaceTracker(detector=Broken()).generate_path(self.clip(), fps=4.0)
        self.assertTrue(any("face_tracker.all_frames_failed" in line for line in logs.output))
        # 16 failed frames: three warnings and the summary, not one line per frame.
        self.assertEqual(sum("face_tracker.frame_failed" in line for line in logs.output), 3)
        self.assertFalse(any("libGLES" in line for line in logs.output))
        self.assertEqual(path.get_at(1.0)[:2], (320, 180))

    def test_a_failed_detector_load_is_not_retried_for_every_clip(self):
        from apps.videos.services import face_tracker as module

        if not module.MEDIAPIPE_TASKS_AVAILABLE:
            self.skipTest("MediaPipe is not available here")
        key = 0.123
        self.addCleanup(module._detectors.pop, key, None)
        with patch.object(module.vision.FaceDetector, "create_from_options", side_effect=RuntimeError("x")) as create:
            self.assertIsNone(module.get_face_detector(key))
            self.assertIsNone(module.get_face_detector(key))
        create.assert_called_once()

    def test_the_detector_is_loaded_once_per_process(self):
        from apps.videos.services.face_tracker import FaceTracker, get_face_detector

        first = get_face_detector()
        if first is None:
            self.skipTest("MediaPipe or the face model is not available here")
        self.assertIs(get_face_detector(), first)
        tracker = FaceTracker()
        tracker.close()  # a tracker closing must not close the shared detector
        self.assertIs(FaceTracker().detector, first)
        # The real MediaPipe path on a real frame: fails without libEGL/libGLESv2.
        frame = self.clip().get_frame(0.5)
        self.assertEqual(FaceTracker().detect(frame, 0.5), [])
