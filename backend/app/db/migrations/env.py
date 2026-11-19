from alembic import context
from sqlalchemy import create_engine, pool

from app.core.config import get_settings
from app.db import (  # noqa: F401
    evaluation_models,
    index_models,
    knowledge_models,
    review_models,
    tender_models,
    workbench_models,
)
from app.db.models import Base

config = context.config
target_metadata = Base.metadata


def run():
    url = get_settings().database_url
    if context.is_offline_mode():
        context.configure(url=url, target_metadata=target_metadata, literal_binds=True)
        with context.begin_transaction():
            context.run_migrations()
    else:
        engine = create_engine(url, poolclass=pool.NullPool)
        with engine.connect() as connection:
            context.configure(connection=connection, target_metadata=target_metadata)
            with context.begin_transaction():
                context.run_migrations()


run()
