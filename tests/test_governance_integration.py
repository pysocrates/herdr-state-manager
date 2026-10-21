"""Checkpoint D joins real authenticated backend and operator clients in temporary state."""
import asyncio
from uuid import uuid4
from typer.testing import CliRunner
from textual.widgets import DataTable, Input
from hsm.client import Client, ClientError
from hsm.cli import app as cli
from hsm.tui import Watch


class Transport:
    def __init__(self, app, token='o'):
        self.app, self.token = app, token

    def request(self, path, params=None, method='GET', data=None, idempotency_key=None):
        headers = {'Authorization': 'Bearer ' + self.token * 32}
        if idempotency_key:
            headers['Idempotency-Key'] = idempotency_key
        with self.app.test_client() as client:
            response = client.open('/api/v1' + path, method=method, json=data,
                query_string={k: v for k, v in (params or {}).items() if v is not None}, headers=headers)
        if response.status_code >= 400:
            raise ClientError(str(response.json))
        return response.json


def test_cli_real_pause_resume_scope(env, make_task, monkeypatch):
    task = make_task()
    transport = Transport(env[1])
    monkeypatch.setattr(Client, 'request', lambda self, *a, **kw: transport.request(*a, **kw))
    runner = CliRunner()
    for command, paused in [('pause', True), ('resume', False)]:
        result = runner.invoke(cli, [command, task['id'], '--reason', 'Reviewed scope',
            '--idempotency-key', str(uuid4())], input='y\n')
        assert result.exit_code == 0, result.output
        current = env[2]('GET', '/tasks/' + task['id']).json['data']
        assert current['governance']['paused'] is paused
        assert current['state'] == 'queued'
    assert runner.invoke(cli, ['scope', task['id'], '--json']).exit_code == 0
    assert runner.invoke(cli, ['checkpoint', task['id'], '--json']).exit_code == 0


def test_textual_real_backend_pause_and_ancestor_containment(env, make_task):
    root = make_task(task_key='A-ROOT')
    child = make_task(task_key='B-CHILD', parent_id=root['id'])
    ui = Watch(client=Transport(env[1]), project='alpha')

    async def scenario():
        async with ui.run_test(size=(180, 55)) as pilot:
            await pilot.pause()
            table = ui.query_one('#tasks', DataTable)
            assert table.row_count == 2
            table.move_cursor(row=0)
            await pilot.click('#task-pause')
            await pilot.pause()
            ui.screen.query_one('#intervention-reason', Input).value = 'Verify inherited governance'
            await pilot.click('#intervention-confirm')
            await pilot.pause()
            root_now = env[2]('GET', '/tasks/' + root['id']).json['data']
            child_now = env[2]('GET', '/tasks/' + child['id']).json['data']
            assert root_now['governance']['paused']
            assert child_now['governance']['effective_paused']
            assert ui.records['tasks'][child['id']]['governance']['effective_paused']
            denied = env[2]('POST', '/tasks/' + child['id'] + '/transition',
                {'expected_version': 1, 'state': 'planning'}, token='a', key=str(uuid4()))
            assert denied.status_code == 409
            assert denied.json['error']['code'] == 'governance_paused'
            await pilot.click('#task-resume')
            await pilot.pause()
            ui.screen.query_one('#intervention-reason', Input).value = 'Reviewed release'
            await pilot.click('#intervention-confirm')
            await pilot.pause()
            assert not env[2]('GET', '/tasks/' + child['id']).json['data']['governance']['effective_paused']
    asyncio.run(scenario())
