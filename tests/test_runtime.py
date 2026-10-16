import json
import sqlite3
from unittest.mock import patch
import pytest
from sqlalchemy import select
from hsm.adapters.base import NoopAdapter, Observations, Observation
from hsm.adapters.herdr import HerdrAdapter
from hsm.adapters.hermes import HermesAdapter, COLUMNS
from hsm.database import transaction
from hsm.models import Event, Incident
from hsm.reconciliation import reconcile, reconcile_lock
from hsm.services import Problem


class FakeAdapter:
    def __init__(self, available=True, resources=None):
        self.available, self.resources = available, resources or {}
    def observe(self, identifiers):
        return Observations(self.available, self.resources)


def test_incident_dedup_resolution_and_outage(env, make_task):
    cfg, app, call = env
    task = make_task()
    pane = call('POST', '/panes', {'project_id': 'alpha', 'external_pane_id': 'pane-1', 'task_id': task['id']}).json['data']
    adapter = FakeAdapter()
    first = reconcile(app.extensions['hsm_engine'], cfg, adapter, FakeAdapter())
    second = reconcile(app.extensions['hsm_engine'], cfg, adapter, FakeAdapter())
    assert first['incidents_opened'] == 1
    assert second['incidents_opened'] == 0
    incidents = call('GET', '/incidents?project=alpha').json['data']
    assert len(incidents) == 1 and incidents[0]['status'] == 'open'
    outage = FakeAdapter(False)
    reconcile(app.extensions['hsm_engine'], cfg, outage, outage)
    pane_state = call('GET', '/panes/' + pane['id']).json['data']
    assert pane_state['runtime_status'] == 'unavailable'
    assert call('GET', '/tasks/' + task['id']).json['data']['state'] == 'queued'
    assert next(i for i in call('GET', '/incidents?project=alpha').json['data'] if i['incident_type'] == 'pane_missing')['status'] == 'open'
    adapter.resources = {'pane-1': Observation('working')}
    resolved = reconcile(app.extensions['hsm_engine'], cfg, adapter, FakeAdapter())
    assert resolved['incidents_resolved'] >= 1
    assert reconcile(app.extensions['hsm_engine'], cfg, adapter, FakeAdapter())['incidents_resolved'] == 0
    assert call('GET', '/panes/' + pane['id']).json['data']['runtime_status'] == 'working'
    assert call('GET', '/incidents?project=beta', token='a').status_code == 403
    with reconcile_lock(cfg.database_url):
        with pytest.raises(Problem, match='Another reconciliation'):
            reconcile(app.extensions['hsm_engine'], cfg, adapter, FakeAdapter())


def test_noop_never_marks_missing(env):
    cfg, app, call = env
    pane = call('POST', '/panes', {'project_id': 'alpha', 'external_pane_id': 'present-or-not'}).json['data']
    reconcile(app.extensions['hsm_engine'], cfg, NoopAdapter(), NoopAdapter())
    assert call('GET', '/panes/' + pane['id']).json['data']['runtime_status'] == 'unavailable'
    assert not any(i['incident_type'] == 'pane_missing' for i in call('GET', '/incidents').json['data'])


def test_herdr_verified_schema_mapping():
    payload = {'id': 'id', 'result': {'type': 'session_snapshot', 'snapshot': {'protocol': 22, 'panes': [
        {'pane_id': 'p', 'agent_status': 'working', 'agent_session': {'agent': 'hermes', 'kind': 'id', 'value': 's'}}]}}}
    def fake_run(argv, **kwargs):
        assert argv == ['herdr', 'api', 'snapshot']
        assert 'shell' not in kwargs
        kwargs['stdout'].write(json.dumps(payload).encode())
        class Result:
            returncode = 0
        return Result()
    with patch('hsm.adapters.herdr.subprocess.run', fake_run):
        result = HerdrAdapter().observe(['p'])
        assert result.available and result.resources['p'].external_session_id == 's'
        payload['result']['snapshot']['protocol'] = 999
        assert not HerdrAdapter().observe(['p']).available
    with patch('hsm.adapters.herdr.subprocess.run', side_effect=FileNotFoundError):
        assert not HerdrAdapter().observe([]).available


def test_hermes_selects_metadata_only(tmp_path):
    path = tmp_path / 'hermes.db'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE sessions (' + ','.join(COLUMNS) + ')')
        db.execute('INSERT INTO sessions (id,model,profile_name,input_tokens) VALUES (?,?,?,?)', ('s', 'model', 'worker', 100))
    result = HermesAdapter(path).observe(['s', 'not-there'])
    assert result.available
    assert result.resources['s'].status == 'unknown'
    assert result.resources['s'].telemetry['input_tokens'] == 100
    assert 'not-there' not in result.resources
    assert not HermesAdapter().observe([]).available
    bad = tmp_path / 'bad.db'
    bad.write_text('invalid')
    assert not HermesAdapter(bad).observe([]).available


def test_unregistered_runtime_count_is_admin_summary_only():
    payload = {'protocol': 22, 'panes': [{'pane_id': 'unregistered', 'agent_status': 'idle'}]}
    def run(argv, **kwargs):
        kwargs['stdout'].write(json.dumps(payload).encode())
        return type('Result', (), {'returncode': 0})()
    with patch('hsm.adapters.herdr.subprocess.run', run):
        observation = HerdrAdapter().observe([])
    assert observation.available and observation.unregistered_count == 1
    assert observation.resources == {}
