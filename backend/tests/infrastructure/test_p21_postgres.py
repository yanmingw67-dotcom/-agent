"""P2.1 真实 PostgreSQL 专项：迁移、文本/Word 提取、覆盖与知识库隔离。

P21_RUN_POSTGRES=1 时运行；使用随机专用临时数据库，结束 DROP DATABASE。
不修改现有业务数据库，不创建 CapabilityClaim。
"""

import os
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from app.db.knowledge_models import CapabilityClaim
from app.db.models import Category
from app.db.tender_models import TenderExtractionJob, TenderRegion
from app.main import create_app
from tests.conftest import READ
from tests.p21_assets import five_text, waf_docx


@unittest.skipUnless(os.getenv("P21_RUN_POSTGRES") == "1", "需显式真实PostgreSQL测试环境")
class P21PostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original_env = {
            key: os.environ.get(key)
            for key in [
                "DATABASE_URL",
                "SOURCE_ROOT",
                "INGESTION_ROOT",
                "ARCHIVE_ROOT",
                "TENDER_ROOT",
                "ADMIN_TOKEN",
                "READ_TOKEN",
                "REDIS_URL",
            ]
        }
        base = get_settings().database_url
        assert make_url(base).get_backend_name() == "postgresql"
        cls.admin_engine = create_engine(base, isolation_level="AUTOCOMMIT")
        cls.database = "p21_test_" + uuid4().hex
        cls.directory = tempfile.TemporaryDirectory(prefix="p21-pg-")
        with cls.admin_engine.connect() as db:
            db.execute(text(f'CREATE DATABASE "{cls.database}"'))
        url = make_url(base).set(database=cls.database)
        os.environ["DATABASE_URL"] = url.render_as_string(hide_password=False)
        os.environ["SOURCE_ROOT"] = cls.directory.name
        os.environ["INGESTION_ROOT"] = cls.directory.name
        os.environ["ARCHIVE_ROOT"] = str(Path(cls.directory.name) / "archives")
        os.environ["TENDER_ROOT"] = str(Path(cls.directory.name) / "tender")
        os.environ["ADMIN_TOKEN"] = "a" * 48
        os.environ["READ_TOKEN"] = "r" * 48
        os.environ["REDIS_URL"] = "redis://127.0.0.1:1/0"
        get_settings.cache_clear()
        command.upgrade(Config("alembic.ini"), "head")
        from app.db.session import get_engine

        get_engine.cache_clear()
        cls.engine = create_engine(url)
        cls.sessions = sessionmaker(cls.engine, expire_on_commit=False)

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()
        from app.db.session import get_engine

        get_engine().dispose()
        get_engine.cache_clear()
        assert cls.database.startswith("p21_test_") and len(cls.database) == 41
        with cls.admin_engine.connect() as db:
            db.execute(text(f'DROP DATABASE "{cls.database}" WITH (FORCE)'))
        cls.admin_engine.dispose()
        cls.directory.cleanup()
        for key, value in cls.original_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        get_settings.cache_clear()

    def category(self):
        with self.sessions.begin() as db:
            row = Category(code="p21-" + uuid4().hex, name="P2.1 真实PG测试品类")
            db.add(row)
            db.flush()
            return row.id

    def test_text_and_docx_on_postgres_without_claim(self):
        with TestClient(create_app()) as client:
            cid = self.category()
            created = client.post(
                "/api/v1/tender/extractions",
                headers=READ,
                json={"category_id": cid, "text": five_text()},
            )
            assert created.status_code == 202, created.text
            job_id = created.json()["job_id"]
            result = client.get(f"/api/v1/tender/extractions/{job_id}", headers=READ)
            assert result.status_code == 200
            data = result.json()
            assert data["status"] == "succeeded"
            assert data["extracted_count"] == 5
            assert data["coverage_status"] == "complete"

            upload = client.post(
                "/api/v1/tender/extractions/upload",
                headers=READ,
                data={"category_id": cid},
                files={
                    "file": (
                        "waf.docx",
                        waf_docx(),
                        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    )
                },
            )
            assert upload.status_code == 202, upload.text
            upload_id = upload.json()["job_id"]
            uploaded = client.get(f"/api/v1/tender/extractions/{upload_id}", headers=READ).json()
            assert uploaded["extracted_count"] == 15
            assert sum(len(item["atomic_requirements"]) for item in uploaded["parameters"]) == 45
            assert uploaded["coverage_status"] == "complete"

        with self.sessions() as db:
            assert db.scalar(select(func.count()).select_from(CapabilityClaim)) == 0
            assert db.scalar(select(func.count()).select_from(TenderExtractionJob)) == 2
            assert db.scalar(select(func.count()).select_from(TenderRegion)) > 0
