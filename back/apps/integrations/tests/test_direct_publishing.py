from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from apps.integrations.social.base import INSTAGRAM_REELS, TIKTOK, YOUTUBE_SHORTS, TikTokOptions
from apps.integrations.social.direct import DirectHTTP, PublishingGrant, checked_upload_url
from apps.integrations.social.direct_platforms import InstagramPublisher, TikTokPublisher
from apps.integrations.social.errors import CLIP_TOO_LONG, OutcomeUnknownError, PermanentError, RetryableError
from apps.integrations.youtube_api import YouTubePublisher
from apps.videos.services import media_validation, publishing


def reply(body=None, *, code=200, headers=None):
    return SimpleNamespace(status_code=code, headers=headers or {}, json=lambda: body or {})


@pytest.fixture
def media(tmp_path):
    path = tmp_path / 'clip.mp4'
    path.write_bytes(b'video bytes')
    return publishing.PublishMedia(path, path.stat().st_size, 30)


def http_replies(*responses):
    http = Mock()
    http.deadline = 1e10
    http.request.side_effect = responses
    http.poll.side_effect = lambda check, **kwargs: check()
    return http


def test_youtube_recovers_a_lost_final_response_using_the_original_upload(media):
    url = 'https://www.googleapis.com/upload/youtube/v3/videos?upload_id=one'
    http = http_replies(
        reply(headers={'Location': url}),
        OutcomeUnknownError(),
        reply({'id': 'video-123'}, code=201),
        reply({'items': [{'processingDetails': {'processingStatus': 'succeeded'}}]}),
    )
    result = YouTubePublisher(http, PublishingGrant('access')).publish(media, 'A <title>', '#caption', None)

    assert result.post_url == 'https://www.youtube.com/shorts/video-123'
    init, chunk, verify, poll = http.request.call_args_list
    assert init.kwargs['params']['uploadType'] == 'resumable'
    assert init.kwargs['json']['snippet']['title'] == 'A title'
    assert chunk.kwargs['data'] == media.path.read_bytes()
    assert verify.args == ('PUT', url)
    assert verify.kwargs['headers']['Content-Range'] == f'bytes */{media.size}'
    assert poll.kwargs['params']['id'] == 'video-123'


def test_youtube_does_not_retry_by_creating_a_second_upload_when_status_is_unknown(media):
    http = http_replies(
        reply(headers={'Location': 'https://www.googleapis.com/upload?upload_id=one'}),
        OutcomeUnknownError(),
        RetryableError(),
    )
    with pytest.raises(OutcomeUnknownError):
        YouTubePublisher(http, PublishingGrant('access')).publish(media, 'Clip', '', None)
    assert [call.args[0] for call in http.request.call_args_list] == ['POST', 'PUT', 'PUT']


def tiktok_reply(data):
    return reply({'data': data, 'error': {'code': 'ok'}})


def test_tiktok_checks_creator_options_uploads_and_waits_for_publish_completion(media):
    http = http_replies(
        tiktok_reply({'max_video_post_duration_sec': 60, 'privacy_level_options': ['SELF_ONLY']}),
        tiktok_reply({
            'publish_id': 'v_pub_file~id', 'upload_url': 'https://open-upload.tiktokapis.com/video/?upload_id=1',
        }),
        reply(code=201),
        tiktok_reply({'status': 'PUBLISH_COMPLETE'}),
    )
    grant = PublishingGrant('access', tiktok_consent=True)
    result = TikTokPublisher(http, grant).publish(media, 'Title', 'Texto #uno', TikTokOptions('SELF_ONLY'))

    assert result.external_id == 'v_pub_file~id'
    assert result.post_url is None
    creator, start, upload, check = http.request.call_args_list
    assert creator.args[1].endswith('/creator_info/query/')
    assert start.kwargs['json']['source_info']['total_chunk_count'] == 1
    assert start.kwargs['json']['post_info']['title'] == 'Texto #uno'
    assert 'Authorization' not in upload.kwargs['headers']
    assert check.kwargs['json'] == {'publish_id': 'v_pub_file~id'}


def test_tiktok_requires_post_specific_consent_before_contacting_the_api(media):
    http = http_replies()
    with pytest.raises(PermanentError):
        TikTokPublisher(http, PublishingGrant('access')).publish(media, '', '', TikTokOptions('SELF_ONLY'))
    http.request.assert_not_called()


def test_instagram_publishes_only_after_its_container_finishes(media):
    http = http_replies(
        reply({'id': '456', 'uri': 'https://rupload.facebook.com/ig-api-upload/v27.0/456'}),
        reply({'success': True}),
        reply({'status_code': 'FINISHED'}),
        reply({'id': '789'}),
        reply({'permalink': 'https://www.instagram.com/reel/test/'}),
    )
    result = InstagramPublisher(http, PublishingGrant('access', '123')).publish(media, 'Clip', 'Texto', None)

    create, upload, status, publish, permalink = http.request.call_args_list
    assert create.args[1].endswith('/123/media')
    assert create.kwargs['data']['upload_type'] == 'resumable'
    assert upload.kwargs['headers']['Authorization'] == 'OAuth access'
    assert status.kwargs['params'] == {'fields': 'status_code'}
    assert publish.kwargs['data'] == {'creation_id': '456'}
    assert permalink.args[1].endswith('/789')
    assert result.post_url == 'https://www.instagram.com/reel/test/'


@pytest.mark.parametrize('url', [
    'http://www.googleapis.com/upload',
    'https://www.googleapis.com@untrusted.example/upload',
    'https://www.googleapis.com:8443/upload',
])
def test_upload_rejects_untrusted_urls(url):
    with pytest.raises(PermanentError):
        checked_upload_url(url, {'www.googleapis.com'})


@pytest.mark.parametrize('platform,maximum', [(TIKTOK, 600), (INSTAGRAM_REELS, 90), (YOUTUBE_SHORTS, 60)])
def test_direct_media_duration_is_checked_on_the_actual_file(monkeypatch, media, platform, maximum):
    probe = {
        'format': {'duration': str(maximum)},
        'streams': [{'codec_type': 'video', 'codec_name': 'h264', 'width': 1080, 'height': 1920}],
    }
    monkeypatch.setattr(media_validation, '_probe', lambda *args: probe)
    assert publishing._inspect(media.path, platform) == (maximum, True)
    probe['format']['duration'] = str(maximum + 0.01)
    with pytest.raises(PermanentError) as exc:
        publishing._inspect(media.path, platform)
    assert exc.value.code == CLIP_TOO_LONG


def test_media_preparation_uses_a_temporary_variant_without_rewriting_original(monkeypatch, media, tmp_path):
    from django.test import override_settings

    monkeypatch.setattr(publishing, '_inspect', lambda path, platform: (30, path != media.path))

    def encode(source, target, duration, limit, deadline):
        assert source == media.path
        assert limit == publishing.MEDIA_LIMITS[YOUTUBE_SHORTS][2]
        target.write_bytes(b'prepared video')

    monkeypatch.setattr(publishing, '_encode', encode)
    with override_settings(USE_R2=False, MEDIA_ROOT=tmp_path):
        with publishing.prepare_media(str(media.path), YOUTUBE_SHORTS, 1e10) as prepared:
            assert prepared.path.read_bytes() == b'prepared video'
        assert not prepared.path.exists()
    assert media.path.read_bytes() == b'video bytes'


def test_direct_http_classifies_rate_limits_and_uncertain_sends(monkeypatch):
    monkeypatch.setattr('apps.integrations.social.direct.time.monotonic', lambda: 100)
    session = Mock()
    session.request.side_effect = [reply(code=429, headers={'Retry-After': '12'}), reply(code=503), reply(code=408)]
    http = DirectHTTP(session, deadline=120)
    with pytest.raises(RetryableError) as limited:
        http.request('GET', 'https://example.com/status')
    assert limited.value.retry_after == 12
    with pytest.raises(OutcomeUnknownError):
        http.request('POST', 'https://example.com/publish', uncertain=True)
    with pytest.raises(OutcomeUnknownError):
        http.request('POST', 'https://example.com/publish', uncertain=True)
    assert session.request.call_args.kwargs['allow_redirects'] is False
