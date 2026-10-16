#!/usr/bin/env python3
"""Create private credential files without printing tokens; refuses overwrite."""
import argparse
import json
import os
from pathlib import Path
import re
import secrets

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('directory', type=Path)
parser.add_argument('--project', required=True)
args = parser.parse_args()
if not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}', args.project):
    parser.error('Invalid project slug')
os.umask(0o077)
args.directory.mkdir(mode=0o700, parents=True, exist_ok=False)
agent, operator = secrets.token_urlsafe(48), secrets.token_urlsafe(48)
credentials = [
    {'identity': 'operator', 'role': 'operator', 'token': operator, 'projects': ['*'], 'actions': ['*']},
    {'identity': 'agent', 'role': 'agent', 'token': agent, 'projects': [args.project],
     'actions': ['read', 'tasks:write', 'runtime:write', 'approvals:request', 'approvals:consume']},
]
for name, content in [('credentials.json', json.dumps(credentials, indent=2)), ('operator.token', operator), ('agent.token', agent)]:
    with (args.directory / name).open('x') as f:
        f.write(content + '\n')
print(f'Created private credential files in {args.directory}. No tokens printed.')
