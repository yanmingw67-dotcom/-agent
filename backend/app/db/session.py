import time
from functools import lru_cache

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from app.core.metrics import observe


@lru_cache
def get_engine():
    settings = get_settings()
    kwargs = {}
    if settings.database_url.startswith("postgresql"):
        options = "-c statement_timeout=3000 -c lock_timeout=2000"
        if settings.service_mode == "retrieval":
            options += " -c default_transaction_read_only=on"
        kwargs = {
            "connect_args": {"connect_timeout": 3, "options": options},
            "pool_size": 4,
            "max_overflow": 2,
            "pool_timeout": 3,
        }
    engine = create_engine(settings.database_url, pool_pre_ping=True, **kwargs)

    @event.listens_for(engine, "before_cursor_execute")
    def before(conn, cursor, statement, parameters, context, executemany):
        context._metric_start = time.monotonic()

    @event.listens_for(engine, "after_cursor_execute")
    def after(conn, cursor, statement, parameters, context, executemany):
        observe("database_queries", (time.monotonic() - context._metric_start) * 1000)

    return engine


def session_factory():
    return sessionmaker(get_engine(), expire_on_commit=False)


def get_db():
    with session_factory()() as db:
        yield db
