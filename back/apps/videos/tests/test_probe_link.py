"""
probe-link (C1 › Pegar link) and the link rules it shares with the creation
and the download (services/link_validation.py, services/ytdlp_safe.py):

- only the exact allowed hosts, never an IP, localhost, another scheme, a user
  in the URL, an odd host label or a host that resolves to a private address;
  nothing is fetched for a refused link (the network guard of conftest.py
  fails the test if it is);
- a link names one video: lists, channels and live streams are refused;
- yt-dlp runs in a child process, with a minimal environment and its own
  process group, killed whole at the time limit;
- the provider's text never reaches the response or the logs;
- the plan's duration limit answers 403 ``duration``; Lector cannot probe.

The view tests mock the child (``run_worker``); the ``Real*`` tests run the
installed yt-dlp against local HTTP servers, with no internet.
"""
import http.server
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
from unittest.mock import MagicMock, patch
from urllib.parse import quote

import pytest
from django.core.cache import cache
from django.test import SimpleTestCase, override_settings
from django.urls import reverse
from rest_framework.test import APITestCase

from apps.payments.models import SubscriptionPlan
from apps.videos.models import VideoProject
from apps.videos.services import link_probe, link_probe_worker, link_validation, ytdlp_safe
from apps.videos.services.link_validation import LinkRejected, validate_video_link
from apps.videos.tests.test_active_workspace import EDITOR, VIEWER, fund, header, home_of, join, make_user, team

URL = reverse('project-probe-link')
SECRET = 'SECRET_LOG_X'
PUBLIC = {'142.250.0.1'}
PRIVATE = {'10.0.0.7'}
VIDEO = {
    'kind': 'video', 'title': 'Charla completa', 'duration': 2700.5, 'is_live': False,
    'thumbnail': 'https://i.ytimg.com/vi/abc/hq.jpg', 'webpage_url': 'https://www.youtube.com/watch?v=abc',
}


def resolving(addresses=PUBLIC, **kwargs):
    """Answers the validator's DNS lookups (only them: socket.getaddrinfo stays guarded)."""
    if 'side_effect' in kwargs:
        return patch.object(link_validation, '_resolve', **kwargs)
    return patch.object(link_validation, '_resolve', return_value=set(addresses))


def worker_answer(answer=None):
    return patch.object(link_probe, 'run_worker', return_value=answer if answer is not None else {'ok': VIDEO})


class LinkValidationTests(SimpleTestCase):
    def test_allowed_links_become_the_url_of_their_one_video(self):
        cases = {
            'https://www.youtube.com/watch?v=abc#t=1': ('https://www.youtube.com/watch?v=abc', 'YouTube'),
            'https://www.youtube.com/watch?v=abc&list=PL1&index=3': ('https://www.youtube.com/watch?v=abc', 'YouTube'),
            'http://YOUTU.BE/abc?si=x': ('https://www.youtube.com/watch?v=abc', 'YouTube'),
            'https://m.youtube.com/shorts/abc': ('https://www.youtube.com/watch?v=abc', 'YouTube'),
            'https://music.youtube.com/watch?v=abc': ('https://www.youtube.com/watch?v=abc', 'YouTube'),
            '  https://youtube.com.:443/watch?v=abc ': ('https://www.youtube.com/watch?v=abc', 'YouTube'),
            'https://vimeo.com/123': ('https://vimeo.com/123', 'Vimeo'),
            'https://vimeo.com/123/abcdef0?share=copy': ('https://vimeo.com/123/abcdef0', 'Vimeo'),
            'https://player.vimeo.com/video/123?h=ff&autoplay=1': ('https://player.vimeo.com/video/123?h=ff', 'Vimeo'),
            'https://www.twitch.tv/videos/1': ('https://www.twitch.tv/videos/1', 'Twitch'),
            'https://m.twitch.tv/videos/1': ('https://www.twitch.tv/videos/1', 'Twitch'),
            'https://www.twitch.tv/canal/clip/Slug-1': ('https://clips.twitch.tv/Slug-1', 'Twitch'),
            'https://clips.twitch.tv/Slug': ('https://clips.twitch.tv/Slug', 'Twitch'),
            'https://player.twitch.tv/?video=v42&parent=x': ('https://www.twitch.tv/videos/42', 'Twitch'),
            'https://drive.google.com/file/d/x/view': ('https://drive.google.com/file/d/x/view', 'Google Drive'),
            'https://drive.google.com/uc?id=x&export=download': ('https://drive.google.com/file/d/x/view', 'Google Drive'),
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
            'https://[::ffff:127.0.0.1]/': 'unsupported',
            'http://2130706433/': 'unsupported',
            'https://0x7f000001/': 'unsupported',
            'https://youtube.com@127.0.0.1/': 'unsupported',
            'https://user:pw@youtube.com/watch?v=a': 'unsupported',
            'https://youtube.com:8080/watch?v=a': 'unsupported',
            'https://youtube.com.evil.test/watch?v=a': 'unsupported',
            'https://evilyoutube.com/watch?v=a': 'unsupported',
            'https://sites.google.com/x': 'unsupported',
            'https://example.com/video.mp4': 'unsupported',
            'https://youtube.com\\@127.0.0.1/': 'unsupported',
            'https://' + 'a' * 600 + '.youtube.com/': 'invalid',
            # Subdomains other than the listed ones: YouTube's consent page hands
            # any "continue" URL to yt-dlp.
            'https://consent.youtube.com/m?continue=http%3A%2F%2F10.0.0.1%2F': 'unsupported',
            'https://accounts.youtube.com/x': 'unsupported',
            'https://x.youtu.be/abc': 'unsupported',
            'https://evil.drive.google.com/file/d/x': 'unsupported',
            # Host labels: letters, digits and hyphens only.
            'https://evil.com;.youtube.com/watch?v=a': 'unsupported',
            'https://127.0.0.1%23.youtube.com/': 'unsupported',
            'https://*.youtube.com/x': 'unsupported',
            'https://a_b.youtube.com/x': 'unsupported',
            'https://evil.com&.vimeo.com/x': 'unsupported',
            'https://ｙｏｕｔｕｂｅ.com/watch?v=a': 'unsupported',
            'https://yоutube.com/watch?v=a': 'unsupported',  # Cyrillic o
            'https://.youtube.com/x': 'unsupported',
            # Not one video.
            'https://www.youtube.com/': 'unsupported',
            'https://www.youtube.com/@canal': 'unsupported',
            'https://www.youtube.com/playlist?list=PL1': 'unsupported',
            'https://www.youtube.com/watch?list=PL1': 'unsupported',
            'https://www.twitch.tv/canal': 'unsupported',
            'https://vimeo.com/usuario': 'unsupported',
            'https://drive.google.com/drive/folders/x': 'unsupported',
        }
        for raw, code in refused.items():
            with self.subTest(raw):
                with self.assertRaises(LinkRejected) as caught:
                    validate_video_link(raw)
                self.assertEqual(caught.exception.code, code)

    def test_an_allowed_host_that_resolves_to_a_private_address_is_refused(self):
        for addresses in (PRIVATE, {'::ffff:127.0.0.1'}, {'142.250.0.1', '127.0.0.1'}):
            with self.subTest(addresses), resolving(addresses), self.assertRaises(LinkRejected):
                validate_video_link('https://www.youtube.com/watch?v=abc', resolve=True)
        with resolving(PUBLIC) as dns:
            self.assertEqual(validate_video_link('https://youtu.be/a', resolve=True).host, 'www.youtube.com')
        dns.assert_called_once_with('www.youtube.com', 443)

    def test_the_lookup_is_patched_here_and_not_in_the_socket_module(self):
        before = socket.getaddrinfo
        with resolving(PUBLIC):
            self.assertIs(socket.getaddrinfo, before)


class FakeYdl:
    """Stands in for GuardedYoutubeDL: answers extract_info from a list, records the calls."""

    def __init__(self, *answers, error=None):
        self.answers, self.error, self.calls, self.processed = list(answers), error, [], []

    def __call__(self, options):
        self.options = options
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def extract_info(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if self.error is not None:
            raise self.error
        return self.answers.pop(0)

    def process_ie_result(self, info, download=True):
        self.processed.append(info)
        for hook in self.options.get('progress_hooks', []):
            hook({'downloaded_bytes': info.get('_bytes', 1)})
        if not info.get('_no_file'):
            with open(self.options['outtmpl'], 'wb') as handle:
                handle.write(b'x' * info.get('_bytes', 1))
        return info


class WorkerTests(SimpleTestCase):
    """The child's own logic, run in this process with yt-dlp's YoutubeDL replaced."""

    def probe_with(self, fake, url='https://www.youtube.com/watch?v=abc'):
        with patch.object(ytdlp_safe, 'GuardedYoutubeDL', fake):
            return link_probe_worker.probe(url)

    def test_errors_are_reduced_to_a_category_without_the_providers_text(self):
        from yt_dlp.utils import DownloadError

        cases = {
            f'ERROR: [youtube] abc: Private video. Sign in {SECRET}': 'unavailable',
            f'ERROR: Video unavailable {SECRET}': 'unavailable',
            f'ERROR: Unsupported URL: {SECRET}': 'unsupported',
            f'ERROR: No suitable extractor found for URL {SECRET}': 'unsupported',
            f'ERROR: Unable to download webpage: blocked non-public address {SECRET}': 'unsupported',
            f'ERROR: HTTP Error 503 {SECRET}': 'provider_error',
        }
        for message, category in cases.items():
            with self.subTest(category):
                answer = self.probe_with(FakeYdl(error=DownloadError(message)))
                self.assertEqual(answer, {'error': category, 'class': 'DownloadError'})

    def test_it_reads_without_downloading_and_only_with_the_sites_extractors(self):
        fake = FakeYdl({'title': 'T', 'duration': 12, 'webpage_url': 'u'})
        answer = self.probe_with(fake)

        self.assertEqual(fake.options['allowed_extractors'], ['youtube', 'vimeo', 'twitch:vod', 'twitch:clips', 'googledrive'])
        self.assertTrue(fake.options['skip_download'] and fake.options['noplaylist'])
        self.assertEqual(fake.options['retries'], 0)
        self.assertEqual(fake.calls[0][1], {'download': False, 'process': False})
        self.assertEqual(fake.processed, [])
        self.assertEqual(answer['ok']['duration'], 12)

    def test_the_allowed_extractor_names_exist_in_the_installed_yt_dlp(self):
        from yt_dlp.extractor import gen_extractor_classes

        by_name = {ie.IE_NAME.lower(): ie.ie_key() for ie in gen_extractor_classes()}
        self.assertEqual({by_name[name] for name in ytdlp_safe.ALLOWED_EXTRACTORS}, set(ytdlp_safe.FOLLOW_EXTRACTORS))

    def test_a_redirect_is_followed_only_through_a_known_extractor_and_an_allowed_link(self):
        video = {'title': 'Video', 'duration': 30}
        fake = FakeYdl({'_type': 'url', 'url': 'https://www.youtube.com/watch?v=a', 'ie_key': 'Youtube'}, video)
        with resolving():
            self.assertEqual(ytdlp_safe.extract_single_video(fake, 'https://www.youtube.com/watch?v=b'), video)
        self.assertEqual(fake.calls[1][1]['ie_key'], 'Youtube')

        for hop in (
            {'_type': 'url', 'url': 'http://10.0.0.1/Mediasite/Play/x', 'ie_key': 'Mediasite'},
            {'_type': 'url', 'url': 'http://10.0.0.1/x', 'ie_key': 'Youtube'},  # a known extractor, a link off the sites
            {'_type': 'url_transparent', 'url': 'https://example.com/v', 'ie_key': 'Vimeo'},
        ):
            with self.subTest(hop), resolving():
                fake = FakeYdl(hop, video)
                with self.assertRaises(ytdlp_safe.LinkRefused) as caught:
                    ytdlp_safe.extract_single_video(fake, 'https://www.youtube.com/watch?v=b')
                self.assertEqual(caught.exception.code, 'unsupported')
                self.assertEqual(len(fake.calls), 1)

    def test_lists_channels_and_live_streams_are_refused(self):
        cases = {
            'not_a_video': [{'_type': 'playlist', 'entries': []}, {'_type': 'multi_video'}],
            'live': [{'title': 'x', 'is_live': True}, {'title': 'x', 'live_status': 'is_upcoming'},
                     {'title': 'x', 'live_status': 'post_live'}],
        }
        for code, infos in cases.items():
            for info in infos:
                with self.subTest(info), self.assertRaises(ytdlp_safe.LinkRefused) as caught:
                    ytdlp_safe.extract_single_video(FakeYdl(info), 'https://www.youtube.com/watch?v=b')
                self.assertEqual(caught.exception.code, code)
        answer = self.probe_with(FakeYdl({'title': 'x', 'is_live': True}))
        self.assertTrue(answer['ok']['is_live'])

    def test_the_worker_script_answers_one_json_line(self):
        """The script itself, as the parent runs it (isolated mode, its modules next to it)."""
        result = subprocess.run(
            [sys.executable, '-I', link_probe.WORKER_PATH], capture_output=True, timeout=60, check=False,
        )
        self.assertEqual(json.loads(result.stdout), {'error': 'provider_error', 'class': 'UsageError'})


def _script(body):
    with tempfile.NamedTemporaryFile('w', suffix='.py', delete=False) as script:
        script.write(body)
    return script.name


def _alive(pid):
    """True while ``pid`` runs (a zombie waiting for its reaper counts as gone)."""
    try:
        with open(f'/proc/{pid}/stat') as stat:
            return stat.read().rsplit(')', 1)[1].split()[0] != 'Z'
    except FileNotFoundError:
        return False


class ChildProcessTests(SimpleTestCase):
    """run_worker with real child processes."""

    def test_at_the_time_limit_the_child_and_what_it_started_are_killed(self):
        pidfile = tempfile.mktemp()
        script = _script(
            'import subprocess, sys, time\n'
            f'child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])\n'
            f'open({pidfile!r}, "w").write(str(child.pid))\n'
            'time.sleep(60)\n'
        )
        started = time.monotonic()
        with patch.object(link_probe, 'WORKER_PATH', script), override_settings(LINK_PROBE_TIMEOUT_SECONDS=2):
            with self.assertRaises(link_probe.LinkProbeError) as caught:
                link_probe.run_worker('https://www.youtube.com/watch?v=abc')
        self.assertEqual((caught.exception.code, caught.exception.status), ('timeout', 504))
        self.assertLess(time.monotonic() - started, 15)
        grandchild = int(open(pidfile).read())
        deadline = time.monotonic() + 5
        while _alive(grandchild) and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertFalse(_alive(grandchild))

    def test_the_child_does_not_see_the_web_process_secrets(self):
        script = _script('import json, os\nprint(json.dumps({"keys": sorted(os.environ)}))\n')
        secrets = {'SECRET_KEY': 'x', 'DATABASE_URL': 'postgres://x', 'CLOUDFLARE_R2_SECRET_ACCESS_KEY': 'x',
                   'GEMINI_API_KEY': 'x', 'HTTPS_PROXY': 'http://proxy.test:3128', 'PATH': os.environ.get('PATH', '/usr/bin')}
        with patch.object(link_probe, 'WORKER_PATH', script), patch.dict(os.environ, secrets):
            keys = set(link_probe.run_worker('https://www.youtube.com/watch?v=abc')['keys'])
        self.assertTrue({'PATH', 'HTTPS_PROXY'} <= keys)
        self.assertFalse(keys & {'SECRET_KEY', 'DATABASE_URL', 'CLOUDFLARE_R2_SECRET_ACCESS_KEY', 'GEMINI_API_KEY'})


class ProbeLinkTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.alice = make_user('alice')
        self.home = home_of(self.alice)
        # Explicit: a new workspace's default plan may not allow VIDEO's 45 min
        # nor the members these tests add.
        self.plan = SubscriptionPlan.objects.create(
            name='Links largos', max_video_duration_seconds=3600, max_members=5,
        )
        self.home.subscription_plan = self.plan
        self.home.save()
        self.client.force_authenticate(self.alice)

    def probe(self, video_url='https://www.youtube.com/watch?v=abc&list=PL1', answer=None, **extra):
        with resolving(), worker_answer(answer) as run:
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
        # The child reads the one video, never the list.
        self.assertEqual([call.args for call in self.run_calls], [('https://www.youtube.com/watch?v=abc',)])

    def test_a_second_probe_of_the_same_link_is_cached(self):
        self.probe()
        response = self.probe()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.run_calls, [])

    def test_a_canonical_url_off_the_site_of_the_link_is_ignored(self):
        answer = {'ok': {**VIDEO, 'webpage_url': 'http://10.0.0.1/x', 'thumbnail': 'http://insecure/x.jpg'}}
        body = self.probe('https://youtu.be/abc', answer=answer).json()
        self.assertEqual(body['video_url'], 'https://www.youtube.com/watch?v=abc')
        self.assertIsNone(body['thumbnail_url'])
        cache.clear()
        body = self.probe('https://vimeo.com/9').json()  # VIDEO's canonical page is on YouTube
        self.assertEqual((body['video_url'], body['provider']), ('https://vimeo.com/9', 'Vimeo'))

    def test_bad_links_and_ssrf_attempts_are_refused_without_any_request(self):
        for raw in ('no es un link', 'file:///etc/passwd', 'http://localhost:8000/', 'http://127.0.0.1/',
                    'http://192.168.0.10/v.mp4', 'http://169.254.169.254/latest/', 'https://example.com/v.mp4',
                    'https://consent.youtube.com/m?continue=http%3A%2F%2F127.0.0.1%2F',
                    'https://www.youtube.com/@canal'):
            with self.subTest(raw), patch.object(link_probe, 'run_worker') as run, \
                    patch.object(link_validation, '_resolve') as dns:
                response = self.client.post(URL, {'video_url': raw}, format='json')
                self.assertEqual(response.status_code, 400)
                self.assertIn(response.json()['code'], ('invalid', 'unsupported'))
                run.assert_not_called()
                dns.assert_not_called()

    def test_an_allowed_host_resolving_to_a_private_address_is_refused(self):
        with resolving(PRIVATE), patch.object(link_probe, 'run_worker') as run:
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
            ({'ok': 'not a dict'}, 502, 'provider_error'),
            ({}, 502, 'provider_error'),
        ]
        for answer, status_code, code in cases:
            with self.subTest(answer):
                cache.clear()
                with resolving(), worker_answer(answer):
                    response = self.client.post(URL, {'video_url': 'https://vimeo.com/1'}, format='json')
                self.assertEqual(response.status_code, status_code)
                self.assertEqual(response.json()['code'], code)

    def test_a_timeout_is_a_504_and_the_logs_carry_no_link(self):
        secret_url = f'https://www.youtube.com/watch?v={SECRET}'
        with resolving(), patch.object(link_probe, 'run_worker', side_effect=link_probe._error(link_probe.TIMEOUT)), \
                self.assertLogs('apps.videos', 'INFO') as logs:
            response = self.client.post(URL, {'video_url': secret_url}, format='json')

        self.assertEqual(response.status_code, 504)
        self.assertEqual(response.json()['code'], 'timeout')
        self.assertNotIn(SECRET, '\n'.join(logs.output))
        self.assertNotIn(SECRET, json.dumps(response.json()))

    def test_a_host_that_does_not_resolve_is_a_502(self):
        with resolving(side_effect=socket.gaierror('x')):
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
        shared.subscription_plan = self.plan
        shared.save()
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
        self.folder = tempfile.mkdtemp()

    def test_a_project_cannot_be_created_from_an_internal_link(self):
        for raw in ('http://127.0.0.1:8000/admin/', 'http://169.254.169.254/latest/meta-data/', 'file:///etc/passwd',
                    'https://example.com/v.mp4', 'https://consent.youtube.com/m?continue=http%3A%2F%2F10.0.0.1%2F',
                    'https://www.youtube.com/playlist?list=PL1'):
            with self.subTest(raw), patch('apps.videos.views.process_initial_ingestion.delay') as ingest:
                response = self.client.post(reverse('project-list'), {'title': 'X', 'video_url': raw}, format='json')
                self.assertEqual(response.status_code, 400)
                self.assertIn('video_url', response.json())
                ingest.assert_not_called()
        self.assertFalse(VideoProject.objects.exists())

    def test_an_allowed_link_is_created_as_its_one_video(self):
        with patch('apps.videos.views.process_initial_ingestion.delay'):
            response = self.client.post(
                reverse('project-list'), {'title': 'X', 'video_url': 'HTTP://WWW.YOUTUBE.COM/watch?v=abc&list=PL#t=3'},
                format='json',
            )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()['video_url'], 'https://www.youtube.com/watch?v=abc')

    def test_the_download_refuses_a_saved_internal_link_without_calling_yt_dlp(self):
        from apps.videos.tasks.shared import UnsupportedLinkError, _is_retryable_pipeline_error, download_from_youtube

        with patch.object(ytdlp_safe, 'GuardedYoutubeDL') as ydl:
            with self.assertRaises(UnsupportedLinkError) as caught:
                download_from_youtube('http://169.254.169.254/latest/meta-data/', self.folder)
            with resolving(PRIVATE), self.assertRaises(UnsupportedLinkError):
                download_from_youtube('https://www.youtube.com/watch?v=a', self.folder)
        ydl.assert_not_called()
        self.assertFalse(_is_retryable_pipeline_error(caught.exception))

    def test_the_download_takes_one_video_and_refuses_lists_and_live_streams(self):
        from apps.videos.tasks.shared import UnsupportedLinkError, download_from_youtube

        fake = FakeYdl({'title': 'ok', 'duration': 10})
        with resolving(), patch.object(ytdlp_safe, 'GuardedYoutubeDL', fake):
            path = download_from_youtube('https://youtu.be/abc?list=PL', self.folder)
        self.assertTrue(os.path.exists(path))
        self.assertEqual(fake.calls[0], ('https://www.youtube.com/watch?v=abc', {'download': False, 'process': False}))
        self.assertEqual(fake.options['allowed_extractors'], ytdlp_safe.ALLOWED_EXTRACTORS)
        self.assertIn('max_filesize', fake.options)

        for info in ({'_type': 'playlist', 'entries': []}, {'title': 'vivo', 'is_live': True}):
            with self.subTest(info), resolving(), patch.object(ytdlp_safe, 'GuardedYoutubeDL', FakeYdl(info)) as ydl:
                with self.assertRaises(UnsupportedLinkError):
                    download_from_youtube('https://www.youtube.com/watch?v=abc', self.folder)
                self.assertEqual(ydl.processed, [])

    def test_the_plan_duration_binds_before_downloading(self):
        from apps.videos.tasks.shared import LinkTooLongError, _is_retryable_pipeline_error, download_from_youtube

        fake = FakeYdl({'title': 'largo', 'duration': 601})
        with resolving(), patch.object(ytdlp_safe, 'GuardedYoutubeDL', fake), self.assertRaises(LinkTooLongError) as caught:
            download_from_youtube('https://www.youtube.com/watch?v=abc', self.folder, max_duration_seconds=600)
        self.assertEqual(fake.processed, [])
        self.assertFalse(_is_retryable_pipeline_error(caught.exception))

    @override_settings(LINK_DOWNLOAD_MAX_BYTES=100)
    def test_a_file_over_the_size_cap_is_refused_and_leaves_nothing(self):
        from apps.videos.tasks.shared import LinkTooLargeError, _is_retryable_pipeline_error, download_from_youtube

        fake = FakeYdl({'title': 'pesado', 'duration': 10, '_bytes': 500})
        with resolving(), patch.object(ytdlp_safe, 'GuardedYoutubeDL', fake), self.assertRaises(LinkTooLargeError) as caught:
            download_from_youtube('https://www.youtube.com/watch?v=abc', self.folder)
        self.assertEqual(os.listdir(self.folder), [])
        self.assertFalse(_is_retryable_pipeline_error(caught.exception))


class LinkDurationAtIngestionTests(APITestCase):
    """The plan's limit is binding when the worker fetches the link, measured if the site did not say."""

    def setUp(self):
        self.alice = make_user('alice')
        self.home = home_of(self.alice)
        self.home.subscription_plan = SubscriptionPlan.objects.create(name='Corto', max_video_duration_seconds=600)
        self.home.save()

    def link_project(self):
        return VideoProject.objects.create(
            workspace=self.home, uploaded_by=self.alice, title='Link', video_url='https://www.youtube.com/watch?v=abc',
        )

    def fake_download(self, url, folder, max_duration_seconds=None):
        self.limit = max_duration_seconds
        path = os.path.join(folder, 'bajado.mp4')
        with open(path, 'wb') as handle:
            handle.write(b'video')
        return path

    def test_a_link_longer_than_the_plan_fails_at_the_prepare_stage(self):
        from apps.videos.tasks.ingestion import _stage_prepare_source
        from apps.videos.tasks.shared import LinkTooLongError

        project = self.link_project()
        with patch('apps.videos.tasks.ingestion.download_from_youtube', side_effect=self.fake_download), \
                patch('apps.videos.services.storage_service.CloudflareR2Manager.upload_video', return_value='k'), \
                patch('apps.videos.tasks.ingestion.generate_proxy_task.delay'), \
                patch('apps.videos.tasks.ingestion._probe_video', return_value=(900.0, [640, 360])):
            with self.assertRaises(LinkTooLongError):
                _stage_prepare_source(project.pk)
        self.assertEqual(self.limit, 600)
        project.refresh_from_db()
        self.assertEqual(project.pipeline_stage, 'uploaded')

    def test_the_app_limit_applies_without_a_plan(self):
        from apps.videos.tasks.shared import link_duration_limit

        project = self.link_project()
        self.assertEqual(link_duration_limit(project), 600)
        self.home.subscription_plan = None
        self.home.save()
        project.refresh_from_db()
        with override_settings(AI_UPLOAD_MAX_DURATION_SECONDS=10800):
            self.assertEqual(link_duration_limit(project), 10800)


# --- real yt-dlp against local servers (no internet) ------------------------------

class _Recorder(http.server.BaseHTTPRequestHandler):
    hits = None
    location = None

    def _answer(self):
        type(self).hits.append(self.path)
        if type(self).location:
            self.send_response(302)
            self.send_header('Location', type(self).location)
        else:
            self.send_response(200)
        self.send_header('Content-Length', '2')
        self.end_headers()
        self.wfile.write(b'{}')

    do_GET = do_HEAD = do_POST = _answer

    def log_message(self, *args):
        pass


def _serve(host, location=None):
    handler = type('Handler', (_Recorder,), {'hits': [], 'location': location})
    server = http.server.HTTPServer((host, 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, handler.hits


@pytest.fixture
def servers(network_guard, monkeypatch):
    """An "internal" server (127.0.0.2) and an outer one that redirects to it, reachable through the guard."""
    internal, internal_hits = _serve('127.0.0.2')
    target = f'http://127.0.0.2:{internal.server_address[1]}/latest/meta-data/iam/security-credentials/role'
    outer, outer_hits = _serve('127.0.0.1', location=target)
    monkeypatch.setattr(network_guard, 'allowed_ports', frozenset(
        {*network_guard.allowed_ports, internal.server_address[1], outer.server_address[1]}))
    yield {'outer': outer.server_address[1], 'internal': internal.server_address[1],
           'outer_hits': outer_hits, 'internal_hits': internal_hits}
    for server in (internal, outer):
        server.shutdown()
        server.server_close()


def _options():
    options = link_probe_worker.ydl_options()
    options['proxy'] = ''  # straight to the local servers, whatever the environment says
    return options


def test_real_yt_dlp_the_consent_page_bypass_makes_no_request(servers):
    """consent.youtube.com hands its continue URL to any extractor (Mediasite here): refused before any request."""
    from yt_dlp.utils import DownloadError

    inner = f"http://127.0.0.1:{servers['outer']}/Mediasite/Play/0123456789abcdef0123456789abcdef"
    consent = 'https://consent.youtube.com/m?continue=' + quote(inner, safe='')
    with ytdlp_safe.GuardedYoutubeDL(_options()) as ydl:
        with pytest.raises(DownloadError, match='No suitable extractor'):
            ytdlp_safe.extract_single_video(ydl, consent)
    assert link_probe_worker.probe(consent)['error'] == 'unsupported'

    from apps.videos.tasks.shared import UnsupportedLinkError, download_from_youtube

    with pytest.raises(UnsupportedLinkError):
        download_from_youtube(consent, tempfile.mkdtemp())
    assert servers['outer_hits'] == [] and servers['internal_hits'] == []


@pytest.mark.parametrize('handler', ['Requests', 'Urllib'])
def test_real_yt_dlp_a_redirect_to_an_internal_host_is_refused(servers, handler, monkeypatch):
    """An allowed extractor's own fetch is redirected to 127.0.0.2: the redirect is never followed."""
    # 127.0.0.1 stands for a public site here; 127.0.0.2 stays internal.
    real = link_validation.is_public_address
    monkeypatch.setattr(link_validation, 'is_public_address', lambda address: address == '127.0.0.1' or real(address))
    with ytdlp_safe.GuardedYoutubeDL(_options()) as ydl:
        handlers = ydl._request_director.handlers
        for key in [key for key in handlers if key != handler]:
            handlers.pop(key).close()
        assert list(handlers) == [handler]
        youtube = ydl.get_info_extractor('Youtube')
        with pytest.raises(Exception, match='blocked non-public address'):
            youtube._download_webpage(f"http://127.0.0.1:{servers['outer']}/watch", None)
    assert servers['outer_hits'] == ['/watch']
    assert servers['internal_hits'] == []


def test_real_yt_dlp_a_request_to_an_internal_address_is_refused(servers):
    with ytdlp_safe.GuardedYoutubeDL(_options()) as ydl:
        with pytest.raises(ytdlp_safe.BlockedRequest):
            ydl.urlopen(f"http://127.0.0.2:{servers['internal']}/latest/meta-data/")
        assert set(ydl._request_director.handlers) <= {'Urllib', 'Requests'}
    assert servers['internal_hits'] == []
