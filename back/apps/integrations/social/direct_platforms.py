"""TikTok and Instagram direct publishing transports."""
import time

from .base import PublishResult
from .direct import checked_upload_url, response_json, safe_id
from .errors import (
    ACCOUNT_EXPIRED, ACCOUNT_NOT_CONNECTED, OutcomeUnknownError, PermanentError, RetryableError, SocialProviderError,
)

TIKTOK_API = 'https://open.tiktokapis.com/v2/post/publish'
INSTAGRAM_API = 'https://graph.facebook.com/v27.0'
TIKTOK_CHUNK = 8 * 1024 * 1024


class _UploadBody:
    """Stream an MP4 with a known Content-Length and a time limit."""

    def __init__(self, source, size, deadline):
        self.source = source
        self.size = size
        self.deadline = deadline

    def __len__(self):
        return self.size

    def __iter__(self):
        while chunk := self.source.read(1024 * 1024):
            if time.monotonic() >= self.deadline:
                raise RetryableError(detail='Instagram media upload deadline')
            yield chunk


class TikTokPublisher:
    def __init__(self, http, grant):
        self.http = http
        self.grant = grant

    def _call(self, path, **body):
        response = self.http.request(
            'POST', f'{TIKTOK_API}/{path}/',
            headers={'Authorization': f'Bearer {self.grant.access_token}'}, json=body,
        )
        value = response_json(response)
        code = (value.get('error') or {}).get('code')
        if code == 'rate_limit_exceeded':
            raise RetryableError(detail='TikTok rate limit', retry_after=1)
        if code == 'internal_error':
            raise RetryableError(detail='TikTok temporarily unavailable')
        if code != 'ok' or not isinstance(value.get('data'), dict):
            raise PermanentError(detail='TikTok refused the request')
        return value['data']

    def publish(self, media, title, text, options):
        if not self.grant.tiktok_consent or options is None:
            raise PermanentError(ACCOUNT_NOT_CONNECTED, 'TikTok direct post consent is missing')
        creator = self._call('creator_info/query')
        limit = creator.get('max_video_post_duration_sec')
        if not isinstance(limit, int) or media.duration > limit:
            raise PermanentError(detail='TikTok creator does not allow this duration')
        if options.privacy_level not in (creator.get('privacy_level_options') or []):
            raise PermanentError(detail='TikTok creator does not allow this privacy level')
        for name, allowed in (
            ('comment', options.allow_comment), ('duet', options.allow_duet), ('stitch', options.allow_stitch),
        ):
            if allowed and creator.get(f'{name}_disabled'):
                raise PermanentError(detail='TikTok creator disabled a selected interaction')
        if len(text.encode('utf-16-le')) // 2 > 2200:
            raise PermanentError(detail='TikTok caption exceeds 2200 UTF-16 units')
        chunk = min(TIKTOK_CHUNK, media.size)
        chunks = media.size // chunk  # The final chunk includes any remainder.
        start = self._call(
            'video/init',
            post_info={
                'title': text, 'privacy_level': options.privacy_level,
                'disable_comment': not options.allow_comment, 'disable_duet': not options.allow_duet,
                'disable_stitch': not options.allow_stitch, 'brand_content_toggle': options.branded_content,
                'brand_organic_toggle': options.brand_organic,
            },
            source_info={
                'source': 'FILE_UPLOAD', 'video_size': media.size, 'chunk_size': chunk, 'total_chunk_count': chunks,
            },
        )
        upload_url = checked_upload_url(start.get('upload_url'), {'open-upload.tiktokapis.com'})
        publish_id = safe_id(start.get('publish_id'))
        if not publish_id:
            raise PermanentError(detail='missing TikTok publish id')
        with media.path.open('rb') as source:
            for index in range(chunks):
                offset = index * chunk
                last = index == chunks - 1
                data = source.read(media.size - offset if last else chunk)
                try:
                    self.http.request(
                        'PUT', upload_url, accepted=(201,) if last else (206,), uncertain=last, data=data,
                        headers={'Content-Type': 'video/mp4', 'Content-Range': f'bytes {offset}-{offset+len(data)-1}/{media.size}'},
                    )
                except OutcomeUnknownError:
                    # The last chunk may already have started publication. Use the existing publish id.
                    break

        def check():
            current = self._call('status/fetch', publish_id=publish_id)
            return current if current.get('status') in ('PUBLISH_COMPLETE', 'FAILED') else None

        try:
            status = self.http.poll(check, interval=5)
        except PermanentError:
            raise OutcomeUnknownError(detail='TikTok publication status unavailable') from None
        if status['status'] == 'FAILED':
            if status.get('fail_reason') in ('video_pull_failed', 'internal_error'):
                raise RetryableError(detail='TikTok reported an unsuccessful post')
            raise PermanentError(detail='TikTok reported an unsuccessful post')
        # TikTok can confirm a private post without providing a public video id.
        post_ids = status.get('publicaly_available_post_id') or []
        public_id = safe_id(str(post_ids[0])) if isinstance(post_ids, list) and post_ids else None
        return PublishResult(public_id or publish_id, None)


class InstagramPublisher:
    def __init__(self, http, grant):
        self.http = http
        self.grant = grant

    def _graph(self, method, path, *, uncertain=False, **kwargs):
        response = self.http.request(
            method, f'{INSTAGRAM_API}/{path}', uncertain=uncertain,
            headers={'Authorization': f'Bearer {self.grant.access_token}'}, **kwargs,
        )
        return response_json(response, uncertain=uncertain)

    def publish(self, media, title, text, options):
        account = safe_id(self.grant.account_id)
        if not account or not account.isdecimal():
            raise PermanentError(ACCOUNT_NOT_CONNECTED, 'missing Instagram professional account id')
        container = self._graph('POST', f'{account}/media', data={
            'media_type': 'REELS', 'upload_type': 'resumable', 'caption': text,
        })
        container_id = safe_id(container.get('id'))
        if not container_id or not container_id.isdecimal():
            raise PermanentError(detail='missing Instagram media container id')
        upload_url = checked_upload_url(container.get('uri'), {'rupload.facebook.com'})
        with media.path.open('rb') as source:
            upload = self.http.request('POST', upload_url, data=_UploadBody(source, media.size, self.http.deadline), headers={
                'Authorization': f'OAuth {self.grant.access_token}', 'offset': '0', 'file_size': str(media.size),
                'Content-Type': 'application/octet-stream',
            })
        if response_json(upload).get('success') is not True:
            raise PermanentError(detail='Instagram did not accept the upload')

        def check():
            state = self._graph('GET', container_id, params={'fields': 'status_code'}).get('status_code')
            return state if state in ('ERROR', 'EXPIRED', 'FINISHED', 'PUBLISHED') else None

        state = self.http.poll(check, interval=60, uncertain=False)
        if state == 'PUBLISHED':
            raise OutcomeUnknownError(detail='Instagram container already published')
        if state != 'FINISHED':
            raise PermanentError(detail='Instagram rejected the media container')
        try:
            result = self._graph('POST', f'{account}/media_publish', uncertain=True, data={'creation_id': container_id})
        except PermanentError as exc:
            # Some refusals may mean the container is already published. A
            # known auth failure is a rejection before publication.
            if exc.code == ACCOUNT_EXPIRED:
                raise
            try:
                state = check()
            except SocialProviderError:
                state = None
            if state == 'PUBLISHED':
                return PublishResult(None, None)
            if state in ('ERROR', 'EXPIRED', 'FINISHED'):
                raise
            raise OutcomeUnknownError(detail='Instagram publication status unavailable') from None
        except OutcomeUnknownError:
            try:
                if check() == 'PUBLISHED':
                    return PublishResult(None, None)
            except SocialProviderError:
                pass
            raise
        post_id = safe_id(result.get('id'))
        if not post_id:
            raise OutcomeUnknownError(detail='missing Instagram post id')
        # An id alone is not enough to construct a permalink.
        try:
            link = self._graph('GET', post_id, params={'fields': 'permalink'}).get('permalink')
        except SocialProviderError:
            link = None
        return PublishResult(post_id, link if isinstance(link, str) and link.startswith('https://www.instagram.com/') else None)
