import asyncio
from textual.widgets import Select, DataTable, Static
from hsm.tui import Watch
from test_tui import FakeClient


def test_native_history_source_outcomes_and_refresh():
    class NativeClient(FakeClient):
        def request(self, path, params=None):
            if path != '/approval-observations':
                return super().request(path, params)
            self.calls.append((path, params))
            return {'data': [
                {'id': 'pre', 'project_id': 'alpha', 'surface': 'cli', 'choice': None,
                 'correlation': 'missing_request_id'},
                {'id': 'post', 'project_id': 'alpha', 'surface': 'cli', 'choice': 'deny',
                 'correlation': 'missing_request_id'},
                {'id': 'smart', 'project_id': 'alpha', 'surface': 'smart', 'choice': 'smart_approve',
                 'correlation': 'missing_request_id'}],
                'pagination': {'offset': 0, 'limit': 50, 'has_more': False}}
    async def scenario():
        client = NativeClient()
        app = Watch(client, 'alpha')
        async with app.run_test(size=(140, 45)) as pilot:
            await pilot.pause()
            app.query_one('#approval-source', Select).value = 'native'
            await pilot.pause()
            assert app.query_one('#approvals', DataTable).row_count == 3
            assert app.records['approvals']['pre']['outcome'] == 'unpaired request'
            assert app.records['approvals']['post']['outcome'] == 'deny'
            assert app.records['approvals']['smart']['outcome'] == 'smart_approve'
            await app.refresh_state()
            assert app.query_one('#approvals', DataTable).row_count == 3
            assert 'native' in str(app.query_one('#approval-page', Static).render())
            assert client.calls[-1] == ('/approval-observations', {'project': 'alpha', 'limit': 50, 'offset': 0})
            app.query_one('#approval-source', Select).value = 'hsm'
            await pilot.pause()
            assert app.query_one('#approvals', DataTable).row_count == 0
    asyncio.run(scenario())
