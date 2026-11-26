"""
CORE-03 · Opciones propias de los layouts nuevos en la API: la esquina del PiP
y el color y el texto de trending. Se guardan como columnas del proyecto y el
render las pasa al layout con VideoProject.layout_options().
"""
from django.urls import reverse
from rest_framework import status

from apps.videos.models import VideoProject

from .test_api import PrivateVideoProjectAPITests


class LayoutOptionsAPITests(PrivateVideoProjectAPITests):
    def _patch(self, payload):
        return self.client.patch(reverse('project-detail', args=[self.project1.id]), payload, format='json')

    def test_defaults(self):
        res = self.client.get(reverse('project-detail', args=[self.project1.id]))
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['pip_corner'], 'bottom-right')
        self.assertEqual(res.data['trending_color'], '#FF2D55')
        self.assertEqual(res.data['trending_text'], '')

    def test_new_layouts_and_their_options_are_saved(self):
        res = self._patch({
            'render_layout': 'trending', 'pip_corner': 'top-left',
            'trending_color': '#00ff80', 'trending_text': 'Lo que nadie te cuenta',
        })
        self.assertEqual(res.status_code, status.HTTP_200_OK, res.data)
        self.project1.refresh_from_db()
        self.assertEqual(self.project1.render_layout, VideoProject.Layout.TRENDING)
        self.assertEqual(self.project1.layout_options(), {
            'pip_corner': 'top-left', 'trending_color': '#00ff80', 'trending_text': 'Lo que nadie te cuenta',
        })

    def test_podcast_is_a_layout(self):
        res = self._patch({'render_layout': 'podcast'})
        self.assertEqual(res.status_code, status.HTTP_200_OK, res.data)

    def test_invalid_options_are_a_400_on_the_field(self):
        for payload, field in (
            ({'trending_color': 'rojo'}, 'trending_color'),
            ({'trending_color': '#FFF'}, 'trending_color'),
            ({'pip_corner': 'center'}, 'pip_corner'),
            ({'trending_text': 'x' * 61}, 'trending_text'),
        ):
            with self.subTest(payload=payload):
                res = self._patch(payload)
                self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertIn(field, res.data)


def test_compose_clip_passes_the_layout_options(monkeypatch):
    """El render usa las opciones del proyecto, no las de cada layout por defecto."""
    from unittest.mock import MagicMock

    from apps.videos.services import render_engine

    project = VideoProject(render_layout='pip', pip_corner='top-left', add_subtitles=False)
    captured = {}

    def fake_strategy(name, w, h, **kwargs):
        captured.update(kwargs)
        return MagicMock()

    monkeypatch.setattr(render_engine, 'VideoFileClip', MagicMock())
    monkeypatch.setattr(render_engine, 'get_layout_strategy', fake_strategy)
    render_engine.RenderEngine.compose_clip(project, MagicMock(start_time=0, end_time=1), 'x.mp4', 360, 640)
    assert captured['layout_options']['pip_corner'] == 'top-left'
