import io
import zipfile

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.db.session import get_engine
from app.main import create_app

ADMIN = {"Authorization": "Bearer " + "a" * 48}
READ = {"Authorization": "Bearer " + "r" * 48}


def make_zip(entries=None, compression=zipfile.ZIP_DEFLATED):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=compression) as archive:
        for name, value in entries or [("main.py", "print('不执行上传源码')\n")]:
            archive.writestr(name, value)
    return output.getvalue()


@pytest.fixture
def environment(tmp_path, monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", "a" * 48)
    monkeypatch.setenv("READ_TOKEN", "r" * 48)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:1/0")
    monkeypatch.setenv("ARCHIVE_ROOT", str(tmp_path / "archives"))
    monkeypatch.setenv("INGESTION_ROOT", str(tmp_path / "ingestion"))
    monkeypatch.setenv("SOURCE_ROOT", str(tmp_path / "ingestion"))
    get_settings.cache_clear()
    get_engine.cache_clear()
    command.upgrade(Config("alembic.ini"), "head")
    yield get_settings()
    get_engine().dispose()
    get_engine.cache_clear()
    get_settings.cache_clear()


@pytest.fixture
def client(environment):
    with TestClient(create_app()) as client:
        yield client
