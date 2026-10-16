import asyncio
from hsm.client import ClientError
from hsm.tui import Watch
from textual.widgets import DataTable, Static


class FakeClient:
    fail = False
    calls = []
    def request(self, path, params=None):
        self.calls.append((path, params))
        if self.fail:
            raise ClientError('offline')
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
            assert all(path == '/state' for path, _ in client.calls)
            await pilot.press('q')
    asyncio.run(scenario())


def test_watch_displays_parent_child_progress_and_owner(env, make_task):
    parent = make_task(task_key='PARENT')
    owner = env[2]('POST', '/agent-sessions', {'project_id': 'alpha', 'profile': 'schema'}).json['data']
    child = make_task(task_key='CHILD', parent_id=parent['id'], owner_session_id=owner['id'])
    snapshot = env[2]('GET', '/state?project=alpha').json
    class SnapshotClient:
        def request(self, *args, **kwargs):
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
