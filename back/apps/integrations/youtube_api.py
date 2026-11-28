"""YouTube Data API resumable video upload and processing status."""
import re

from .social.base import PublishResult
from .social.direct import checked_upload_url, response_json, safe_id
from .social.errors import OutcomeUnknownError, PermanentError, RetryableError, SocialProviderError

API = 'https://www.googleapis.com'
CHUNK_BYTES = 8 * 1024 * 1024  # A multiple of YouTube's 256 KiB chunk boundary.


class YouTubePublisher:
    def __init__(self, http, grant):
        self.http = http
        self.auth = {'Authorization': f'Bearer {grant.access_token}'}

    def _upload(self, media, url):
        offset = 0
        checked_offset = None
        uncertain_final = False
        with media.path.open('rb') as source:
            while offset < media.size:
                source.seek(offset)
                data = source.read(CHUNK_BYTES)
                if not data:
                    raise PermanentError(detail='incomplete local video')
                last = offset + len(data) == media.size
                try:
                    response = self.http.request(
                        'PUT', url, accepted=(200, 201, 308), uncertain=last, data=data,
                        headers={**self.auth, 'Content-Type': 'video/mp4',
                                 'Content-Range': f'bytes {offset}-{offset+len(data)-1}/{media.size}'},
                    )
                except PermanentError:
                    if uncertain_final:
                        raise OutcomeUnknownError(detail='YouTube final upload could already have completed') from None
                    raise
                except (RetryableError, OutcomeUnknownError) as exc:
                    uncertain_final = uncertain_final or (last and isinstance(exc, OutcomeUnknownError))
                    try:
                        response = self.http.request(
                            'PUT', url, accepted=(200, 201, 308), data=b'',
                            headers={**self.auth, 'Content-Range': f'bytes */{media.size}'},
                        )
                    except SocialProviderError:
                        if last:
                            raise OutcomeUnknownError(detail='YouTube upload outcome unavailable') from None
                        raise
                if response.status_code != 308:
                    video_id = safe_id(response_json(response, uncertain=True).get('id'))
                    if video_id is None:
                        raise OutcomeUnknownError(detail='missing YouTube video id')
                    return video_id
                uploaded = re.fullmatch(r'bytes=0-(\d{1,12})', response.headers.get('Range', ''))
                next_offset = int(uploaded[1]) + 1 if uploaded else 0
                if next_offset >= media.size:
                    raise OutcomeUnknownError(detail='invalid YouTube upload offset')
                if next_offset <= offset:
                    if last:
                        if checked_offset == offset:
                            raise OutcomeUnknownError(detail='YouTube final upload made no progress')
                        # A transient failure on the final chunk is retried on
                        # the same session only, never with a new video insert.
                        checked_offset = offset
                        continue
                    raise RetryableError(detail='YouTube upload made no progress')
                offset = next_offset
        raise OutcomeUnknownError(detail='missing YouTube upload result')

    def publish(self, media, title, text, options):
        if len(text.encode('utf-8')) > 5000:
            raise PermanentError(detail='YouTube description exceeds 5000 UTF-8 bytes')
        response = self.http.request(
            'POST', f'{API}/upload/youtube/v3/videos', accepted=(200, 201),
            params={'uploadType': 'resumable', 'part': 'snippet,status'},
            headers={**self.auth, 'X-Upload-Content-Type': 'video/mp4', 'X-Upload-Content-Length': str(media.size)},
            json={
                'snippet': {'title': title.translate(str.maketrans('', '', '<>'))[:100].strip() or 'OneCreator',
                            'description': text, 'categoryId': '22'},
                'status': {'privacyStatus': 'public'},
            },
        )
        url = checked_upload_url(response.headers.get('Location'), {'www.googleapis.com'})
        video_id = self._upload(media, url)

        def check():
            response = self.http.request(
                'GET', f'{API}/youtube/v3/videos', headers=self.auth,
                params={'part': 'processingDetails,status', 'id': video_id},
            )
            items = response_json(response).get('items') or []
            if items:
                status = items[0].get('status') or {}
                processing = (items[0].get('processingDetails') or {}).get('processingStatus')
                if status.get('uploadStatus') in ('failed', 'rejected') or processing == 'failed':
                    return 'failed'
                if status.get('uploadStatus') == 'processed' or processing == 'succeeded':
                    if status.get('privacyStatus', 'public') != 'public':
                        raise OutcomeUnknownError(detail='YouTube accepted the upload with unexpected visibility')
                    return 'ready'
            return None

        try:
            state = self.http.poll(check, interval=5)
        except PermanentError:
            raise OutcomeUnknownError(detail='YouTube processing status unavailable') from None
        if state == 'failed':
            raise PermanentError(detail='YouTube rejected the processed video')
        return PublishResult(video_id, f'https://www.youtube.com/shorts/{video_id}')
