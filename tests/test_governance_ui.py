"""Checkpoint D: real REST client over an isolated mocked HTTP transport, never live HSM."""
import asyncio
import io
import json
from urllib.error import HTTPError
from urllib.parse import urlsplit, parse_qs

import pytest
from typer.testing import CliRunner
from textual.widgets import DataTable, Input, Static, TabbedContent
from hsm.cli import app as cli
from hsm.client import Client
from hsm.tui import Watch, Intervention

TASK = '00000000-0000-0000-0000-000000000001'
CHILD = '00000000-0000-0000-0000-000000000002'


@pytest.fixture
def transport(monkeypatch):
    class Transport:
        def __init__(self):
            self.calls = []
            self.denial = None
            self.readback_fail = False
            self.legacy = False
            self.item = dict(id=TASK, project_id='alpha', task_key='ROOT', title='Root',
                             state='in_progress', parent_id=None, depth=0, version=7,
                             owner_profile='worker', descendant_summary={'completed': 0, 'total': 1},
                             governance=dict(paused=False, effective_paused=False, remaining_children=0,
                                             ready_deliverables=1, required_deliverables=2, unresolved_exceptions=1))

        def open(self, req, timeout):
            assert req.get_header('Authorization') == 'Bearer scoped-test'
            parsed = urlsplit(req.full_url)
            path = parsed.path.removeprefix('/api/v1')
            params = parse_qs(parsed.query)
            body = json.loads(req.data) if req.data else None
            self.calls.append((req.method, path, params, body, req.get_header('Idempotency-key')))
            if req.method == 'POST':
                assert path in {f'/tasks/{TASK}/pause', f'/tasks/{TASK}/resume'}
                assert set(body) == {'expected_version', 'reason'}
                assert req.get_header('Idempotency-key')
                code = self.denial or (409 if body['expected_version'] != self.item['version'] else None)
                if code:
                    raise HTTPError(req.full_url, code, 'denied', {}, io.BytesIO(json.dumps({'error': {'message': 'denied or stale'}}).encode()))
                self.item['version'] += 1
                self.item['governance']['paused'] = path.endswith('/pause')
                self.item['governance']['effective_paused'] = path.endswith('/pause')
                # Deliberately misleading mutation response proves independent readback is used.
                data = {'version': -999}
            elif path == '/state':
                root = dict(self.item)
                if self.legacy:
                    root.pop('governance')
                child = {**root, 'id': CHILD, 'task_key': 'CHILD', 'parent_id': TASK, 'depth': 1}
                data = dict(project=params.get('project', [None])[0], revision=1, generated_at='test', warnings=[],
                            active_tasks=[root, child], task_hierarchy={'nodes': [root, child]},
                            agent_sessions=[], active_panes=[], pending_approvals=[], unresolved_incidents=[],
                            recent_events=[dict(id='event', cursor=1, event_type='contract.violation', actor='agent',
                                                payload={'code': 'child_budget'})])
            elif path == '/approvals':
                return io.BytesIO(json.dumps({'data': [], 'pagination': {'has_more': False}}).encode())
            elif path == '/tasks':
                return io.BytesIO(json.dumps({'data': [self.item], 'pagination': {'has_more': False}}).encode())
            elif path == '/checkpoints':
                return io.BytesIO(json.dumps({'data': [{'id': 'checkpoint', 'task_id': TASK,
                    'next_authorized_action': None, 'revision_ids': ['known-good']}],
                    'pagination': {'has_more': True}}).encode())
            elif path == f'/tasks/{TASK}/scope':
                data = dict(task=self.item, contracts=[{'id': 'contract'}], remaining_budget={'children': 0},
                            deliverables=[{'key': 'artifact'}], unresolved_exceptions=[{'id': 'exception'}],
                            runtime_control={'supported': False, 'acknowledged': False})
            elif path == f'/tasks/{TASK}':
                if self.readback_fail and any(c[0] == 'POST' for c in self.calls):
                    raise HTTPError(req.full_url, 503, 'offline', {}, io.BytesIO(b'{"error":{"message":"offline"}}'))
                data = self.item
            else:
                raise AssertionError(path)
            return io.BytesIO(json.dumps({'data': data}).encode())

    fake = Transport()
    monkeypatch.setattr('hsm.client.build_opener', lambda *args: fake)
    monkeypatch.setenv('HSM_TOKEN', 'scoped-test')
    monkeypatch.delenv('HSM_TOKEN_FILE', raising=False)
    monkeypatch.setenv('HSM_API_URL', 'http://127.0.0.1:1')
    return fake


@pytest.mark.parametrize('command', ['pause', 'resume'])
def test_cli_confirm_readback_and_version(transport, command):
    result = CliRunner().invoke(cli, [command, 'ROOT', '--project', 'alpha', '--reason', 'review scope',
                                    '--idempotency-key', 'fixed-key', '--json'], input='y\n')
    assert result.exit_code == 0, result.output
    assert 'version=7' in result.output and 'unsupported' in result.output
    assert '"version": 8' in result.output and '-999' not in result.output
    assert json.loads(result.stdout)['data']['version'] == 8
    post = next(c for c in transport.calls if c[0] == 'POST')
    assert post[3] == {'expected_version': 7, 'reason': 'review scope'}
    assert post[4] == 'fixed-key'
    assert transport.calls[-1][0:2] == ('GET', f'/tasks/{TASK}')


@pytest.mark.parametrize('extra,answer', [([], 'n\n'), (['--expected-version', '6'], 'y\n')])
def test_cli_cancel_or_stale_preflight_never_writes(transport, extra, answer):
    result = CliRunner().invoke(cli, ['pause', TASK, '--reason', 'reason', '--idempotency-key', 'key', *extra], input=answer)
    assert result.exit_code != 0
    assert not any(c[0] == 'POST' for c in transport.calls)


@pytest.mark.parametrize('status', [403, 409])
def test_cli_denial_readback_no_retry(transport, status):
    transport.denial = status
    result = CliRunner().invoke(cli, ['pause', TASK, '--reason', 'review', '--idempotency-key', 'key'], input='y\n')
    assert result.exit_code == 1 and f'HTTP {status}' in result.output
    assert len([c for c in transport.calls if c[0] == 'POST']) == 1
    assert transport.calls[-1][0:2] == ('GET', f'/tasks/{TASK}')


def test_cli_scope_and_checkpoint_bounded_exact_records(transport):
    runner = CliRunner()
    result = runner.invoke(cli, ['scope', 'ROOT', '--project', 'alpha', '--json'])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)['data']['runtime_control']['supported'] is False
    result = runner.invoke(cli, ['checkpoint', TASK, '--limit', '3', '--offset', '6'])
    assert result.exit_code == 0 and 'known-good' in result.output and 'More checkpoints' in result.output
    assert transport.calls[-1][2] == {'task_id': [TASK], 'limit': ['3'], 'offset': ['6']}
    assert all(c[0] == 'GET' for c in transport.calls)


def test_cli_readback_failure_is_unverified(transport):
    transport.readback_fail = True
    result = CliRunner().invoke(cli, ['pause', TASK, '--reason', 'review', '--idempotency-key', 'key'], input='y\n')
    assert result.exit_code == 1 and 'outcome unverified' in result.output
    assert len([c for c in transport.calls if c[0] == 'POST']) == 1


@pytest.mark.parametrize('flags', [[], ['--reason', 'review'], ['--idempotency-key', 'key'],
                                   ['--reason', ' ', '--idempotency-key', 'key']])
def test_cli_required_intervention_inputs(transport, flags):
    result = CliRunner().invoke(cli, ['pause', TASK, *flags], input='y\n')
    assert result.exit_code != 0
    assert not transport.calls


def test_tui_expand_scope_governance_events_and_legacy(transport):
    async def scenario():
        app = Watch(Client(), 'alpha')
        async with app.run_test(size=(160, 48)) as pilot:
            table = app.query_one('#tasks', DataTable)
            assert table.row_count == 2
            assert app.records['tasks'][TASK]['owner'] == 'worker'
            assert app.records['tasks'][TASK]['children_left'] == 0
            assert app.records['tasks'][TASK]['deliverables'] == '1/2'
            assert app.records['tasks'][TASK]['exceptions'] == 1
            table.focus()
            await pilot.press('space')
            assert table.row_count == 1
            await app.refresh_state()
            assert table.row_count == 1
            await pilot.click('#task-toggle')
            assert table.row_count == 2
            await pilot.click('#task-scope')
            assert 'runtime_control' in str(app.query_one('#details', Static).render())
            app.query_one(TabbedContent).active = app.query_one('#events').parent.id
            await pilot.pause()
            events = app.query_one('#events', DataTable)
            events.focus()
            await pilot.press('enter')
            assert 'child_budget' in str(app.query_one('#details', Static).render())
            transport.legacy = True
            await app.refresh_state()
            assert app.records['tasks'][TASK]['pause'] == 'unknown'
            await pilot.press('tab')  # Existing focus/tab navigation remains available.
    asyncio.run(scenario())


@pytest.mark.parametrize('operation', ['pause', 'resume'])
def test_tui_dialog_cancel_reason_confirm_and_readback(transport, operation):
    async def scenario():
        app = Watch(Client(), 'alpha')
        async with app.run_test(size=(160, 48)) as pilot:
            await pilot.click('#task-' + operation)
            assert isinstance(app.screen, Intervention)
            assert app.screen.task_record['version'] == 7
            before = len(transport.calls)
            await app.refresh_state()  # Timer refresh is deferred while confirmation is open.
            assert len(transport.calls) == before
            await pilot.click('#intervention-confirm')
            assert 'nonblank' in str(app.screen.query_one('#intervention-error', Static).render())
            await pilot.click('#intervention-cancel')
            assert not any(c[0] == 'POST' for c in transport.calls)
            await pilot.click('#task-' + operation)
            key = app.screen.key
            app.screen.query_one('#intervention-reason', Input).value = 'operator review'
            await pilot.click('#intervention-confirm')
            await pilot.pause()
            post_index = next(i for i, c in enumerate(transport.calls) if c[0] == 'POST')
            assert transport.calls[post_index][3] == {'expected_version': 7, 'reason': 'operator review'}
            assert transport.calls[post_index][4] == key
            assert transport.calls[post_index + 1][0:2] == ('GET', f'/tasks/{TASK}')
            details = str(app.query_one('#details', Static).render())
            assert 'independent readback' in details and '"version": 8' in details
            assert '-999' not in details
    asyncio.run(scenario())


@pytest.mark.parametrize('failure', ['denied', 'stale', 'readback'])
def test_tui_denied_stale_unverified_no_retry(transport, failure):
    async def scenario():
        app = Watch(Client(), 'alpha')
        async with app.run_test(size=(160, 48)) as pilot:
            await pilot.click('#task-pause')
            if failure == 'denied':
                transport.denial = 403
            elif failure == 'stale':
                transport.item['version'] = 8
            else:
                transport.readback_fail = True
            app.screen.query_one('#intervention-reason', Input).value = 'review'
            await pilot.click('#intervention-confirm')
            await pilot.pause()
            details = str(app.query_one('#details', Static).render())
            assert ('unverified' if failure == 'readback' else 'failed/denied') in details
            posts = [c for c in transport.calls if c[0] == 'POST']
            assert len(posts) == 1 and posts[0][3]['expected_version'] == 7
    asyncio.run(scenario())


def test_tui_discards_scope_and_history_on_filter_race(transport):
    class RacingClient(Client):
        change_filter = False
        def request(self, path, *args, **kwargs):
            result = super().request(path, *args, **kwargs)
            if self.change_filter and path in {'/approvals', f'/tasks/{TASK}/scope'}:
                app.project = 'beta'
            return result
    async def scenario():
        nonlocal app
        client = RacingClient()
        app = Watch(client, 'alpha')
        async with app.run_test(size=(160, 48)):
            before = str(app.query_one('#details', Static).render())
            client.change_filter = True
            await app.show_scope()
            assert str(app.query_one('#details', Static).render()) == before
            app.project = 'alpha'
            old_snapshot = app.snapshot
            await app.refresh_state()
            assert app.snapshot is old_snapshot
            assert app.approval_project == 'alpha'
    app = None
    asyncio.run(scenario())
