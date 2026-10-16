from urllib.parse import quote
from mcp.server.fastmcp import FastMCP
from hsm.client import Client
from hsm.schemas import TaskCreate, Transition, PaneCreate, SessionCreate, ApprovalCreate


def build_server(client=None):
    api = client or Client()
    server = FastMCP('HSM', instructions='Persistent state only. No execution tools. Project grants enforced by the REST service.')

    @server.tool()
    def hsm_task_create(request: TaskCreate, idempotency_key: str) -> dict:
        return api.request('/tasks', method='POST', data=request.model_dump(), idempotency_key=idempotency_key)

    @server.tool()
    def hsm_task_get(task_id: str) -> dict:
        return api.request('/tasks/' + quote(task_id, safe=''))

    @server.tool()
    def hsm_task_tree(task_id: str) -> dict:
        """Inspect a task and all descendants, with ownership and progress counts."""
        return api.request('/tasks/' + quote(task_id, safe='') + '/tree')

    @server.tool()
    def hsm_task_transition(task_id: str, request: Transition, idempotency_key: str) -> dict:
        return api.request('/tasks/' + quote(task_id, safe='') + '/transition', method='POST',
                           data=request.model_dump(), idempotency_key=idempotency_key)

    @server.tool()
    def hsm_pane_register(request: PaneCreate) -> dict:
        return api.request('/panes', method='POST', data=request.model_dump())

    @server.tool()
    def hsm_session_register(request: SessionCreate) -> dict:
        return api.request('/agent-sessions', method='POST', data=request.model_dump())

    @server.tool()
    def hsm_pane_heartbeat(pane_id: str) -> dict:
        return api.request('/panes/' + quote(pane_id, safe='') + '/heartbeat', method='POST', data={})

    @server.tool()
    def hsm_session_heartbeat(session_id: str) -> dict:
        return api.request('/agent-sessions/' + quote(session_id, safe='') + '/heartbeat', method='POST', data={})

    @server.tool()
    def hsm_approval_request(request: ApprovalCreate, idempotency_key: str) -> dict:
        return api.request('/approvals', method='POST', data=request.model_dump(), idempotency_key=idempotency_key)

    @server.tool()
    def hsm_approval_get(approval_id: str) -> dict:
        return api.request('/approvals/' + quote(approval_id, safe=''))

    @server.tool()
    def hsm_state_snapshot(project: str | None = None) -> dict:
        return api.request('/state', {'project': project})

    return server


def main():
    build_server().run(transport='stdio')
