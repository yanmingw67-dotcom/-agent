"""P2.2 真实 PostgreSQL 端到端：固定发布、5/15 参数、证据回读和表计数。

P22_RUN_POSTGRES=1 时运行；使用随机专用临时数据库，结束 DROP DATABASE。
测试知识均为合成内容，不证明真实雷池 WAF 能力。
"""

import json
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
from app.db.evaluation_models import (
    AtomicEvaluation,
    EvaluationEvidence,
    EvaluationJob,
    ParameterEvaluation,
)
from app.db.models import Category
from app.main import create_app
from tests.conftest import READ
from tests.integration.test_p22_api import (
    create_evaluation,
    create_extraction,
    phrase_set,
    publish_synthetic,
)
from tests.p21_assets import PARAM_NAMES, waf_docx


@unittest.skipUnless(os.getenv("P22_RUN_POSTGRES") == "1", "需显式真实PostgreSQL测试环境")
class P22PostgresTests(unittest.TestCase):
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
                "DOCUMENT_STORE",
                "ADMIN_TOKEN",
                "READ_TOKEN",
                "REDIS_URL",
            ]
        }
        base = get_settings().database_url
        assert make_url(base).get_backend_name() == "postgresql"
        cls.admin_engine = create_engine(base, isolation_level="AUTOCOMMIT")
        cls.database = "p22_test_" + uuid4().hex
        cls.directory = tempfile.TemporaryDirectory(prefix="p22-pg-")
        with cls.admin_engine.connect() as db:
            db.execute(text(f'CREATE DATABASE "{cls.database}"'))
        url = make_url(base).set(database=cls.database)
        os.environ["DATABASE_URL"] = url.render_as_string(hide_password=False)
        os.environ["SOURCE_ROOT"] = cls.directory.name
        os.environ["INGESTION_ROOT"] = cls.directory.name
        os.environ["ARCHIVE_ROOT"] = str(Path(cls.directory.name) / "archives")
        os.environ["TENDER_ROOT"] = str(Path(cls.directory.name) / "tender")
        os.environ["DOCUMENT_STORE"] = str(Path(cls.directory.name) / "documents")
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
        assert cls.database.startswith("p22_test_") and len(cls.database) == 41
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

    def test_pinned_publication_five_and_word_fifteen_on_postgres(self):
        with TestClient(create_app()) as client:
            cid, publication = publish_synthetic(client, phrase_set(five=True), "五项")
            extraction_id, extraction = create_extraction(client, cid)
            assert extraction["extracted_count"] == 5
            job_id = create_evaluation(client, extraction_id, key="p22-pg-five")["job_id"]
            data = client.get(f"/api/v1/tender/evaluations/{job_id}/results", headers=READ).json()
            assert data["job"]["status"] == "succeeded", data["job"]
            assert data["job"]["publication_id"] == publication["publication_id"]
            assert data["job"]["checkpoint"] == {"completed": 5, "total": 5}
            assert len(data["parameters"]) == 5
            assert {p["verdict"] for p in data["parameters"]} <= {"支持", "不支持", "无法确认"}

            cid2, publication2 = publish_synthetic(client, phrase_set(PARAM_NAMES), "十五项")
            upload = client.post(
                "/api/v1/tender/extractions/upload",
                headers=READ,
                data={"category_id": cid2},
                files={
                    "file": (
                        "synthetic-waf.docx",
                        waf_docx(),
                        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    )
                },
            )
            assert upload.status_code == 202, upload.text
            word_id = upload.json()["job_id"]
            word = client.get(f"/api/v1/tender/extractions/{word_id}", headers=READ).json()
            assert word["extracted_count"] == 15
            assert sum(len(item["atomic_requirements"]) for item in word["parameters"]) == 45
            word_job = create_evaluation(client, word_id)["job_id"]
            word_data = client.get(
                f"/api/v1/tender/evaluations/{word_job}/results", headers=READ
            ).json()
            assert word_data["job"]["status"] == "succeeded", word_data["job"]
            assert word_data["job"]["publication_id"] == publication2["publication_id"]
            assert len(word_data["parameters"]) == 15
            assert sum(len(p["atomic_requirements"]) for p in word_data["parameters"]) == 45

            print(
                "P22_METRICS",
                json.dumps(
                    {
                        "five": {
                            key: data["job"]["metrics"].get(key)
                            for key in ("elapsed_ms", "failed_parameters")
                        },
                        "word": {
                            key: word_data["job"]["metrics"].get(key)
                            for key in ("elapsed_ms", "failed_parameters")
                        },
                    },
                    ensure_ascii=False,
                ),
            )
            after = client.get(f"/api/v1/tender/evaluations/{job_id}/results", headers=READ).json()
            assert after["job"]["publication_id"] == publication["publication_id"]

        with self.sessions() as db:
            assert db.get(EvaluationJob, job_id).publication_id == publication["publication_id"]
            assert (
                db.scalar(
                    select(func.count())
                    .select_from(ParameterEvaluation)
                    .where(ParameterEvaluation.job_id == job_id)
                )
                == 5
            )
            assert (
                db.scalar(
                    select(func.count())
                    .select_from(AtomicEvaluation)
                    .where(AtomicEvaluation.job_id == job_id)
                )
                == 9
            )
            assert (
                db.scalar(
                    select(func.count())
                    .select_from(AtomicEvaluation)
                    .where(AtomicEvaluation.job_id == word_job)
                )
                == 45
            )
            states = set(
                db.scalars(
                    select(EvaluationEvidence.verification_state).where(
                        EvaluationEvidence.job_id.in_([job_id, word_job])
                    )
                )
            )
            assert states <= {
                "verified_support",
                "verified_contradiction",
                "verified_context",
            }
            assert "verified_support" in states
            assert db.scalar(select(func.count()).select_from(Category)) == 2
