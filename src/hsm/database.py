from contextlib import contextmanager
from pathlib import Path
from sqlalchemy import create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session


def engine_for(url: str):
    path = make_url(url).database
    if path and path != ':memory:':
        Path(path).parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            Path(path).touch(mode=0o600, exist_ok=False)
        except FileExistsError:
            pass
    engine = create_engine(url, connect_args={'timeout': 5})

    @event.listens_for(engine, 'connect')
    def configure(dbapi, _):
        dbapi.isolation_level = None
        dbapi.execute('PRAGMA foreign_keys=ON')
        dbapi.execute('PRAGMA busy_timeout=5000')
        dbapi.execute('PRAGMA journal_mode=WAL')

    @event.listens_for(engine, 'begin')
    def begin(conn):
        conn.exec_driver_sql('BEGIN IMMEDIATE' if conn.info.get('hsm_write') else 'BEGIN')

    return engine


@contextmanager
def transaction(engine, write=False):
    with engine.connect() as connection:
        connection.info['hsm_write'] = write
        try:
            with Session(connection) as session, session.begin():
                yield session
        finally:
            connection.info.pop('hsm_write', None)


def migrate(url):
    from alembic import command
    from alembic.config import Config
    import sysconfig
    source = Path(__file__).resolve().parents[2]
    root = source if (source / 'alembic.ini').exists() else Path(sysconfig.get_path('data')) / 'share/hsm'
    cfg = Config(str(root / 'alembic.ini'))
    cfg.set_main_option('script_location', str(root / 'migrations'))
    cfg.set_main_option('sqlalchemy.url', url.replace('%', '%%'))
    command.upgrade(cfg, 'head')
