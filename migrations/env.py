from alembic import context
from hsm.database import engine_for
from hsm.models import Base

if context.is_offline_mode():
    context.configure(url=context.config.get_main_option('sqlalchemy.url'), target_metadata=Base.metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    engine = engine_for(context.config.get_main_option('sqlalchemy.url'))
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=Base.metadata, render_as_batch=True)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()
