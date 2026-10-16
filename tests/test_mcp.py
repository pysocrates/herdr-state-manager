import asyncio
import pytest
pytest.importorskip('mcp')
from hsm.mcp.server import build_server


class FlaskTransport:
    def __init__(self, flask_app, token):
        self.app, self.token = flask_app, token
    def request(self, path, params=None, method='GET', data=None, idempotency_key=None):
        headers = {'Authorization': 'Bearer ' + self.token * 32}
        if idempotency_key:
            headers['Idempotency-Key'] = idempotency_key
        response = self.app.test_client().open('/api/v1' + path, method=method, query_string=params, json=data, headers=headers)
        if response.status_code >= 400:
            raise ValueError(response.json['error']['code'])
        return response.json


def test_mcp_uses_authenticated_api_and_no_operator_tools(env):
    async def scenario():
        server = build_server(FlaskTransport(env[1], 'a'))
        names = {t.name for t in await server.list_tools()}
        assert 'hsm_task_create' in names
        assert not any('resolve' in n or 'consume' in n or 'approve' in n for n in names)
        await server.call_tool('hsm_task_create', {'request': {'project_id': 'alpha', 'task_key': 'MCP-1', 'title': 'MCP', 'objective': 'recover'}, 'idempotency_key': 'mcp-create'})
        assert env[2]('GET', '/tasks?project=alpha').json['data'][0]['task_key'] == 'MCP-1'
        with pytest.raises(Exception):
            await server.call_tool('hsm_task_create', {'request': {'project_id': 'beta', 'task_key': 'MCP-2', 'title': 'MCP', 'objective': 'scope'}, 'idempotency_key': 'mcp-forbidden'})
    asyncio.run(scenario())
