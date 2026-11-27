from alembic import context

from app.core.config import get_settings
from app.core.database import make_engine
from app.models import Base

config = context.config
target_metadata = Base.metadata


def database_url() -> str:
    return config.attributes.get("database_url") or get_settings().database_url


def run_offline() -> None:
    context.configure(url=database_url(), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_online() -> None:
    engine = make_engine(database_url())
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, render_as_batch=True)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_offline()
else:
    run_online()
