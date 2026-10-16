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
