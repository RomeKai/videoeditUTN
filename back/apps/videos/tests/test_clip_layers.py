"""
Review of #192: the project detail read the layers of its clips with one query
per clip. Every clip the API sends brings its `layers`, read once for the whole
response, as its `posts` are (test_clip_posts.py).
"""
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from rest_framework.test import APITestCase

from apps.videos.models import ClipLayer, VideoClip, VideoProject
from apps.videos.tests.test_active_workspace import home_of, make_user


def layer_reads(captured):
    return [entry['sql'] for entry in captured.captured_queries if 'FROM "videos_cliplayer"' in entry['sql']]


class ClipLayersTests(APITestCase):
    def setUp(self):
        self.alice = make_user('alice')
        self.home = home_of(self.alice)
        self.client.force_authenticate(self.alice)
        self.project = VideoProject.objects.create(
            workspace=self.home, uploaded_by=self.alice, title='Entrevista', source_file='test/file.mp4',
            status=VideoProject.Status.COMPLETED,
        )

    def clip(self, title, project=None, layers=1):
        clip = VideoClip.objects.create(
            project=project or self.project, title=title, start_time=0, end_time=5, status=VideoClip.Status.COMPLETED,
        )
        for number in range(layers):
            ClipLayer.objects.create(clip=clip, layer_type=ClipLayer.LayerType.TEXT, content=f'{title} {number}')
        return clip

    def read(self, url):
        with CaptureQueriesContext(connection) as captured:
            response = self.client.get(url)
        self.assertEqual(response.status_code, 200, response.content)
        return response.json(), captured

    def test_the_queries_do_not_grow_with_the_clips(self):
        first = self.clip('Primero')
        urls = {
            'project': f'/api/v1/projects/{self.project.pk}/',
            'paper-edit-data': f'/api/v1/projects/{self.project.pk}/paper-edit-data/',
            'clip': reverse('clip-detail', args=[first.pk]),
        }
        one = {name: self.read(url)[1] for name, url in urls.items()}
        for number in range(4):
            self.clip(f'Otro {number}', layers=2)
        five = {name: self.read(url) for name, url in urls.items()}

        for name in urls:
            with self.subTest(name):
                self.assertEqual(len(five[name][1]), len(one[name]), layer_reads(five[name][1]))
                self.assertEqual((len(layer_reads(one[name])), len(layer_reads(five[name][1]))), (1, 1))
        project_clips = five['project'][0]['clips']
        self.assertEqual(sorted(len(clip['layers']) for clip in project_clips), [1, 2, 2, 2, 2])
        self.assertEqual(sorted(len(clip['layers']) for clip in five['paper-edit-data'][0]['ai_suggestions']), [1, 2, 2, 2, 2])
        self.assertEqual([layer['content'] for layer in five['clip'][0]['layers']], ['Primero 0'])

    def test_each_clip_brings_its_own_layers(self):
        mine, other = self.clip('Mío', layers=2), self.clip('Otro', layers=0)
        elsewhere = VideoProject.objects.create(
            workspace=self.home, uploaded_by=self.alice, title='Otro proyecto', source_file='test/file.mp4',
        )
        self.clip('Ajeno', project=elsewhere)

        clips = {clip['id']: clip for clip in self.read(f'/api/v1/projects/{self.project.pk}/')[0]['clips']}

        layers = clips[str(mine.pk)]['layers']
        self.assertEqual(sorted(layer['content'] for layer in layers), ['Mío 0', 'Mío 1'])
        self.assertEqual({layer['clip'] for layer in layers}, {str(mine.pk)})
        self.assertEqual(clips[str(other.pk)]['layers'], [])
        # The list of projects reads them once too, for every row of the page.
        rows, captured = self.read('/api/v1/projects/')
        self.assertEqual(
            {row['title']: sorted(len(clip['layers']) for clip in row['clips']) for row in rows['results']},
            {'Entrevista': [0, 2], 'Otro proyecto': [1]},
        )
        self.assertEqual(len(layer_reads(captured)), 1)
