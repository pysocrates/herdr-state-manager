import json
import os
from dataclasses import dataclass, field
from pathlib import Path


def load_credentials():
    file = os.getenv('HSM_CREDENTIALS_FILE')
    if file:
        return json.loads(Path(file).read_text())
    return json.loads(os.getenv('HSM_CREDENTIALS', '[]'))


@dataclass
class Config:
    database_url: str = field(default_factory=lambda: os.getenv(
        'HSM_DATABASE_URL', f'sqlite:///{Path.home()}/.local/state/hsm/state.db'))
    credentials: list[dict] = field(default_factory=lambda: load_credentials())
    recent_events: int = 30

    def validate(self):
        if not self.database_url.startswith('sqlite:///'):
            raise ValueError('HSM requires a local SQLite database URL')
        from sqlalchemy.engine import make_url
        database = make_url(self.database_url).database
        if not database or not Path(database).is_absolute():
            raise ValueError('Use an absolute SQLite database path outside the source repository')
        seen = set()
        identities = set()
        for c in self.credentials:
            if (not isinstance(c.get('token'), str) or len(c['token']) < 32
                    or c['token'] in seen or not c.get('identity') or c['identity'] in identities
                    or c.get('role') not in {'agent', 'operator'}
                    or not isinstance(c.get('projects'), list) or not isinstance(c.get('actions'), list)
                    or not all(isinstance(x, str) for x in c['projects'] + c['actions'])):
                raise ValueError('Invalid credential configuration')
            if c['role'] == 'agent' and ('*' in c['projects'] or '*' in c['actions']):
                raise ValueError('Agent grants must be explicit')
            seen.add(c['token'])
            identities.add(c['identity'])
