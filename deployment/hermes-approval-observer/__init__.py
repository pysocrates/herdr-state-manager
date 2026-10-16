"""Opt-in, best-effort OBSERVER. Never answers or vetoes native approvals.

Installed Hermes 0.19.0 has no request ID in these hooks. Each hook is an
uncorrelated observation; session/turn/tool IDs are NOT request identities.
"""
import hashlib
import json
import logging
import os
from pathlib import Path
from queue import Queue
from threading import Thread
import time
from urllib.request import Request, build_opener, HTTPRedirectHandler
from urllib.parse import urlsplit
from uuid import uuid4

log = logging.getLogger(__name__)
CHOICES = {'once', 'session', 'always', 'deny', 'timeout', 'smart_approve', 'smart_deny'}


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class Observer:
    def __init__(self, project, url, token, sender=None, capacity=128, start=True):
        parsed = urlsplit(url)
        if parsed.username or parsed.password or parsed.query or parsed.fragment or not (
            parsed.scheme == 'https' or parsed.scheme == 'http' and parsed.hostname in {'127.0.0.1', 'localhost', '::1'}
        ):
            raise ValueError('Use HTTPS or loopback HTTP without URL credentials')
        self.project, self.url, self.token = project, url.rstrip('/'), token
        self.queue = Queue(maxsize=capacity)
        self.dropped = 0
        self.sender = sender or self.send
        if start:
            Thread(target=self.deliver, daemon=True, name='hsm-approval-observer').start()

    def observe(self, phase, **kwargs):
        try:
            surface, choice = kwargs.get('surface'), kwargs.get('choice')
            if surface not in {'cli', 'gateway', 'smart'} or phase == 'post' and choice not in CHOICES:
                self.dropped += 1
                return None
            payload = {'project_id': self.project, 'event_id': str(uuid4()), 'phase': phase, 'surface': surface}
            # Do not copy command, description, pattern keys, raw session IDs or arbitrary kwargs.
            session = kwargs.get('session_key')
            if isinstance(session, str) and len(session) <= 4096:
                payload['session_key_hash'] = hashlib.sha256(session.encode()).hexdigest()
            if phase == 'post':
                payload['choice'] = choice
            self.queue.put_nowait(payload)
        except Exception:
            # No exception/log I/O on the safety-critical approval thread.
            self.dropped += 1
        return None

    def pre(self, **kwargs):
        return self.observe('pre', **kwargs)

    def post(self, **kwargs):
        return self.observe('post', **kwargs)

    def send(self, payload):
        req = Request(self.url + '/api/v1/approval-observations',
            data=json.dumps(payload).encode(), method='POST',
            headers={'Authorization': 'Bearer ' + self.token, 'Content-Type': 'application/json'})
        with build_opener(NoRedirect()).open(req, timeout=2) as response:
            if response.status != 200:
                raise RuntimeError('Observation rejected')

    def deliver_one(self, payload):
        for attempt in range(3):
            try:
                self.sender(payload)
                return
            except Exception:
                if attempt < 2:
                    time.sleep(0.1 * (attempt + 1))
        self.dropped += 1
        log.warning('HSM approval observation delivery failed; telemetry dropped')

    def deliver(self):
        while True:
            payload = self.queue.get()
            try:
                self.deliver_one(payload)
            finally:
                self.queue.task_done()


def register(ctx):
    # Profile-local settings avoid modifying global shell or other profiles.
    settings_path = Path(__file__).with_name('settings.json')
    settings = json.loads(settings_path.read_text()) if settings_path.exists() else {}
    def setting(key, default=None):
        return os.environ.get(key, settings.get(key, default))
    # Read credentials once at registration, not inside approval callbacks.
    token_file = setting('HSM_OBSERVER_TOKEN_FILE')
    project = setting('HSM_OBSERVER_PROJECT')
    if not isinstance(token_file, str) or not isinstance(project, str) or not project:
        raise ValueError('Configure observer token file and explicit project')
    token = Path(token_file).read_text().strip()
    if len(token) < 32:
        raise ValueError('Observer credential is missing or too short')
    observer = Observer(setting('HSM_OBSERVER_PROJECT'), setting('HSM_OBSERVER_URL', 'http://127.0.0.1:8787'), token)
    ctx.register_hook('pre_approval_request', observer.pre)
    ctx.register_hook('post_approval_response', observer.post)
