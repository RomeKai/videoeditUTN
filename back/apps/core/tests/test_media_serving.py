"""
Local files (USE_R2=False) are served at /media/ by the dev server, so the
proxy video and the clip downloads work without a proxy. Only with DEBUG on:
with it off, or with R2, the route does not exist.
"""
import importlib
import os

import pytest
from django.test import Client
from django.urls import clear_url_caches

import backend.urls


@pytest.fixture
def media_file(settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path)
    os.makedirs(tmp_path / 'demo')
    (tmp_path / 'demo' / 'clip.mp4').write_bytes(b'contenido de prueba')
    return '/media/demo/clip.mp4'


@pytest.fixture
def reload_urls(settings):
    """backend.urls decides when it is imported: load it again with the settings of the test."""
    debug, use_r2 = settings.DEBUG, settings.USE_R2

    def reload():
        clear_url_caches()
        importlib.reload(backend.urls)

    yield reload
    # Back to the routes of the suite, before the next test resolves a URL.
    settings.DEBUG, settings.USE_R2 = debug, use_r2
    reload()


def test_served_with_debug_and_local_files(settings, media_file, reload_urls):
    settings.DEBUG = True
    settings.USE_R2 = False
    reload_urls()
    response = Client().get(media_file)
    assert response.status_code == 200
    assert b''.join(response.streaming_content) == b'contenido de prueba'
    assert Client().get('/media/demo/no-existe.mp4').status_code == 404


def test_not_served_without_debug(settings, media_file, reload_urls):
    settings.DEBUG = False
    settings.USE_R2 = False
    reload_urls()
    assert Client().get(media_file).status_code == 404


def test_not_served_with_r2(settings, media_file, reload_urls):
    settings.DEBUG = True
    settings.USE_R2 = True
    reload_urls()
    assert Client().get(media_file).status_code == 404
