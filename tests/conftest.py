from uuid import uuid4
import pytest
from hsm.app import create_app
from hsm.config import Config
from hsm.database import migrate


@pytest.fixture
def env(tmp_path):
    cfg = Config(database_url=f'sqlite:///{tmp_path}/state.db', credentials=[
        {'token': 'o' * 32, 'identity': 'operator', 'role': 'operator', 'projects': ['*'], 'actions': ['*']},
        {'token': 'a' * 32, 'identity': 'worker-a', 'role': 'agent', 'projects': ['alpha'],
         'actions': ['read', 'tasks:write', 'runtime:write', 'approvals:request', 'approvals:consume']},
        {'token': 'b' * 32, 'identity': 'worker-b', 'role': 'agent', 'projects': ['beta'],
         'actions': ['read', 'tasks:write', 'runtime:write']},
    ])
    migrate(cfg.database_url)
    app = create_app(cfg)
    app.config['TESTING'] = True
    client = app.test_client()
    def call(method, path, data=None, token='o', key=None):
        headers = {'Authorization': 'Bearer ' + token * 32}
        if key is not None:
            headers['Idempotency-Key'] = key
        return client.open('/api/v1' + path, method=method, json=data, headers=headers)
    for project in ['alpha', 'beta']:
        assert call('POST', '/projects', {'id': project, 'name': project}).status_code == 201
    yield cfg, app, call
    app.extensions['hsm_engine'].dispose()


@pytest.fixture
def make_task(env):
    call = env[2]
    def create(project='alpha', **extra):
        result = call('POST', '/tasks', {'project_id': project, 'task_key': str(uuid4()),
                      'title': 'Task', 'objective': 'Recover this work', **extra}, key=str(uuid4()))
        assert result.status_code == 201, result.json
        return result.json['data']
    return create
