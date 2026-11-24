"""CORE-02 (#11): who is speaking, from speech in the audio and mouth movement per tracked face."""

from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
from django.test import SimpleTestCase

from apps.videos.services.face_tracking import Detection, FaceTrack, FaceTracks
from apps.videos.services.speaker_engine import (
    SpeakerTrackingEngine,
    SpeechActivity,
    mouth_activity,
    speaker_at,
    speech_activity,
)

A, B = 0, 1
DURATION = 10.0
W, H = 640, 360
FACE = 120
CENTERS = {A: (160, 180), B: (480, 180)}
COLORS = {A: (200, 60, 60), B: (60, 60, 200)}


def track(face_id, start=0.0, end=DURATION, step=0.5):
    cx, cy = CENTERS[face_id]
    detections = [
        Detection(t=float(t), x=cx - FACE / 2, y=cy - FACE / 2, w=FACE, h=FACE)
        for t in np.arange(start, end + 1e-9, step)
    ]
    return FaceTrack(id=face_id, detections=detections)


def faces_of(*tracks):
    times = sorted({d.t for tr in tracks for d in tr.detections})
    return FaceTracks(list(tracks), times, (W, H))


def clip_stub(duration=DURATION):
    return SimpleNamespace(duration=duration, w=W, h=H, audio=None)


def all_speech(duration=DURATION):
    return SpeechActivity(0.05, np.ones(int(duration / 0.05), dtype=bool))


def activity(talker_by_time, step=0.25, duration=DURATION, loud=20.0, quiet=1.0):
    """Mouth scores per face: ``talker_by_time(t)`` moves the mouth, the other barely."""
    samples = {A: [], B: []}
    for t in np.arange(0, duration, step):
        talker = talker_by_time(float(t))
        for face_id in (A, B):
            samples[face_id].append((float(t), loud if face_id == talker else quiet))
    return samples


class TimelineTests(SimpleTestCase):
    def timeline(self, talker_by_time, speech=None, **engine_kwargs):
        engine = SpeakerTrackingEngine(**engine_kwargs)
        return engine.timeline(
            clip_stub(), faces_of(track(A), track(B)),
            speech=speech or all_speech(), activity=activity(talker_by_time),
        )

    def test_turns_follow_whoever_moves_the_mouth(self):
        timeline = self.timeline(lambda t: A if t < 5 else B)
        self.assertEqual([(s, e, f) for s, e, f in timeline], [(0.0, 5.0, A), (5.0, 10.0, B)])

    def test_a_one_second_blip_does_not_cut(self):
        timeline = self.timeline(lambda t: B if 4 <= t < 5 else A)
        self.assertEqual([f for _, _, f in timeline], [A])

    def test_a_small_difference_does_not_take_the_camera(self):
        engine = SpeakerTrackingEngine()
        acts = {A: [(t, 10.0) for t in np.arange(0, 10, 0.25)], B: [(t, 12.0) for t in np.arange(0, 10, 0.25)]}
        timeline = engine.timeline(clip_stub(), faces_of(track(A), track(B)), speech=all_speech(), activity=acts)
        self.assertEqual([f for _, _, f in timeline], [A])  # A is the primary (lower id on a tie)

    def test_mouth_movement_without_speech_is_ignored(self):
        speaking = np.zeros(200, dtype=bool)
        speaking[:100] = True  # speech only in the first 5 s
        timeline = self.timeline(lambda t: A if t < 5 else B, speech=SpeechActivity(0.05, speaking))
        self.assertEqual([f for _, _, f in timeline], [A])

    def test_when_the_speaker_leaves_the_frame_the_camera_moves(self):
        engine = SpeakerTrackingEngine()
        faces = faces_of(track(A, end=4.0), track(B))
        acts = activity(lambda t: A)  # A "talks" but is gone after 4 s
        acts[A] = [(t, s) for t, s in acts[A] if t <= 4.0]
        timeline = engine.timeline(clip_stub(), faces, speech=all_speech(), activity=acts)
        self.assertEqual(timeline[-1][2], B)
        self.assertEqual(speaker_at(timeline)(8.0), B)

    def test_one_face_or_none(self):
        engine = SpeakerTrackingEngine()
        self.assertEqual(engine.timeline(clip_stub(), faces_of(track(A))), [(0.0, DURATION, A)])
        self.assertEqual(engine.timeline(clip_stub(), FaceTracks([], [], (W, H))), [])

    def test_speaker_at_covers_the_whole_clip(self):
        lookup = speaker_at([(0.0, 5.0, A), (5.0, 10.0, B)])
        self.assertEqual([lookup(0.0), lookup(4.99), lookup(5.0), lookup(10.0)], [A, A, B, B])
        self.assertIsNone(speaker_at([])(1.0))


class SignalTests(SimpleTestCase):
    def frames(self):
        frame = np.full((H, W, 3), 120, dtype=np.uint8)
        return frame, frame.copy()

    def test_mouth_change_counts(self):
        a, b = self.frames()
        cx, cy = CENTERS[A]
        b[int(cy + 0.15 * FACE):int(cy + 0.45 * FACE), cx - 30:cx + 30] = 250  # mouth area
        self.assertGreater(mouth_activity(a, b, (cx, cy, FACE, FACE)), 20)
        self.assertEqual(mouth_activity(a, b, (*CENTERS[B], FACE, FACE)), 0.0)

    def test_moving_or_relighting_the_whole_head_does_not_count(self):
        a, b = self.frames()
        cx, cy = CENTERS[A]
        b[cy - FACE // 2:cy + FACE // 2, cx - FACE // 2:cx + FACE // 2] = 200  # whole face changes
        self.assertLess(mouth_activity(a, b, (cx, cy, FACE, FACE)), 1.0)

    def test_speech_is_found_against_the_noise_floor(self):
        from moviepy import AudioClip

        def sound(t):
            t = np.asarray(t, dtype=float)
            level = np.where(t >= 2.0, 0.5, 0.005)
            wave = level * np.sin(2 * np.pi * 220 * t)
            return np.stack([wave, wave], axis=-1)

        speech = speech_activity(SimpleNamespace(audio=AudioClip(sound, duration=4.0, fps=16000)))
        self.assertLess(speech.fraction(0.0, 1.9), 0.1)
        self.assertGreater(speech.fraction(2.1, 4.0), 0.9)

    def test_no_audio_means_no_speech_gate(self):
        self.assertIsNone(speech_activity(SimpleNamespace(audio=None)))


def talking_clip():
    """Two colored 'faces'; A's mouth moves in the first half, B's in the second; constant voice."""
    from moviepy import AudioClip, VideoClip

    def frame(t):
        image = np.full((H, W, 3), 30, dtype=np.uint8)
        talker = A if t < DURATION / 2 else B
        rng = np.random.default_rng(int(t * 1000))
        for face_id, (cx, cy) in CENTERS.items():
            x1, y1 = cx - FACE // 2, cy - FACE // 2
            image[y1:y1 + FACE, x1:x1 + FACE] = COLORS[face_id]
            if face_id == talker:
                mouth = image[int(cy + 0.15 * FACE):int(cy + 0.42 * FACE), cx - 30:cx + 30]
                mouth[:] = rng.integers(0, 255, size=mouth.shape, dtype=np.uint8)
        return image

    def voice(t):
        t = np.asarray(t, dtype=float)
        wave = 0.3 * np.sin(2 * np.pi * 180 * t) * (1 + 0.5 * np.sin(2 * np.pi * 3 * t))
        return np.stack([wave, wave], axis=-1)

    clip = VideoClip(frame, duration=DURATION).with_fps(24)
    return clip.with_audio(AudioClip(voice, duration=DURATION, fps=16000))


class _FixedFaces:
    """Detector stand-in: the two faces where talking_clip draws them."""

    def detect(self, frame):
        return SimpleNamespace(detections=[
            SimpleNamespace(
                bounding_box=SimpleNamespace(origin_x=cx - FACE // 2, origin_y=cy - FACE // 2, width=FACE, height=FACE),
                categories=[SimpleNamespace(score=0.9)],
            )
            for cx, cy in CENTERS.values()
        ])


class EndToEndTests(SimpleTestCase):
    def test_the_engine_finds_both_turns_on_a_real_clip(self):
        from apps.videos.services.face_tracker import FaceTracker

        clip = talking_clip()
        faces = FaceTracker(detector=_FixedFaces()).track_faces(clip, fps=2.0)

        timeline = SpeakerTrackingEngine().timeline(clip, faces)

        self.assertEqual([f for _, _, f in timeline], [A, B])
        self.assertAlmostEqual(timeline[0][1], 5.0, delta=1.0)

    def test_the_active_speaker_layout_cuts_to_each_speaker(self):
        from apps.videos.services.layouts import get_layout_strategy

        clip = talking_clip()
        with patch("apps.videos.services.layouts.pro.FaceTracker",
                   side_effect=lambda *a, **k: __import__(
                       "apps.videos.services.face_tracker", fromlist=["FaceTracker"]
                   ).FaceTracker(detector=_FixedFaces())):
            output = get_layout_strategy("active", 180, 320).apply(clip)

        def dominant(t):
            pixel = output.get_frame(t)[160, 90].astype(int)
            return A if pixel[0] > pixel[2] else B

        self.assertEqual(output.size, (180, 320))
        self.assertEqual([dominant(1.0), dominant(3.0), dominant(7.0), dominant(9.0)], [A, A, B, B])

    def test_without_faces_the_layout_keeps_the_centered_crop(self):
        from apps.videos.services.layouts import get_layout_strategy

        clip = talking_clip()
        with patch("apps.videos.services.layouts.pro.FaceTracker") as tracker:
            tracker.return_value.track_faces.return_value = FaceTracks([], [], (W, H))
            output = get_layout_strategy("active", 180, 320).apply(clip)
        self.assertEqual(output.size, (180, 320))
