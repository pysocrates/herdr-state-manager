"""REST transport shared by terminal clients. Never opens SQLite."""
import json
import os
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class ClientError(Exception):
    pass


class Client:
    def __init__(self, endpoint=None, token=None):
        self.endpoint = (endpoint or os.getenv('HSM_API_URL', 'http://127.0.0.1:8787')).rstrip('/')
        self.token = token if token is not None else (Path(os.environ['HSM_TOKEN_FILE']).read_text().strip() if os.getenv('HSM_TOKEN_FILE') else os.getenv('HSM_TOKEN', ''))
        parsed = urlsplit(self.endpoint)
        if parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ClientError('Invalid HSM_API_URL')
        if parsed.scheme == 'http' and parsed.hostname not in {'localhost', '127.0.0.1', '::1'}:
            raise ClientError('Remote API endpoints require HTTPS')

    def request(self, path, params=None, method='GET', data=None, idempotency_key=None):
        query = urlencode({k: v for k, v in (params or {}).items() if v is not None})
        headers = {'Authorization': 'Bearer ' + self.token, 'Content-Type': 'application/json'}
        if idempotency_key is not None:
            headers['Idempotency-Key'] = idempotency_key
        req = Request(self.endpoint + '/api/v1' + path + ('?' + query if query else ''),
                      data=json.dumps(data).encode() if data is not None else None,
                      headers=headers, method=method)
        try:
            with build_opener(NoRedirect()).open(req, timeout=10) as response:
                return json.load(response)
        except HTTPError as error:
            try:
                message = json.load(error)['error']['message']
            except (ValueError, KeyError):
                message = 'Request rejected'
            raise ClientError(f'HTTP {error.code}: {message}') from None
        except (URLError, TimeoutError, ValueError):
            raise ClientError('HSM API unavailable or returned invalid JSON') from None
