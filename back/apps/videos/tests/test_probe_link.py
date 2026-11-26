"""
probe-link (C1 › Pegar link) and the link rules it shares with the creation
and the download (services/link_validation.py):

- only the allowed sites, never an IP, localhost, another scheme, a user in
  the URL or a host that resolves to a private address; nothing is fetched
  for a refused link (the network guard of conftest.py fails the test if it is);
- yt-dlp runs in a child process that is killed at the time limit;
- the provider's text never reaches the response or the logs;
- the plan's duration limit answers 403 ``duration``; Lector cannot probe.

yt-dlp is never called for real: the child (or yt-dlp inside it) is mocked.
"""
import json
import socket
import subprocess
import sys
import time
from unittest.mock import MagicMock, patch

from django.core.cache import cache
from django.test import SimpleTestCase, override_settings
from django.urls import reverse
from rest_framework.test import APITestCase

from apps.payments.models import SubscriptionPlan
from apps.videos.models import VideoProject
from apps.videos.services import link_probe, link_probe_worker
from apps.videos.services.link_validation import LinkRejected, validate_video_link
from apps.videos.tests.test_active_workspace import EDITOR, VIEWER, fund, header, home_of, join, make_user, team

URL = reverse('project-probe-link')
SECRET = 'SECRET_LOG_X'
PUBLIC = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('142.250.0.1', 443))]
PRIVATE = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('10.0.0.7', 443))]
VIDEO = {
    'kind': 'video', 'title': 'Charla completa', 'duration': 2700.5, 'is_live': False,
    'thumbnail': 'https://i.ytimg.com/vi/abc/hq.jpg', 'webpage_url': 'https://www.youtube.com/watch?v=abc',
}


def completed(answer, code=0):
    return subprocess.CompletedProcess(args=[], returncode=code, stdout=(json.dumps(answer) + '\n').encode(), stderr=None)


def resolving(addresses=PUBLIC):
    return patch('apps.videos.services.link_validation.socket.getaddrinfo', return_value=addresses)


class LinkValidationTests(SimpleTestCase):
    def test_allowed_sites_are_normalized(self):
        cases = {
            'https://www.youtube.com/watch?v=abc#t=1': ('https://www.youtube.com/watch?v=abc', 'YouTube'),
            'http://YOUTU.BE/abc': ('https://youtu.be/abc', 'YouTube'),
            'https://m.youtube.com/shorts/abc': ('https://m.youtube.com/shorts/abc', 'YouTube'),
            'https://vimeo.com/123': ('https://vimeo.com/123', 'Vimeo'),
            'https://www.twitch.tv/videos/1': ('https://www.twitch.tv/videos/1', 'Twitch'),
            'https://drive.google.com/file/d/x/view': ('https://drive.google.com/file/d/x/view', 'Google Drive'),
            '  https://youtube.com.:443/watch?v=abc ': ('https://youtube.com/watch?v=abc', 'YouTube'),
        }
        for raw, (url, provider) in cases.items():
            with self.subTest(raw):
                link = validate_video_link(raw)
                self.assertEqual((link.url, link.provider), (url, provider))

    def test_ssrf_and_other_sites_are_refused(self):
        refused = {
            'file:///etc/passwd': 'invalid',
            'ftp://youtube.com/x': 'invalid',
            'gopher://localhost:70/': 'invalid',
            'javascript:alert(1)': 'invalid',
            'youtube.com/watch?v=abc': 'invalid',
            'https://': 'invalid',
            'https://www.you tube.com/': 'invalid',
            'http://localhost:8000/admin/': 'unsupported',
            'http://127.0.0.1/': 'unsupported',
            'http://10.0.0.5/video.mp4': 'unsupported',
            'http://169.254.169.254/latest/meta-data/': 'unsupported',
            'http://[::1]/': 'unsupported',
            'http://2130706433/': 'unsupported',
            'https://youtube.com@127.0.0.1/': 'unsupported',
            'https://user:pw@youtube.com/watch?v=a': 'unsupported',
            'https://youtube.com:8080/watch?v=a': 'unsupported',
            'https://youtube.com.evil.test/watch?v=a': 'unsupported',
            'https://evilyoutube.com/watch?v=a': 'unsupported',
            'https://sites.google.com/x': 'unsupported',
            'https://example.com/video.mp4': 'unsupported',
            'https://youtube.com\\@127.0.0.1/': 'unsupported',
            'https://' + 'a' * 600 + '.youtube.com/': 'invalid',
        }
        for raw, code in refused.items():
            with self.subTest(raw):
                with self.assertRaises(LinkRejected) as caught:
                    validate_video_link(raw)
                self.assertEqual(caught.exception.code, code)

    def test_an_allowed_host_that_resolves_to_a_private_address_is_refused(self):
        for addresses in (PRIVATE, [(socket.AF_INET6, socket.SOCK_STREAM, 6, '', ('::ffff:127.0.0.1', 443, 0, 0))]):
            with self.subTest(addresses), resolving(addresses), self.assertRaises(LinkRejected):
                validate_video_link('https://www.youtube.com/watch?v=abc', resolve=True)
        with resolving(PUBLIC):
            self.assertEqual(validate_video_link('https://youtu.be/a', resolve=True).host, 'youtu.be')


class WorkerTests(SimpleTestCase):
    """The child's own logic, run in this process with yt-dlp mocked."""

    def test_errors_are_reduced_to_a_category_without_the_providers_text(self):
        from yt_dlp.utils import DownloadError

        cases = {
            f'ERROR: [youtube] abc: Private video. Sign in {SECRET}': 'unavailable',
            f'ERROR: Video unavailable {SECRET}': 'unavailable',
            f'ERROR: Unsupported URL: {SECRET}': 'unsupported',
            f'ERROR: No suitable extractor found for URL {SECRET}': 'unsupported',
            f'ERROR: HTTP Error 503 {SECRET}': 'provider_error',
        }
        for message, category in cases.items():
            with self.subTest(category):
                ydl = MagicMock()
                ydl.__enter__.return_value.extract_info.side_effect = DownloadError(message)
                with patch('yt_dlp.YoutubeDL', return_value=ydl):
                    answer = link_probe_worker.probe('https://youtu.be/abc')
                self.assertEqual(answer, {'error': category, 'class': 'DownloadError'})

    def test_it_reads_without_downloading_and_never_with_the_generic_extractor(self):
        ydl = MagicMock()
        ydl.__enter__.return_value.extract_info.return_value = {'title': 'T', 'duration': 12, 'webpage_url': 'u'}
        with patch('yt_dlp.YoutubeDL', return_value=ydl) as factory:
            answer = link_probe_worker.probe('https://youtu.be/abc')

        options = factory.call_args.args[0]
        self.assertEqual(options['allowed_extractors'], ['default', '-generic'])
        self.assertTrue(options['skip_download'] and options['noplaylist'])
        self.assertEqual(options['retries'], 0)
        self.assertEqual(ydl.__enter__.return_value.extract_info.call_args.kwargs, {'download': False, 'process': False})
        self.assertEqual(answer['ok']['duration'], 12)

    def test_a_redirect_is_followed_only_through_a_known_extractor(self):
        inner = {'title': 'Video', 'duration': 30}
        ydl = MagicMock()
        ydl.extract_info.return_value = inner
        followed = link_probe_worker._follow(ydl, {'_type': 'url', 'url': 'https://www.youtube.com/watch?v=a', 'ie_key': 'Youtube'})
        self.assertEqual(followed, inner)
        self.assertEqual(ydl.extract_info.call_args.kwargs['ie_key'], 'Youtube')

        ydl.reset_mock()
        stopped = {'_type': 'url', 'url': 'http://10.0.0.1/', 'ie_key': 'Generic'}
        self.assertEqual(link_probe_worker._follow(ydl, stopped), stopped)
        ydl.extract_info.assert_not_called()

    def test_the_child_process_is_killed_at_the_time_limit(self):
        """A real child that hangs: the parent stops waiting and kills it."""
        import tempfile

        with tempfile.NamedTemporaryFile('w', suffix='.py', delete=False) as script:
            script.write('import time\ntime.sleep(30)\n')
        started = time.monotonic()
        with patch.object(link_probe, 'WORKER_PATH', script.name), override_settings(LINK_PROBE_TIMEOUT_SECONDS=1):
            with self.assertRaises(link_probe.LinkProbeError) as caught:
                link_probe.run_worker('https://youtu.be/abc')
        self.assertEqual((caught.exception.code, caught.exception.status), ('timeout', 504))
        self.assertLess(time.monotonic() - started, 10)

    def test_the_worker_script_answers_one_json_line(self):
        """The script itself, as the parent runs it (isolated mode), on a refused link."""
        result = subprocess.run(
            [sys.executable, '-I', link_probe.WORKER_PATH], capture_output=True, timeout=60, check=False,
        )
        self.assertEqual(json.loads(result.stdout), {'error': 'provider_error', 'class': 'UsageError'})


class ProbeLinkTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.alice = make_user('alice')
        self.home = home_of(self.alice)
        self.client.force_authenticate(self.alice)

    def probe(self, video_url='https://www.youtube.com/watch?v=abc&list=PL1', answer=None, **extra):
        with resolving(), patch.object(link_probe.subprocess, 'run', return_value=completed(answer or {'ok': VIDEO})) as run:
            response = self.client.post(URL, {'video_url': video_url}, format='json', **extra)
        self.run_calls = run.call_args_list
        return response

    def test_a_link_is_recognised(self):
        response = self.probe()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {
            'video_url': 'https://www.youtube.com/watch?v=abc', 'provider': 'YouTube', 'title': 'Charla completa',
            'duration_seconds': 2700.5, 'thumbnail_url': 'https://i.ytimg.com/vi/abc/hq.jpg',
        })
        [call] = self.run_calls
        command = call.args[0]
        self.assertEqual(command[:3], [sys.executable, '-I', link_probe.WORKER_PATH])
        self.assertEqual(command[3], 'https://www.youtube.com/watch?v=abc&list=PL1')
        self.assertEqual(call.kwargs['timeout'], 25)
        self.assertEqual(call.kwargs['stderr'], subprocess.DEVNULL)

    def test_a_second_probe_of_the_same_link_is_cached(self):
        self.probe()
        response = self.probe()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.run_calls, [])

    def test_a_canonical_url_off_the_site_of_the_link_is_ignored(self):
        answer = {'ok': {**VIDEO, 'webpage_url': 'http://10.0.0.1/x', 'thumbnail': 'http://insecure/x.jpg'}}
        body = self.probe('https://youtu.be/abc', answer=answer).json()
        self.assertEqual(body['video_url'], 'https://youtu.be/abc')
        self.assertIsNone(body['thumbnail_url'])
        cache.clear()
        body = self.probe('https://vimeo.com/9').json()  # VIDEO's canonical page is on YouTube
        self.assertEqual((body['video_url'], body['provider']), ('https://vimeo.com/9', 'Vimeo'))

    def test_bad_links_and_ssrf_attempts_are_refused_without_any_request(self):
        for raw in ('no es un link', 'file:///etc/passwd', 'http://localhost:8000/', 'http://127.0.0.1/',
                    'http://192.168.0.10/v.mp4', 'http://169.254.169.254/latest/', 'https://example.com/v.mp4'):
            with self.subTest(raw), patch.object(link_probe.subprocess, 'run') as run, \
                    patch('apps.videos.services.link_validation.socket.getaddrinfo') as dns:
                response = self.client.post(URL, {'video_url': raw}, format='json')
                self.assertEqual(response.status_code, 400)
                self.assertIn(response.json()['code'], ('invalid', 'unsupported'))
                run.assert_not_called()
                dns.assert_not_called()

    def test_an_allowed_host_resolving_to_a_private_address_is_refused(self):
        with resolving(PRIVATE), patch.object(link_probe.subprocess, 'run') as run:
            response = self.client.post(URL, {'video_url': 'https://www.youtube.com/watch?v=a'}, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['code'], 'unsupported')
        run.assert_not_called()

    def test_a_missing_or_too_long_url_is_a_400(self):
        for body in ({}, {'video_url': ''}, {'video_url': 'https://youtu.be/' + 'a' * 500}):
            with self.subTest(body):
                self.assertEqual(self.client.post(URL, body, format='json').status_code, 400)

    def test_the_provider_answers_map_to_the_contract_codes(self):
        cases = [
            ({'error': 'unavailable', 'class': 'DownloadError'}, 400, 'unavailable'),
            ({'error': 'unsupported', 'class': 'DownloadError'}, 400, 'unsupported'),
            ({'error': 'provider_error', 'class': 'DownloadError'}, 502, 'provider_error'),
            ({'error': 'tool_missing', 'class': 'ImportError'}, 503, 'unavailable_tool'),
            ({'ok': {**VIDEO, 'is_live': True}}, 400, 'unsupported'),
            ({'ok': {**VIDEO, 'duration': None}}, 400, 'unsupported'),
            ({'ok': {**VIDEO, 'kind': 'playlist'}}, 400, 'unsupported'),
            ('not json', 502, 'provider_error'),
        ]
        for answer, status_code, code in cases:
            with self.subTest(answer):
                cache.clear()
                stdout = answer if isinstance(answer, str) else json.dumps(answer)
                with resolving(), patch.object(link_probe.subprocess, 'run', return_value=subprocess.CompletedProcess(
                        [], 1, stdout=stdout.encode(), stderr=None)):
                    response = self.client.post(URL, {'video_url': 'https://vimeo.com/1'}, format='json')
                self.assertEqual(response.status_code, status_code)
                self.assertEqual(response.json()['code'], code)

    def test_a_timeout_is_a_504_and_the_logs_carry_no_link(self):
        secret_url = f'https://www.youtube.com/watch?v={SECRET}'
        with resolving(), patch.object(link_probe.subprocess, 'run', side_effect=subprocess.TimeoutExpired('x', 25)), \
                self.assertLogs('apps.videos', 'INFO') as logs:
            response = self.client.post(URL, {'video_url': secret_url}, format='json')

        self.assertEqual(response.status_code, 504)
        self.assertEqual(response.json()['code'], 'timeout')
        self.assertNotIn(SECRET, '\n'.join(logs.output))
        self.assertNotIn(SECRET, json.dumps(response.json()))

    def test_a_host_that_does_not_resolve_is_a_502(self):
        with patch('apps.videos.services.link_validation.socket.getaddrinfo', side_effect=socket.gaierror('x')):
            response = self.client.post(URL, {'video_url': 'https://vimeo.com/1'}, format='json')
        self.assertEqual(response.status_code, 502)

    def test_longer_than_the_plan_is_a_403_with_the_limit(self):
        plan = SubscriptionPlan.objects.create(name='Corto', max_video_duration_seconds=600)
        self.home.subscription_plan = plan
        self.home.save()

        response = self.probe()

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()['code'], 'duration')
        self.assertEqual(response.json()['limit'], 600)
        self.assertEqual(response.json()['error'], 'El video dura 45 min y tu plan permite hasta 10 min.')

    def test_roles_and_workspaces(self):
        boss = make_user('boss')
        shared = team(boss)
        fund(shared)
        self.assertEqual(self.probe(**header(shared)).status_code, 404)  # not a member
        join(shared, self.alice, VIEWER)
        response = self.probe(**header(shared))
        self.assertEqual(response.status_code, 403)
        self.assertIn('Lector', response.json()['detail'])
        self.assertEqual(self.run_calls, [])

        other = make_user('carla')
        join(shared, other, EDITOR)
        self.client.force_authenticate(other)
        self.assertEqual(self.probe(**header(shared)).status_code, 200)

    def test_too_many_probes_in_a_minute_are_a_429(self):
        for _ in range(20):
            self.assertEqual(self.probe('https://vimeo.com/1').status_code, 200)
        response = self.probe('https://vimeo.com/1')
        self.assertEqual(response.status_code, 429)


class CreationAndDownloadUseTheSameRulesTests(APITestCase):
    def setUp(self):
        self.alice = make_user('alice')
        fund(home_of(self.alice))
        self.client.force_authenticate(self.alice)

    def test_a_project_cannot_be_created_from_an_internal_link(self):
        for raw in ('http://127.0.0.1:8000/admin/', 'http://169.254.169.254/latest/meta-data/', 'file:///etc/passwd',
                    'https://example.com/v.mp4'):
            with self.subTest(raw), patch('apps.videos.views.process_initial_ingestion.delay') as ingest:
                response = self.client.post(reverse('project-list'), {'title': 'X', 'video_url': raw}, format='json')
                self.assertEqual(response.status_code, 400)
                self.assertIn('video_url', response.json())
                ingest.assert_not_called()
        self.assertFalse(VideoProject.objects.exists())

    def test_an_allowed_link_is_created_normalized(self):
        with patch('apps.videos.views.process_initial_ingestion.delay'):
            response = self.client.post(
                reverse('project-list'), {'title': 'X', 'video_url': 'HTTP://WWW.YOUTUBE.COM/watch?v=abc#t=3'}, format='json',
            )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()['video_url'], 'https://www.youtube.com/watch?v=abc')

    def test_the_download_refuses_a_saved_internal_link_without_calling_yt_dlp(self):
        from apps.videos.tasks.shared import UnsupportedLinkError, _is_retryable_pipeline_error, download_from_youtube

        with patch('yt_dlp.YoutubeDL') as ydl:
            with self.assertRaises(UnsupportedLinkError) as caught:
                download_from_youtube('http://169.254.169.254/latest/meta-data/', '/tmp')
            with resolving(PRIVATE), self.assertRaises(UnsupportedLinkError):
                download_from_youtube('https://www.youtube.com/watch?v=a', '/tmp')
        ydl.assert_not_called()
        self.assertFalse(_is_retryable_pipeline_error(caught.exception))

    def test_the_download_of_an_allowed_link_never_uses_the_generic_extractor(self):
        from apps.videos.tasks.shared import download_from_youtube

        with resolving(), patch('yt_dlp.YoutubeDL') as ydl:
            download_from_youtube('https://youtu.be/abc', '/tmp')
        options = ydl.call_args.args[0]
        self.assertEqual(options['allowed_extractors'], ['default', '-generic'])
        ydl.return_value.__enter__.return_value.download.assert_called_once_with(['https://youtu.be/abc'])
