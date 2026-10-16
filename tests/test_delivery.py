"""Real HTTP/Gunicorn and optional stdio boundary verification; temporary state only."""
import asyncio
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from uuid import uuid4
import pytest
from typer.testing import CliRunner
from hsm.client import Client, ClientError
from hsm.cli import app as cli


@pytest.fixture
def http_api(env):
    cfg = env[0]
    server_env = {**os.environ, 'HSM_DATABASE_URL': cfg.database_url,
                  'HSM_CREDENTIALS': json.dumps(cfg.credentials)}
    server_env.pop('HSM_CREDENTIALS_FILE', None)
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    sock.listen(128)
    endpoint = 'http://127.0.0.1:' + str(sock.getsockname()[1])
    process = subprocess.Popen([str(Path(sys.executable).parent / 'gunicorn'), '--bind', f'fd://{sock.fileno()}',
                                '--workers', '2', 'hsm.app:create_app()'], env=server_env, pass_fds=(sock.fileno(),),
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        client = Client(endpoint, 'o' * 32)
        for _ in range(100):
            if process.poll() is not None:
                raise AssertionError('Gunicorn exited unexpectedly')
            try:
                if client.request('/health')['data']['status'] == 'ready':
                    break
            except ClientError:
                time.sleep(.05)
        else:
            raise AssertionError('Gunicorn did not become ready')
        yield endpoint
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)
        sock.close()


def test_real_http_recovery_approval_and_cli(http_api, monkeypatch):
    agent, operator = Client(http_api, 'a' * 32), Client(http_api, 'o' * 32)
    session = agent.request('/agent-sessions', method='POST', data={'project_id': 'alpha', 'profile': 'worker'})['data']
    task = agent.request('/tasks', method='POST', idempotency_key='http-task', data={'project_id': 'alpha',
        'task_key': 'HTTP-1', 'title': 'HTTP', 'objective': 'Recover', 'owner_session_id': session['id']})['data']
    agent.request('/panes', method='POST', data={'project_id': 'alpha', 'external_pane_id': 'simulated-http', 'task_id': task['id'], 'agent_session_id': session['id']})
    for state in ['planning', 'running', 'awaiting_approval']:
        task = agent.request('/tasks/' + task['id'] + '/transition', method='POST', idempotency_key=state,
                             data={'expected_version': task['version'], 'state': state})['data']
    descriptor = {'task_id': task['id'], 'operation': 'test', 'target': 'no-execution', 'action': 'simulate grant', 'artifact': 'sha256:test'}
    approval = agent.request('/approvals', method='POST', idempotency_key='http-approval', data={
        **descriptor, 'project_id': 'alpha', 'expires_at': (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()})['data']
    assert operator.request('/state')['data']['pending_approvals'][0]['id'] == approval['id']
    operator.request('/approvals/' + approval['id'] + '/resolve', method='POST', data={'expected_version': 1, 'decision': 'approved'})
    assert operator.request('/state')['data']['outstanding_approvals'][0]['id'] == approval['id']
    agent.request('/approvals/' + approval['id'] + '/consume', method='POST', data={**descriptor, 'expected_version': 2})
    fresh = Client(http_api, 'a' * 32).request('/state')['data']
    assert fresh['active_tasks'][0]['state'] == 'awaiting_approval'
    assert fresh['agent_sessions'][0]['id'] == session['id']
    monkeypatch.setenv('HSM_API_URL', http_api)
    monkeypatch.setenv('HSM_TOKEN', 'a' * 32)
    monkeypatch.delenv('HSM_TOKEN_FILE', raising=False)
    result = CliRunner().invoke(cli, ['status', '--json'])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)['data']['active_tasks'][0]['id'] == task['id']


def test_optional_real_stdio_mcp(http_api):
    pytest.importorskip('mcp')
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    async def scenario():
        params = StdioServerParameters(command=sys.executable, args=['-c', 'from hsm.mcp.server import main; main()'],
                                      env={'HSM_API_URL': http_api, 'HSM_TOKEN': 'a' * 32})
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = await session.list_tools()
                assert 'hsm_state_snapshot' in {t.name for t in tools.tools}
                result = await session.call_tool('hsm_state_snapshot', {'project': 'alpha'})
                assert not result.isError
                assert 'alpha' in str(result.content)
    asyncio.run(scenario())


def test_cli_approval_requires_confirmation(env, make_task, monkeypatch):
    from hsm.client import Client
    from test_approvals import requested
    item, _ = requested(env, make_task)
    writes = []
    def request(self, path, params=None, method='GET', data=None, **kwargs):
        if method != 'GET':
            writes.append(data)
            return {'data': {**item, 'status': data['decision']}}
        return {'data': {**item, 'effective_status': 'pending'}}
    monkeypatch.setattr(Client, 'request', request)
    runner = CliRunner()
    rejected = runner.invoke(cli, ['approve', item['id']], input='n\n')
    assert rejected.exit_code != 0 and writes == []
    approved = runner.invoke(cli, ['approve', item['id']], input='y\n')
    assert approved.exit_code == 0
    assert item['action'] in approved.output
    assert writes[0]['decision'] == 'approved'
