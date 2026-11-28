"""
Local files (USE_R2=False): the /media/ URLs the API answers are absolute.

The web app is served from another origin than Django, so a relative
``/media/...`` was asked to the web app, which has no such file: the review
video did not load. The origin is the one of the request (its ``Host``, also
behind a proxy that forwards it). Without a request (a Celery task) it is
``MEDIA_PUBLIC_BASE_URL``, and without that the path stays relative, as before.
R2 URLs are signed and absolute already: nothing changes for them.

The one URL that is never relative is the one the publisher hands to the provider
(provider_media_url): ensayo 6 saw a double of Ayrshare get ``/media/...``.
"""
import os
from unittest.mock import MagicMock, patch

import pytest
from django.contrib.auth import get_user_model
from django.test import RequestFactory
from django.utils import timezone
from rest_framework.test import APIClient

from apps.users.models import Workspace
from apps.videos.models import ClipPreview, VideoClip, VideoProject
from apps.videos.services import storage_service
from apps.videos.services.clip_preview import preview_url
from apps.videos.services.storage_service import CloudflareR2Manager, absolute_media_url, provider_media_url

pytestmark = pytest.mark.django_db

# What the demo's nginx forwards (docker/demo/nginx.conf: `proxy_set_header Host $http_host`).
PROXIED_HOST = 'localhost:8080'


@pytest.fixture(autouse=True)
def local_storage(settings):
    settings.USE_R2 = False
    settings.MEDIA_URL = '/media/'
    settings.MEDIA_PUBLIC_BASE_URL = ''


@pytest.fixture
def source(settings):
    folder = os.path.join(str(settings.MEDIA_ROOT), 'videos', 'raw')
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, 'source.mp4')
    with open(path, 'wb') as handle:
        handle.write(b'fake-mp4-bytes')
    return path


@pytest.fixture
def project(source):
    user = get_user_model().objects.create_user(username='media', email='media@test.com', password='pw')
    return VideoProject.objects.create(
        workspace=Workspace.objects.get(owner=user), uploaded_by=user, title='Con proxy', proxy_r2_key=source,
    )


def client_of(project):
    client = APIClient()
    client.force_authenticate(project.uploaded_by)
    return client


class TestUrlBuilder:
    def test_with_a_request_it_is_absolute(self, source):
        request = RequestFactory().get('/api/v1/projects/')
        url = CloudflareR2Manager.generate_presigned_url(source, request=request)
        assert url == 'http://testserver/media/videos/raw/source.mp4'

    def test_a_proxied_host_gives_the_public_origin(self, source):
        request = RequestFactory().get('/api/v1/projects/', HTTP_HOST=PROXIED_HOST)
        url = CloudflareR2Manager.generate_presigned_url(source, request=request)
        assert url == 'http://localhost:8080/media/videos/raw/source.mp4'

    def test_without_a_request_or_the_setting_it_stays_relative(self, source):
        assert CloudflareR2Manager.generate_presigned_url(source) == '/media/videos/raw/source.mp4'

    def test_without_a_request_the_setting_is_the_origin(self, source, settings):
        settings.MEDIA_PUBLIC_BASE_URL = 'http://localhost:8000/'
        assert CloudflareR2Manager.generate_presigned_url(source) == 'http://localhost:8000/media/videos/raw/source.mp4'

    def test_the_request_wins_over_the_setting(self, source, settings):
        settings.MEDIA_PUBLIC_BASE_URL = 'http://otro:9000'
        request = RequestFactory().get('/', HTTP_HOST=PROXIED_HOST)
        assert CloudflareR2Manager.generate_presigned_url(source, request=request).startswith('http://localhost:8080/')

    def test_an_absolute_url_is_left_alone(self, settings):
        settings.MEDIA_PUBLIC_BASE_URL = 'http://localhost:8000'
        request = RequestFactory().get('/', HTTP_HOST=PROXIED_HOST)
        assert absolute_media_url('https://cdn.example.com/a.mp4', request) == 'https://cdn.example.com/a.mp4'
        assert absolute_media_url('https://cdn.example.com/a.mp4') == 'https://cdn.example.com/a.mp4'

    def test_r2_urls_are_unchanged(self, monkeypatch, settings):
        settings.USE_R2 = True
        settings.CLOUDFLARE_R2_BUCKET_NAME = 'test-bucket'
        settings.MEDIA_PUBLIC_BASE_URL = 'http://localhost:8000'
        client = MagicMock()
        client.generate_presigned_url.return_value = 'https://r2.example.com/videos/a.mp4?X-Amz-Signature=abc'
        monkeypatch.setattr(storage_service, '_get_r2_client', lambda: client)
        request = RequestFactory().get('/', HTTP_HOST=PROXIED_HOST)

        url = CloudflareR2Manager.generate_presigned_url('videos/a.mp4', expiration_seconds=120, request=request)

        assert url == 'https://r2.example.com/videos/a.mp4?X-Amz-Signature=abc'
        client.generate_presigned_url.assert_called_once_with(
            'get_object', Params={'Bucket': 'test-bucket', 'Key': 'videos/a.mp4'}, ExpiresIn=120,
        )


class TestProviderUrl:
    """What upload_to_social_network sends the provider: a URL it can download, never a relative path."""

    def test_local_files_take_the_web_origin_where_the_demo_serves_media(self, source, settings):
        settings.FRONTEND_URL = 'http://localhost:8195/'
        assert provider_media_url(source) == 'http://localhost:8195/media/videos/raw/source.mp4'

    def test_the_setting_of_the_media_origin_wins(self, source, settings):
        settings.FRONTEND_URL = 'http://localhost:3000'
        settings.MEDIA_PUBLIC_BASE_URL = 'http://localhost:8000'
        assert provider_media_url(source) == 'http://localhost:8000/media/videos/raw/source.mp4'

    def test_r2_is_the_presigned_url_as_always(self, monkeypatch, settings):
        settings.USE_R2 = True
        settings.CLOUDFLARE_R2_BUCKET_NAME = 'test-bucket'
        settings.FRONTEND_URL = 'http://localhost:8195'
        client = MagicMock()
        client.generate_presigned_url.return_value = 'https://r2.example.com/videos/a.mp4?X-Amz-Signature=abc'
        monkeypatch.setattr(storage_service, '_get_r2_client', lambda: client)

        assert provider_media_url('videos/a.mp4', expiration_seconds=3600) == client.generate_presigned_url.return_value
        client.generate_presigned_url.assert_called_once_with(
            'get_object', Params={'Bucket': 'test-bucket', 'Key': 'videos/a.mp4'}, ExpiresIn=3600,
        )

    def test_the_publisher_sends_the_absolute_url(self, project, source, settings):
        from apps.integrations.tests.helpers import PUBLISH, PUBLISHED, give_social_profile
        from apps.videos.models import ScheduledPost
        from apps.videos.tasks import upload_to_social_network

        settings.FRONTEND_URL = 'http://localhost:8195'
        give_social_profile(project.workspace)
        clip = VideoClip.objects.create(
            project=project, title='Clip', start_time=0, end_time=10, status=VideoClip.Status.COMPLETED, s3_object_key=source,
        )
        post = ScheduledPost.objects.create(
            video_clip=clip, platform=ScheduledPost.Platform.INSTAGRAM_REELS, status=ScheduledPost.Status.QUEUED,
            publish_at=timezone.now(), caption='Hola',
        )
        with patch(PUBLISH, return_value=PUBLISHED) as publish:
            upload_to_social_network.apply(args=[str(post.pk)]).get()

        assert publish.call_args.args[1].media_url == 'http://localhost:8195/media/videos/raw/source.mp4'


class TestApi:
    def test_project_detail_and_list(self, project):
        client = client_of(project)
        expected = 'http://testserver/media/videos/raw/source.mp4'
        assert client.get(f'/api/v1/projects/{project.pk}/').data['proxy_url'] == expected
        assert client.get('/api/v1/projects/').data['results'][0]['proxy_url'] == expected

    def test_paper_edit_data(self, project):
        response = client_of(project).get(f'/api/v1/projects/{project.pk}/paper-edit-data/')
        assert response.data['proxy_url'] == 'http://testserver/media/videos/raw/source.mp4'

    def test_behind_the_proxy_the_urls_use_its_origin(self, project):
        client = client_of(project)
        expected = 'http://localhost:8080/media/videos/raw/source.mp4'
        detail = client.get(f'/api/v1/projects/{project.pk}/', HTTP_HOST=PROXIED_HOST)
        paper = client.get(f'/api/v1/projects/{project.pk}/paper-edit-data/', HTTP_HOST=PROXIED_HOST)
        assert detail.data['proxy_url'] == expected
        assert paper.data['proxy_url'] == expected

    def test_a_project_without_proxy_has_none(self, project):
        VideoProject.objects.filter(pk=project.pk).update(proxy_r2_key=None)
        assert client_of(project).get(f'/api/v1/projects/{project.pk}/').data['proxy_url'] is None

    def test_clip_preview(self, project, settings):
        clip = VideoClip.objects.create(project=project, title='Clip', start_time=0, end_time=10)
        key = os.path.join(str(settings.MEDIA_ROOT), 'previews', 'p.mp4')
        ClipPreview.objects.create(
            clip=clip, status=ClipPreview.Status.READY, storage_key=key,
            ready_at=timezone.now(), expires_at=timezone.now() + timezone.timedelta(hours=1),
        )
        response = client_of(project).get(f'/api/v1/clips/{clip.pk}/preview/', HTTP_HOST=PROXIED_HOST)
        assert response.status_code == 200
        assert response.data['url'] == 'http://localhost:8080/media/previews/p.mp4'

    def test_preview_url_without_a_request_stays_relative(self, project, settings):
        clip = VideoClip.objects.create(project=project, title='Clip', start_time=0, end_time=10)
        preview = ClipPreview.objects.create(
            clip=clip, status=ClipPreview.Status.READY,
            storage_key=os.path.join(str(settings.MEDIA_ROOT), 'previews', 'p.mp4'),
            ready_at=timezone.now(), expires_at=timezone.now() + timezone.timedelta(hours=1),
        )
        assert preview_url(preview) == '/media/previews/p.mp4'
