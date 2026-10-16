import asyncio
from hsm.client import ClientError
from hsm.tui import Watch
from textual.widgets import Button, DataTable, Input, Static, TabbedContent


class FakeClient:
    fail = False
    def __init__(self):
        self.calls = []

    def request(self, path, params=None):
        self.calls.append((path, params))
        if self.fail:
            raise ClientError('offline')
        if path == '/approvals':
            return {'data': [], 'pagination': {'offset': params['offset'], 'limit': params['limit'], 'has_more': False}}
        return {'data': {'project': params['project'], 'revision': 5, 'generated_at': '2026-10-16T00:00:00Z',
                         'warnings': ['Herdr unavailable'], 'active_tasks': [
                             {'id': 't', 'project_id': 'alpha', 'task_key': 'A-1', 'title': '[red]literal', 'state': 'blocked', 'version': 1}],
                         'agent_sessions': [], 'active_panes': [], 'pending_approvals': [],
                         'recent_events': [], 'unresolved_incidents': [], 'adapter_health': []}}


def test_watch_mocked_refresh_navigation_and_outage():
    async def scenario():
        client = FakeClient()
        app = Watch(client, 'alpha')
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app.query_one('#tasks', DataTable).row_count == 1
            table = app.query_one('#tasks', DataTable)
            table.focus()
            await pilot.press('enter')
            assert 'literal' in str(app.query_one('#details', Static).render())
            client.fail = True
            await app.refresh_state()
            assert app.query_one('#tasks', DataTable).row_count == 1
            assert 'unavailable' in str(app.query_one('#summary', Static).render())
            assert all(path in {'/state', '/approvals'} for path, _ in client.calls)
            await pilot.press('q')
    asyncio.run(scenario())


def test_watch_approval_history_pagination_and_failure():
    class HistoryClient(FakeClient):
        approval_fail = False
        def request(self, path, params=None):
            if path != '/approvals':
                return super().request(path, params)
            self.calls.append((path, params))
            if self.approval_fail:
                raise ClientError('approval history offline')
            offset, limit = params['offset'], params['limit']
            rows = [{'id': str(i), 'project_id': params['project'], 'operation': 'deploy',
                     'status': 'rejected', 'effective_status': 'rejected'} for i in range(53)]
            return {'data': rows[offset:offset + limit],
                    'pagination': {'offset': offset, 'limit': limit, 'has_more': offset + limit < len(rows)}}

    async def scenario():
        client = HistoryClient()
        app = Watch(client, 'alpha')
        async with app.run_test(size=(140, 45)) as pilot:
            await pilot.pause()
            app.query_one(TabbedContent).active = app.query_one('#approvals').parent.id
            await pilot.pause()
            table = app.query_one('#approvals', DataTable)
            assert table.row_count == 50
            assert 'More records' in str(app.query_one('#approval-page', Static).render())
            assert len([c for c in client.calls if c[0] == '/approvals']) == 1
            client.approval_fail = True
            await pilot.click('#approval-next')
            await pilot.pause()
            assert app.approval_offset == 0 and table.row_count == 50
            assert 'unavailable' in str(app.query_one('#summary', Static).render())
            assert '0' in app.records['approvals']
            client.approval_fail = False
            await pilot.pause(0.4)  # Let the button's click debounce expire before retrying.
            await pilot.click('#approval-next')
            await pilot.pause()
            assert app.approval_offset == 50 and table.row_count == 3
            assert set(app.records['approvals']) == {'50', '51', '52'}
            assert app.query_one('#approval-next', Button).disabled
            assert 'End of history' in str(app.query_one('#approval-page', Static).render())
            before = len(client.calls)
            await app.refresh_state()
            assert len(client.calls) == before + 2  # state + current page only
            assert client.calls[-1] == ('/approvals', {'project': 'alpha', 'limit': 50, 'offset': 50})
            await pilot.click('#approval-previous')
            await pilot.pause()
            assert app.approval_offset == 0 and table.row_count == 50
            await pilot.click('#approval-next')
            await pilot.pause()
            field = app.query_one('#project', Input)
            field.value = 'beta'
            field.focus()
            client.approval_fail = True
            await pilot.press('enter')
            await pilot.pause()
            assert app.approval_offset == 50 and app.approval_project == 'alpha'
            assert table.row_count == 3
            assert 'Project: alpha' in str(app.query_one('#approval-page', Static).render())
            client.approval_fail = False
            await app.refresh_state()
            assert client.calls[-1] == ('/approvals', {'project': 'beta', 'limit': 50, 'offset': 0})
            assert all(row['project_id'] == 'beta' for row in app.records['approvals'].values())
            field.value = ''
            await pilot.press('enter')
            await pilot.pause()
            assert client.calls[-1][1]['project'] is None
    asyncio.run(scenario())


def test_watch_persisted_approval_outcomes_from_authenticated_api(env, make_task):
    from datetime import datetime, timedelta, timezone
    from urllib.parse import urlencode
    from hsm.database import transaction
    from hsm.models import Approval

    task = make_task()
    call = env[2]
    expected = {}
    for outcome in ('pending', 'approved', 'rejected', 'expired', 'consumed'):
        descriptor = {'task_id': task['id'], 'operation': 'test', 'target': outcome,
                      'action': 'simulate', 'artifact': 'sha256:test'}
        response = call('POST', '/approvals', {**descriptor, 'project_id': 'alpha',
                        'expires_at': (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()},
                        token='a', key=outcome)
        assert response.status_code == 201
        item = response.json['data']
        expected[item['id']] = outcome
        if outcome in {'approved', 'rejected', 'consumed'}:
            assert call('POST', '/approvals/' + item['id'] + '/resolve', {
                'expected_version': 1, 'decision': 'rejected' if outcome == 'rejected' else 'approved'}).status_code == 200
        if outcome == 'consumed':
            assert call('POST', '/approvals/' + item['id'] + '/consume', {
                **descriptor, 'expected_version': 2}, token='a').status_code == 200
        if outcome == 'expired':
            with transaction(env[1].extensions['hsm_engine'], True) as db:
                db.get(Approval, item['id']).expires_at = '2000-01-01T00:00:00.000000Z'

    class ApiClient:
        def request(self, path, params=None):
            response = call('GET', path + '?' + urlencode({k: v for k, v in params.items() if v is not None}))
            assert response.status_code == 200
            return response.json

    async def scenario():
        app = Watch(ApiClient(), 'alpha')
        async with app.run_test(size=(140, 45)) as pilot:
            await pilot.pause()
            assert {k: v['outcome'] for k, v in app.records['approvals'].items()} == expected
            assert len(app.snapshot['outstanding_approvals']) < len(expected)
            app.query_one(TabbedContent).active = app.query_one('#approvals').parent.id
            table = app.query_one('#approvals', DataTable)
            await pilot.pause()
            assert {str(table.get_row_at(i)[3]) for i in range(5)} == set(expected.values())
            consumed_id = next(k for k, v in expected.items() if v == 'consumed')
            assert app.records['approvals'][consumed_id]['status'] == 'approved'
            table.focus()
            await pilot.press('enter')
            assert 'effective_status' in str(app.query_one('#details', Static).render())
            await app.refresh_state()
            assert table.row_count == 5
    asyncio.run(scenario())


def test_watch_displays_parent_child_progress_and_owner(env, make_task):
    parent = make_task(task_key='PARENT')
    owner = env[2]('POST', '/agent-sessions', {'project_id': 'alpha', 'profile': 'schema'}).json['data']
    child = make_task(task_key='CHILD', parent_id=parent['id'], owner_session_id=owner['id'])
    snapshot = env[2]('GET', '/state?project=alpha').json
    class SnapshotClient:
        def request(self, path, params=None):
            if path == '/approvals':
                return {'data': [], 'pagination': {'offset': 0, 'limit': 50, 'has_more': False}}
            return snapshot
    async def scenario():
        app = Watch(SnapshotClient(), 'alpha')
        async with app.run_test() as pilot:
            await pilot.pause()
            table = app.query_one('#tasks', DataTable)
            assert table.row_count == 2
            assert str(table.get_row_at(0)[1]) == 'PARENT'
            assert str(table.get_row_at(0)[6]) == '0/1'
            assert str(table.get_row_at(1)[1]) == '  CHILD'
            assert str(table.get_row_at(1)[3]) == 'PARENT'
            assert str(table.get_row_at(1)[5]) == 'schema'
            table.focus()
            table.move_cursor(row=1)
            await pilot.press('enter')
            assert child['owner_session_id'] in str(app.query_one('#details', Static).render())
            await pilot.press('q')
    asyncio.run(scenario())
