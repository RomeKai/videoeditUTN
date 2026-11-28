"""
Ensayo 6: Clips said «Publicados 0 · Programados 0» and the clip «Listo» after
publishing it, because the clip did not bring its publications. Every clip the
API sends now has `posts` (contract ClipPostRef: id, platform, status and
publish_at), read once for the whole response, never one query per clip.
"""
from datetime import timedelta

from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from apps.videos.models import ScheduledPost, VideoClip, VideoProject
from apps.videos.tests.test_active_workspace import home_of, make_user

Status, Platform = ScheduledPost.Status, ScheduledPost.Platform


def post_reads(captured):
    return [entry['sql'] for entry in captured.captured_queries if 'FROM "videos_scheduledpost"' in entry['sql']]


class ClipPostsTests(APITestCase):
    def setUp(self):
        self.alice = make_user('alice')
        self.home = home_of(self.alice)
        self.client.force_authenticate(self.alice)
        self.project = VideoProject.objects.create(
            workspace=self.home, uploaded_by=self.alice, title='Entrevista', source_file='test/file.mp4',
            status=VideoProject.Status.COMPLETED,
        )
        self.soon = timezone.now() + timedelta(days=1)

    def clip(self, title='Clip', project=None):
        return VideoClip.objects.create(
            project=project or self.project, title=title, start_time=0, end_time=5, status=VideoClip.Status.COMPLETED,
        )

    def post(self, clip, platform, status, minutes=0):
        return ScheduledPost.objects.create(
            video_clip=clip, platform=platform, status=status, publish_at=self.soon + timedelta(minutes=minutes),
        )

    def project_clips(self):
        return {clip['id']: clip for clip in self.client.get(f'/api/v1/projects/{self.project.pk}/').json()['clips']}

    def test_each_clip_brings_its_publications_with_their_status_by_publish_at(self):
        published, other = self.clip('Publicado'), self.clip('Sin publicar')
        scheduled = self.post(published, Platform.INSTAGRAM_REELS, Status.SCHEDULED, minutes=30)
        out = self.post(published, Platform.TIKTOK, Status.PUBLISHED)
        draft = self.post(published, Platform.YOUTUBE_SHORTS, Status.DRAFT, minutes=60)

        clips = self.project_clips()

        self.assertEqual(clips[str(published.pk)]['posts'], [
            {'id': str(out.pk), 'platform': 'TIKTOK', 'status': 'PUBLISHED', 'publish_at': out.publish_at.isoformat().replace('+00:00', 'Z')},
            {'id': str(scheduled.pk), 'platform': 'INSTAGRAM_REELS', 'status': 'SCHEDULED',
             'publish_at': scheduled.publish_at.isoformat().replace('+00:00', 'Z')},
            {'id': str(draft.pk), 'platform': 'YOUTUBE_SHORTS', 'status': 'DRAFT', 'publish_at': draft.publish_at.isoformat().replace('+00:00', 'Z')},
        ])
        self.assertEqual(clips[str(other.pk)]['posts'], [])
        # The clip's own endpoint says the same.
        detail = self.client.get(reverse('clip-detail', args=[published.pk])).json()
        self.assertEqual(detail['posts'], clips[str(published.pk)]['posts'])

    def test_a_project_reads_the_publications_of_its_clips_once(self):
        first = self.clip()
        self.post(first, Platform.TIKTOK, Status.PUBLISHED)
        with CaptureQueriesContext(connection) as one:
            self.client.get(f'/api/v1/projects/{self.project.pk}/')
        for number in range(5):
            clip = self.clip(f'Otro {number}')
            self.post(clip, Platform.TIKTOK, Status.SCHEDULED)
            self.post(clip, Platform.INSTAGRAM_REELS, Status.FAILED)
        with CaptureQueriesContext(connection) as six:
            clips = self.project_clips()

        self.assertEqual(sum(len(clip['posts']) for clip in clips.values()), 11)
        self.assertEqual((len(post_reads(one)), len(post_reads(six))), (1, 1), post_reads(six))

    def test_the_clips_of_the_review_and_a_clip_alone_read_them_once_too(self):
        clips = [self.clip(f'Clip {number}') for number in range(4)]
        for clip in clips:
            self.post(clip, Platform.TIKTOK, Status.PUBLISHED)
        other_project = VideoProject.objects.create(
            workspace=self.home, uploaded_by=self.alice, title='Otro', source_file='test/file.mp4',
        )
        self.post(self.clip('Ajeno', project=other_project), Platform.TIKTOK, Status.PUBLISHED)

        with CaptureQueriesContext(connection) as review:
            suggestions = self.client.get(f'/api/v1/projects/{self.project.pk}/paper-edit-data/').json()['ai_suggestions']
        with CaptureQueriesContext(connection) as alone:
            detail = self.client.get(reverse('clip-detail', args=[clips[0].pk])).json()

        self.assertEqual([len(clip['posts']) for clip in suggestions], [1, 1, 1, 1])
        self.assertEqual(len(detail['posts']), 1)
        self.assertEqual((len(post_reads(review)), len(post_reads(alone))), (1, 1))

    def test_another_workspace_never_shows_its_publications(self):
        clip = self.clip()
        self.post(clip, Platform.TIKTOK, Status.PUBLISHED)
        self.client.force_authenticate(make_user('bob'))
        self.assertEqual(self.client.get(reverse('clip-detail', args=[clip.pk])).status_code, 404)
