from uuid import uuid4
from pathlib import Path
import importlib.util
import json
from hsm.app import create_app


def payload(**kw):
    return dict(project_id='alpha', event_id=str(uuid4()), phase='pre', surface='cli', **kw)


def enable(env):
    env[0].credentials[1]['actions'].append('approvals:observe')
    return env[2]


def test_scopes_and_schema(env):
    call = env[2]
    p = payload()
    assert call('POST', '/approval-observations', p, token='a').status_code == 403
    enable(env)
    assert call('POST', '/approval-observations', dict(p, project_id='beta'), token='a').status_code == 403
    assert call('POST', '/approval-observations', dict(p, command='SECRET'), token='a').status_code == 422
    assert call('POST', '/approval-observations', dict(p, phase='post'), token='a').status_code == 422
    assert call('POST', '/approval-observations', dict(p, native_request_id='raw secret'), token='a').status_code == 422
    assert env[1].test_client().get('/api/v1/approval-observations').status_code == 401
    assert call('GET', '/approval-observations?project=beta', token='a').status_code == 403


def test_correlation_final_replay_restart(env):
    call = enable(env)
    p = payload(native_request_id='a'*64)
    a = call('POST', '/approval-observations', p, token='a')
    assert a.status_code == 200, a.json
    assert a.json['data']['status'] == 'pending'
    assert call('POST', '/approval-observations', p, token='a').json == a.json
    post = dict(p, event_id=str(uuid4()), phase='post', choice='once')
    result = call('POST', '/approval-observations', post, token='a')
    assert result.json['data']['id'] == a.json['data']['id']
    assert result.json['data']['status'] == 'outcome'
    bad = dict(post, event_id=str(uuid4()), choice='deny')
    assert call('POST', '/approval-observations', bad, token='a').status_code == 409
    assert call('POST', '/approval-observations', dict(p, surface='smart'), token='a').status_code == 409
    restarted = create_app(env[0])
    response = restarted.test_client().post('/api/v1/approval-observations', json=post,
        headers={'Authorization': 'Bearer ' + 'a'*32})
    assert response.json == result.json
    restarted.extensions['hsm_engine'].dispose()
    assert call('GET', '/approvals').json['data'] == []
    assert call('GET', '/health/ready').json['data']['schema_revision'] == '0005'


def test_post_without_pre_and_missing_correlation(env):
    call = enable(env)
    post = dict(payload(native_request_id='b'*64), phase='post', choice='timeout')
    first = call('POST', '/approval-observations', post, token='a').json['data']
    assert first['requested_at'] is None and first['status'] == 'outcome'
    late = dict(post, phase='pre', choice=None, event_id=str(uuid4()))
    result = call('POST', '/approval-observations', late, token='a').json['data']
    assert result['status'] == 'outcome' and result['requested_at']
    for phase in ['pre', 'post']:
        p = dict(payload(), phase=phase, choice='deny' if phase == 'post' else None)
        assert call('POST', '/approval-observations', p, token='a').json['data']['correlation'] == 'missing_request_id'
    page = call('GET', '/approval-observations?limit=2', token='a').json
    assert len(page['data']) == 2 and page['pagination']['has_more']
    assert len(call('GET', '/approval-observations?offset=2', token='a').json['data']) == 1
    assert call('GET', '/approval-observations?status=bad', token='a').status_code == 422
    assert call('GET', '/approval-observations?limit=201', token='a').status_code == 422
    assert call('GET', '/approval-observations', token='b').json['data'] == []


def plugin():
    path = Path(__file__).parents[1] / 'deployment/hermes-approval-observer/__init__.py'
    spec = importlib.util.spec_from_file_location('observer_plugin', path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_plugin_hooks_bounded_private_and_uncorrelated(tmp_path, monkeypatch, env):
    module = plugin()
    token = tmp_path / 'token'
    token.write_text('a'*32)
    monkeypatch.setenv('HSM_OBSERVER_TOKEN_FILE', str(token))
    monkeypatch.setenv('HSM_OBSERVER_PROJECT', 'alpha')
    observer = module.Observer('alpha', 'http://127.0.0.1:8787', 'a'*32, capacity=2, start=False)
    monkeypatch.setattr(module, 'Observer', lambda *args: observer)
    class Hooks:
        def __init__(self): self.hooks = {}
        def register_hook(self, name, callback): self.hooks[name] = callback
    ctx = Hooks()
    module.register(ctx)
    kwargs = dict(command='SECRET COMMAND', description='SECRET', session_key='PRIVATE',
        pattern_key='SECRET', pattern_keys=['SECRET'], surface='cli', turn_id='turn', tool_call_id='tool')
    assert ctx.hooks['pre_approval_request'](**kwargs) is None
    assert ctx.hooks['post_approval_response'](**kwargs, choice='deny') is None
    assert observer.pre(**kwargs) is None
    assert observer.dropped == 1
    call = enable(env)
    ids = []
    while not observer.queue.empty():
        p = observer.queue.get_nowait()
        assert 'SECRET' not in json.dumps(p) and 'PRIVATE' not in json.dumps(p)
        assert 'native_request_id' not in p
        result = call('POST', '/approval-observations', p, token='a')
        assert result.status_code == 200
        ids.append(result.json['data']['id'])
    assert ids[0] != ids[1]


def test_concurrent_final_outcomes(env):
    from concurrent.futures import ThreadPoolExecutor
    enable(env)
    def post(choice):
        with env[1].test_client() as client:
            p = dict(payload(native_request_id='c'*64), phase='post', choice=choice)
            return client.post('/api/v1/approval-observations', json=p,
                headers={'Authorization': 'Bearer ' + 'a'*32}).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(post, ['once', 'deny'])) == [200, 409]


def test_plugin_http_delivery(env):
    from werkzeug.serving import make_server
    from threading import Thread
    enable(env)
    server = make_server('127.0.0.1', 0, env[1])
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        observer = plugin().Observer('alpha', f'http://127.0.0.1:{server.server_port}', 'a'*32, start=False)
        observer.post(surface='cli', choice='deny', command='DO NOT STORE')
        observer.send(observer.queue.get_nowait())
        rows = env[2]('GET', '/approval-observations').json['data']
        assert len(rows) == 1 and rows[0]['choice'] == 'deny'
        assert 'DO NOT STORE' not in json.dumps(rows)
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


def test_plugin_retry_identity_and_failure(monkeypatch):
    module = plugin()
    monkeypatch.setattr(module.time, 'sleep', lambda _: None)
    received = []
    def fail(p):
        received.append(p.copy())
        raise OSError('SECRET')
    observer = module.Observer('alpha', 'http://localhost:8787', 'a'*32, sender=fail, start=False)
    observer.pre(surface='cli')
    p = observer.queue.get_nowait()
    observer.deliver_one(p)
    assert len(received) == 3 and all(r == p for r in received)
    assert observer.dropped == 1
