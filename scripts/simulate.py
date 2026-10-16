#!/usr/bin/env python3
"""Simulate a task/recovery workflow against an explicitly configured test HSM.

Creates a new project. Does not use live Herdr/Hermes or execute approved actions.
"""
import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4
from hsm.client import Client

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--api-url', required=True)
parser.add_argument('--operator-token-file', type=Path, required=True)
parser.add_argument('--agent-token-file', type=Path, required=True)
parser.add_argument('--project', required=True, help='Must be granted to the supplied agent credential')
args = parser.parse_args()
operator = Client(args.api_url, args.operator_token_file.read_text().strip())
agent = Client(args.api_url, args.agent_token_file.read_text().strip())
operator.request('/projects', method='POST', data={'id': args.project, 'name': 'Simulated recovery'})
session = agent.request('/agent-sessions', method='POST', data={'project_id': args.project, 'profile': 'worker'})['data']
task = agent.request('/tasks', method='POST', idempotency_key=str(uuid4()), data={
    'project_id': args.project, 'task_key': 'SIM-1', 'title': 'Simulated work', 'objective': 'Verify recovery', 'owner_session_id': session['id']})['data']
agent.request('/panes', method='POST', data={'project_id': args.project, 'external_pane_id': 'simulated-' + str(uuid4()), 'task_id': task['id'], 'agent_session_id': session['id']})
for state in ['planning', 'running', 'awaiting_approval']:
    task = agent.request('/tasks/' + task['id'] + '/transition', method='POST', idempotency_key=str(uuid4()), data={'expected_version': task['version'], 'state': state})['data']
action = {'project_id': args.project, 'task_id': task['id'], 'operation': 'simulation', 'target': 'no-execution',
          'action': 'record a simulated grant only', 'artifact': 'sha256:simulated',
          'expires_at': (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()}
approval = agent.request('/approvals', method='POST', data=action, idempotency_key=str(uuid4()))['data']
# Explicitly scoped simulation grant; no actual command is authorized/executed.
operator.request('/approvals/' + approval['id'] + '/resolve', method='POST', data={'expected_version': 1, 'decision': 'approved', 'note': 'Explicit simulation'})
consume = {k: action[k] for k in ['task_id', 'operation', 'target', 'action', 'artifact']}
agent.request('/approvals/' + approval['id'] + '/consume', method='POST', data={**consume, 'expected_version': 2})
fresh = Client(args.api_url, args.agent_token_file.read_text().strip())
snapshot = fresh.request('/state', {'project': args.project})['data']
assert snapshot['active_tasks'][0]['id'] == task['id']
assert snapshot['active_tasks'][0]['state'] == 'awaiting_approval'
assert snapshot['agent_sessions'][0]['id'] == session['id']
assert snapshot['active_panes'][0]['task_id'] == task['id']
print('Simulated recovery and single-use grant succeeded. No runtime commands executed.')
