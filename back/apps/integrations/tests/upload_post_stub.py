"""
A local stand-in of Upload-Post, for the tests and for a rehearsal (runbook
documents/runbooks/redes-sociales-prueba.md, «Ensayo con un Upload-Post falso»).
It answers what docs.upload-post.com documents for the calls OneCreator makes
(apps/integrations/social/upload_post.py). Standard library only:

    python back/apps/integrations/tests/upload_post_stub.py <port> [log.jsonl]

Then UPLOAD_POST_BASE_URL=http://127.0.0.1:<port> (with DEBUG) and any
UPLOAD_POST_API_KEY. The linking page is GET /connect?token=...: opening it
links the network the link was asked for.

Control, between steps: GET /ctl?upload=<mode>[&status_after=<n>][&privacy=A,B][&reauth=tiktok]
  sync (the default)   the network's result in the answer
  async                only a request_id; its status completes after <n> reads (2)
  pending              only a request_id; its status never completes
  fail:<error_code>    the network failed with that code (account_reauth_required, ...)
  http:<status>        that HTTP status with an HTML page, and nothing published
  gateway504           504, but the upload went on: its status completes after <n> reads
  drop                 reads the request and closes the connection; the upload goes on
GET /state shows the profiles, the uploads it received and the modes.
"""
import json
import socket
import sys
import threading
import time
import uuid
from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

HANDLES = {'tiktok': 'ana.demo', 'instagram': 'ana.demo.reels', 'youtube': '@AnaDemo'}
ALL_PRIVACY = ['PUBLIC_TO_EVERYONE', 'MUTUAL_FOLLOW_FRIENDS', 'FOLLOWER_OF_CREATOR', 'SELF_ONLY']


class StubState:
    def __init__(self, log_path=None):
        self.lock = threading.Lock()
        self.log_path = log_path
        self.profiles = {}   # username -> {network: account}
        self.tokens = {}     # token -> (username, network)
        self.jobs = {}       # request_id -> {network, reads, after, outcome, url}
        self.replies = {}    # Idempotency-Key -> (status, body)
        self.uploads = []    # what each POST /api/upload carried
        self.calls = []      # (method, path) of every call
        self.links = []      # what each generate-jwt asked for
        self.modes = {'upload': 'sync', 'status_after': 2, 'privacy': list(ALL_PRIVACY), 'reauth': ''}

    def log(self, entry):
        if not self.log_path:
            return
        entry = {**entry, 't': time.strftime('%H:%M:%S')}
        with open(self.log_path, 'a') as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + '\n')

    def snapshot(self):
        return {
            'profiles': {name: sorted(linked) for name, linked in self.profiles.items()},
            'uploads': self.uploads, 'links': self.links, 'modes': self.modes,
        }


def _form(content_type, raw):
    """The multipart fields as a list of (name, value): `platform[]` may repeat."""
    message = BytesParser(policy=policy.HTTP).parsebytes(
        b'Content-Type: ' + content_type.encode() + b'\r\n\r\n' + raw,
    )
    fields = []
    for part in message.iter_parts():
        name = part.get_param('name', header='content-disposition')
        fields.append((name, (part.get_payload(decode=True) or b'').decode()))
    return fields


def make_handler(state):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def log_message(self, *args):
            pass

        # --- plumbing ---

        def _raw(self):
            length = int(self.headers.get('Content-Length') or 0)
            return self.rfile.read(length) if length else b''

        def _json_body(self):
            try:
                return json.loads(self._raw() or b'{}')
            except ValueError:
                return {}

        def _send(self, status, body, content_type='application/json'):
            data = body.encode() if isinstance(body, str) else json.dumps(body).encode()
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _authorized(self):
            if (self.headers.get('Authorization') or '').startswith('Apikey ') and len(self.headers['Authorization']) > 7:
                return True
            self._send(401, {'success': False, 'message': 'Invalid or expired token'})
            return False

        def _route(self):
            url = urlparse(self.path)
            query = {key: values[0] for key, values in parse_qs(url.query).items()}
            with state.lock:
                state.calls.append((self.command, url.path))
            return url.path, query

        # --- GET ---

        def do_GET(self):
            path, query = self._route()
            if path == '/ctl':
                with state.lock:
                    for key in ('upload', 'reauth'):
                        if key in query:
                            state.modes[key] = query[key]
                    if 'status_after' in query:
                        state.modes['status_after'] = int(query['status_after'])
                    if 'privacy' in query:
                        state.modes['privacy'] = [level for level in query['privacy'].split(',') if level]
                return self._send(200, state.modes)
            if path == '/state':
                with state.lock:
                    return self._send(200, state.snapshot())
            if path == '/connect':
                with state.lock:
                    found = state.tokens.get(query.get('token'))
                    if found:
                        username, network = found
                        state.profiles.setdefault(username, {})[network] = {
                            'username': f'id-{network}-{uuid.uuid4().hex[:8]}', 'handle': HANDLES[network],
                            'display_name': 'Ana Demo', 'reauth_required': False,
                        }
                if not found:
                    return self._send(404, '<h1>Link vencido</h1>', 'text/html; charset=utf-8')
                state.log({'op': 'connect_page', 'network': network})
                return self._send(200, f'<html><body><h1>Upload-Post falso</h1><p>Conectaste {network} como '
                                       f'{HANDLES[network]}.</p></body></html>', 'text/html; charset=utf-8')
            if not self._authorized():
                return
            if path.startswith('/api/uploadposts/users/'):
                username = unquote(path.rsplit('/', 1)[1])
                with state.lock:
                    linked = state.profiles.get(username)
                    if linked is None:
                        return self._send(404, {'success': False, 'message': 'Profile not found'})
                    reauth = set(state.modes['reauth'].split(','))
                    accounts = {
                        network: ({**linked[network], 'reauth_required': network in reauth} if network in linked else None)
                        for network in HANDLES
                    }
                state.log({'op': 'user', 'linked': sorted(linked)})
                return self._send(200, {'success': True, 'profile': {'username': username, 'social_accounts': accounts}})
            if path == '/api/uploadposts/tiktok/settings':
                with state.lock:
                    linked = state.profiles.get(query.get('profile'))
                    privacy = list(state.modes['privacy'])
                if linked is None:
                    return self._send(404, {'success': False, 'message': 'Profile not found.', 'error_code': 'PROFILE_NOT_FOUND'})
                if 'tiktok' not in linked:
                    return self._send(400, {'success': False, 'error_code': 'tiktok_reconnect_required'})
                return self._send(200, {
                    'success': True, 'privacy_level_options': privacy, 'max_video_post_duration_sec': 600,
                    'comment_disabled': False, 'duet_disabled': True, 'stitch_disabled': False,
                })
            if path == '/api/uploadposts/status':
                return self._status(query.get('request_id'))
            self._send(404, {'success': False, 'message': 'not found'})

        def _status(self, request_id):
            with state.lock:
                job = state.jobs.get(request_id)
                if job is None:
                    return self._send(404, {'request_id': request_id, 'status': 'not_found',
                                            'message': 'No upload request found with this ID'})
                job['reads'] += 1
                done = job['outcome'] != 'pending' and job['reads'] >= job['after']
                network = job['network']
            if not done:
                # As the docs show it: the entry says success while it is still queued.
                return self._send(200, {'request_id': request_id, 'status': 'in_progress', 'completed': 0, 'total': 1,
                                        'results': [{'platform': network, 'success': True, 'message': 'Queued'}]})
            if job['outcome'] == 'completed':
                entry = {'platform': network, 'success': True, 'status': 'completed', 'post_url': job['url'],
                         'message': 'Published'}
                return self._send(200, {'request_id': request_id, 'status': 'completed', 'completed': 1, 'total': 1,
                                        'results': [entry]})
            entry = {'platform': network, 'success': False, 'status': 'failed', 'error_code': job['outcome'],
                     'error': 'Texto del proveedor que puede citar el caption'}
            return self._send(200, {'request_id': request_id, 'status': 'failed', 'completed': 1, 'total': 1,
                                    'results': [entry]})

        # --- POST ---

        def do_POST(self):
            path, _ = self._route()
            if path == '/api/upload':
                return self._upload()
            body = self._json_body()
            if not self._authorized():
                return
            if path == '/api/uploadposts/users':
                username = body.get('username') or ''
                with state.lock:
                    if any(name.lower() == username.lower() for name in state.profiles):
                        return self._send(409, {'success': False, 'message': 'Username already in use'})
                    state.profiles[username] = {}
                state.log({'op': 'create_profile'})
                return self._send(201, {'success': True, 'profile': {'username': username, 'social_accounts': {}}})
            if path == '/api/uploadposts/users/generate-jwt':
                username = body.get('username')
                with state.lock:
                    if username not in state.profiles:
                        return self._send(404, {'success': False, 'error_code': 'PROFILE_NOT_FOUND'})
                    state.links.append({key: body.get(key) for key in ('platforms', 'redirect_url', 'language', 'show_calendar')})
                    token = uuid.uuid4().hex
                    state.tokens[token] = (username, (body.get('platforms') or ['tiktok'])[0])
                port = self.server.server_address[1]
                return self._send(200, {'success': True, 'duration': '48h',
                                        'access_url': f'http://127.0.0.1:{port}/connect?token={token}'})
            self._send(404, {'success': False, 'message': 'not found'})

        def _upload(self):
            raw = self._raw()
            if not self._authorized():
                return
            fields = _form(self.headers.get('Content-Type') or '', raw)
            values = dict(fields)
            key = self.headers.get('Idempotency-Key')
            with state.lock:
                mode = state.modes['upload']
                state.uploads.append({'fields': fields, 'idempotency_key': key, 'mode': mode})
                if key and key in state.replies:
                    status, reply = state.replies[key]
                    return self._send(status, {**reply, 'idempotent_replay': True})
            state.log({'op': 'upload', 'mode': mode, 'networks': [v for n, v in fields if n == 'platform[]']})
            network = values.get('platform[]')
            request_id = values.get('request_id') or uuid.uuid4().hex
            with state.lock:
                linked = state.profiles.get(values.get('user'))
            if linked is None:
                return self._send(400, {'success': False, 'message': 'Username not associated with any profile',
                                        'error_code': 'profile_not_found'})
            if network not in linked:
                return self._send(400, {'success': False, 'invalid_platforms': {network: 'not configured'},
                                        'message': 'None of the requested platforms are valid for profile'})
            post_url = {
                'tiktok': f'https://www.tiktok.com/@ana.demo/video/{uuid.uuid4().int % 10 ** 12}',
                'instagram': f'https://www.instagram.com/reel/{uuid.uuid4().hex[:11]}/',
                'youtube': f'https://www.youtube.com/shorts/{uuid.uuid4().hex[:11]}',
            }[network]
            if mode.startswith('http:'):
                return self._send(int(mode.split(':', 1)[1]), '<html>upstream error</html>', 'text/html')
            if mode.startswith('fail:'):
                reply = {'success': True, 'request_id': request_id, 'results': {network: {
                    'success': False, 'error': 'Texto del proveedor que puede citar el caption',
                    'error_code': mode.split(':', 1)[1],
                }}}
                return self._reply(key, 200, reply)
            if mode in ('async', 'pending', 'gateway504', 'drop'):
                with state.lock:
                    state.jobs[request_id] = {
                        'network': network, 'reads': 0, 'after': state.modes['status_after'], 'url': post_url,
                        'outcome': 'pending' if mode == 'pending' else 'completed',
                    }
                if mode == 'gateway504':
                    return self._send(504, '<html>504 Gateway Time-out</html>', 'text/html')
                if mode == 'drop':
                    try:
                        self.connection.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
                    self.close_connection = True
                    return None
                reply = {'success': True, 'message': 'Upload initiated successfully in background.',
                         'request_id': request_id, 'total_platforms': 1}
                return self._reply(key, 200, reply)
            reply = {'success': True, 'request_id': request_id, 'results': {network: {
                'success': True, 'url': post_url, 'publish_id': uuid.uuid4().hex[:12],
            }}, 'usage': {'count': len(state.uploads), 'limit': 100}}
            return self._reply(key, 200, reply)

        def _reply(self, key, status, reply):
            if key:
                with state.lock:
                    state.replies[key] = (status, reply)
            return self._send(status, reply)

        # --- DELETE ---

        def do_DELETE(self):
            path, _ = self._route()
            body = self._json_body()
            if not self._authorized():
                return
            if path == '/api/uploadposts/users/social':
                with state.lock:
                    linked = state.profiles.get(body.get('profile_username'))
                    if linked is None or body.get('social_platform') not in linked:
                        return self._send(404, {'success': False, 'message': 'Not connected'})
                    linked.pop(body['social_platform'])
                return self._send(200, {'success': True})
            if path == '/api/uploadposts/users':
                with state.lock:
                    if state.profiles.pop(body.get('username'), None) is None:
                        return self._send(404, {'success': False, 'message': 'No profile found'})
                return self._send(200, {'success': True, 'message': 'Perfil eliminado correctamente'})
            self._send(404, {'success': False})

    return Handler


class UploadPostStub:
    """The stand-in on 127.0.0.1, in a thread: start(), base_url, state, stop()."""

    def __init__(self, port=0, log_path=None):
        self.state = StubState(log_path)
        self.server = ThreadingHTTPServer(('127.0.0.1', port), make_handler(self.state))
        self.port = self.server.server_address[1]
        self.base_url = f'http://127.0.0.1:{self.port}'
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': 0.05}, daemon=True)

    def start(self):
        self.thread.start()
        return self

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)


if __name__ == '__main__':
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8791
    stub = UploadPostStub(port, sys.argv[2] if len(sys.argv) > 2 else None)
    print(f'Upload-Post falso en {stub.base_url}', flush=True)
    stub.server.serve_forever()
