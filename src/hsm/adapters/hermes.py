"""Opt-in read-only metadata query, verified against Hermes 0.19.0 SCHEMA_SQL.

Never instantiate Hermes SessionDB (it may migrate); never query messages/prompts.
A history row proves recorded existence, not process liveness or context capacity.
"""
import sqlite3
from pathlib import Path
from .base import Observation, Observations

COLUMNS = ('id', 'model', 'profile_name', 'started_at', 'ended_at', 'input_tokens',
           'output_tokens', 'cache_read_tokens', 'cache_write_tokens', 'reasoning_tokens')


class HermesAdapter:
    def __init__(self, database_path=None):
        self.path = Path(database_path) if database_path else None

    def observe(self, identifiers):
        if self.path is None or not self.path.is_file():
            return Observations(False)
        try:
            with sqlite3.connect(self.path.resolve().as_uri() + '?mode=ro', uri=True, timeout=2) as db:
                db.execute('PRAGMA query_only=ON')
                # Cancel unexpectedly expensive operations, including incompatible schemas.
                budget = [0]
                def bound():
                    budget[0] += 1
                    return int(budget[0] > 10000)
                db.set_progress_handler(bound, 1000)
                available = {row[1] for row in db.execute('PRAGMA table_info(sessions)')}
                if not set(COLUMNS) <= available:
                    return Observations(False)
                resources = {}
                for ident in set(identifiers):
                    row = db.execute('SELECT ' + ','.join(COLUMNS) + ' FROM sessions WHERE id=?', (ident,)).fetchone()
                    if row:
                        metadata = dict(zip(COLUMNS, row))
                        metadata.pop('id')
                        if len(str(metadata)) > 8192:
                            return Observations(False)
                        resources[ident] = Observation('terminated' if metadata['ended_at'] is not None else 'unknown', metadata)
                return Observations(True, resources)
        except (sqlite3.Error, OSError, ValueError):
            return Observations(False)
