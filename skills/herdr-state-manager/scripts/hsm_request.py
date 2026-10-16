#!/usr/bin/env python3
"""Small agent-side HSM REST client; stdlib only, no DB or execution access."""
import argparse
import json
import os
from pathlib import Path
import re
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

# Agent operations only. Server remains responsible for project/action authorization.
UUID = r'[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}'
ALLOWED = {
    'GET': rf'/(?:projects|tasks(?:/{UUID}(?:/events)?)?|agent-sessions(?:/{UUID})?|panes(?:/{UUID})?|approvals(?:/{UUID})?|events|incidents|state(?:/projects/[a-zA-Z0-9_-]{{1,80}})?|health(?:/(?:live|ready))?)',
    'POST': rf'/(?:tasks(?:/{UUID}/transition)?|agent-sessions(?:/{UUID}/heartbeat)?|panes(?:/{UUID}/heartbeat)?|approvals(?:/{UUID}/consume)?)',
    'PATCH': rf'/(?:tasks|agent-sessions|panes)/{UUID}',
}


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('method', choices=list(ALLOWED))
    parser.add_argument('path', help='API resource path, e.g. /tasks (without /api/v1)')
    parser.add_argument('--query', action='append', default=[], metavar='KEY=VALUE')
    parser.add_argument('--data-file', help='JSON object file, or - to read stdin')
    parser.add_argument('--idempotency-key', help='Preserve this key and payload across ambiguous retries')
    args = parser.parse_args(argv)
    if not re.fullmatch(ALLOWED[args.method], args.path):
        parser.error('Unsupported agent endpoint; operator/admin routes are not exposed')
    if args.method != 'GET' and not args.data_file:
        parser.error('Writes require --data-file FILE or --data-file -')
    if args.method == 'GET' and args.data_file:
        parser.error('GET does not accept a request body')
    if args.method == 'POST' and (args.path in {'/tasks', '/approvals'} or args.path.endswith('/transition')) and not args.idempotency_key:
        parser.error('This operation requires --idempotency-key')
    if args.idempotency_key and not re.fullmatch(r'[!-~]{1,128}', args.idempotency_key):
        parser.error('Idempotency key must be 1..128 non-space ASCII characters')
    try:
        endpoint = os.getenv('HSM_API_URL', 'http://127.0.0.1:8787').rstrip('/')
        parsed = urlsplit(endpoint)
        if (parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username
                or parsed.password or parsed.query or parsed.fragment or parsed.path not in {'', '/'}):
            parser.error('HSM_API_URL must be an HTTP(S) server origin, without credentials or API path')
        if parsed.scheme == 'http' and parsed.hostname not in {'localhost', '127.0.0.1', '::1'}:
            parser.error('Remote HSM endpoints require HTTPS (or use SSH localhost forwarding)')
        token_file = os.getenv('HSM_TOKEN_FILE')
        token = Path(token_file).read_text().strip() if token_file else os.getenv('HSM_TOKEN', '').strip()
        if not token:
            parser.error('Configure a scoped agent credential with HSM_TOKEN_FILE or HSM_TOKEN')
        if any(ord(c) < 33 or ord(c) > 126 for c in token):
            parser.error('Configured credential has invalid header characters')
        query = {}
        for item in args.query:
            key, separator, value = item.partition('=')
            if not key or not separator or key in query:
                parser.error('--query requires unique KEY=VALUE pairs')
            query[key] = value
        body = None
        if args.data_file:
            if args.data_file == '-':
                raw = sys.stdin.buffer.read(65537)
            else:
                with Path(args.data_file).open('rb') as stream:
                    raw = stream.read(65537)
            if len(raw) > 65536:
                parser.error('Request exceeds 64 KiB')
            data = json.loads(raw)
            if not isinstance(data, dict):
                parser.error('Request must be a JSON object')
            body = json.dumps(data).encode()
        headers = {'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'}
        if args.idempotency_key:
            headers['Idempotency-Key'] = args.idempotency_key
        request = Request(endpoint + '/api/v1' + args.path + ('?' + urlencode(query) if query else ''),
                          data=body, headers=headers, method=args.method)
        with build_opener(NoRedirect()).open(request, timeout=10) as response:
            result = json.load(response)
        print(json.dumps(result, sort_keys=True))
        return 0
    except HTTPError as error:
        # Avoid echoing arbitrary proxy error bodies, which may contain credentials.
        print(json.dumps({'error': {'code': 'http_error', 'status': error.code,
                                   'message': 'HSM rejected the request; inspect current state and API contract.'}}), file=sys.stderr)
        return 1
    except (OSError, URLError, TimeoutError, ValueError):
        print(json.dumps({'error': {'code': 'client_error', 'message': 'Check API connectivity, private credential file, and JSON input.'}}), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
