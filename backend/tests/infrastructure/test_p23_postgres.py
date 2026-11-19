"""P2.3 真实 PostgreSQL 端到端：复核追加、Word/Excel 导出与迁移回退。

P23_RUN_POSTGRES=1 时运行；使用随机专用临时数据库，结束 DROP DATABASE。
测试知识均为合成内容，不证明真实雷池 WAF 能力。
"""

import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
from docx import Document
from fastapi.testclient import TestClient
from openpyxl import load_workbook
from sqlalchemy import create_engine, func, inspect, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from app.db.evaluation_models import EvaluationEvidence, ParameterEvaluation
from app.db.session import get_engine
from app.db.workbench_models import EvaluationExportJob, EvaluationReview
from app.main import create_app
from tests.conftest import READ
from tests.integration.test_p22_api import (
    create_evaluation,
    create_extraction,
    phrase_set,
    publish_synthetic,
)


def _download(client, export):
    response = client.get(
        f"/api/v1/tender/workbench/exports/{export['export_id']}/download",
        headers={**READ, "X-Export-Token": export["download_token"]},
    )
    assert response.status_code == 200, response.text
    return response.content


def _create_export(client, job_id, fmt):
    response = client.post(
        f"/api/v1/tender/workbench/tasks/{job_id}/exports",
        headers=READ,
        json={"format": fmt},
    )
    assert response.status_code == 202, response.text
    detail = client.get(
        f"/api/v1/tender/workbench/exports/{response.json()['export_id']}", headers=READ
    )
    assert detail.status_code == 200, detail.text
    return detail.json()


@unittest.skipUnless(os.getenv("P23_RUN_POSTGRES") == "1", "需显式真实PostgreSQL测试环境")
class P23PostgresTests(unittest.TestCase):
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
                "EXPORT_ROOT",
                "ADMIN_TOKEN",
                "READ_TOKEN",
                "REDIS_URL",
            ]
        }
        base = get_settings().database_url
        assert make_url(base).get_backend_name() == "postgresql"
        cls.admin_engine = create_engine(base, isolation_level="AUTOCOMMIT")
        cls.database = "p23_test_" + uuid4().hex
        cls.directory = tempfile.TemporaryDirectory(prefix="p23-pg-")
        with cls.admin_engine.connect() as db:
            db.execute(text(f'CREATE DATABASE "{cls.database}"'))
        url = make_url(base).set(database=cls.database)
        os.environ["DATABASE_URL"] = url.render_as_string(hide_password=False)
        os.environ["SOURCE_ROOT"] = cls.directory.name
        os.environ["INGESTION_ROOT"] = cls.directory.name
        os.environ["ARCHIVE_ROOT"] = str(Path(cls.directory.name) / "archives")
        os.environ["TENDER_ROOT"] = str(Path(cls.directory.name) / "tender")
        os.environ["DOCUMENT_STORE"] = str(Path(cls.directory.name) / "documents")
        os.environ["EXPORT_ROOT"] = str(Path(cls.directory.name) / "exports")
        os.environ["ADMIN_TOKEN"] = "a" * 48
        os.environ["READ_TOKEN"] = "r" * 48
        os.environ["REDIS_URL"] = "redis://127.0.0.1:1/0"
        get_settings.cache_clear()
        command.upgrade(Config("alembic.ini"), "head")
        get_engine.cache_clear()
        cls.engine = create_engine(url)
        cls.sessions = sessionmaker(cls.engine, expire_on_commit=False)

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()
        get_engine().dispose()
        get_engine.cache_clear()
        assert cls.database.startswith("p23_test_") and len(cls.database) == 41
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

    def test_review_export_and_workbench_migration_on_postgres(self):
        with TestClient(create_app()) as client:
            cid, publication = publish_synthetic(client, phrase_set(five=True), "PG工作台")
            extraction_id, _ = create_extraction(client, cid)
            job_id = create_evaluation(client, extraction_id)["job_id"]

            listing = client.get("/api/v1/tender/workbench/tasks", headers=READ)
            assert listing.status_code == 200, listing.text
            assert len(listing.json()["items"]) == 1
            results = client.get(
                f"/api/v1/tender/workbench/tasks/{job_id}/results", headers=READ
            ).json()
            assert results["job"]["publication_id"] == publication["publication_id"]
            assert results["job"]["evidence_context_available"] is True
            parameter = next(item for item in results["parameters"] if item["verdict"] == "支持")
            final = "不支持"
            adjusted = client.post(
                f"/api/v1/tender/workbench/tasks/{job_id}/parameters/"
                f"{parameter['parameter_id']}/review",
                headers=READ,
                json={
                    "action": "adjust",
                    "final_verdict": final,
                    "adjust_reason": "PG 合成测试：人工调整理由",
                    "comment": "PG 合成测试：保留系统自动结论",
                },
            )
            assert adjusted.status_code == 201, adjusted.text
            assert adjusted.json()["auto_verdict"] == parameter["verdict"]

            docx_export = _create_export(client, job_id, "docx")
            assert docx_export["status"] == "succeeded", docx_export
            document = Document(io.BytesIO(_download(client, docx_export)))
            text = "\n".join(paragraph.text for paragraph in document.paragraphs)
            for table in document.tables:
                for row in table.rows:
                    text += "\n" + "\t".join(cell.text for cell in row.cells)
            assert parameter["parameter_name"] in text
            assert "PG 合成测试：人工调整理由" in text
            assert "PG 合成测试：保留系统自动结论" in text
            assert "提供检测报告及功能截图" not in text

            xlsx_export = _create_export(client, job_id, "xlsx")
            assert xlsx_export["status"] == "succeeded", xlsx_export
            workbook = load_workbook(io.BytesIO(_download(client, xlsx_export)))
            assert workbook.sheetnames == [
                "评估结果汇总",
                "原子子要求明细",
                "待确认与问题清单",
                "证据索引",
            ]
            assert workbook["评估结果汇总"].max_row == 6
            assert all(
                cell.data_type != "f"
                for sheet in workbook.worksheets
                for row in sheet.iter_rows()
                for cell in row
            )

        with self.sessions() as db:
            reviews = db.scalar(
                select(func.count())
                .select_from(EvaluationReview)
                .where(EvaluationReview.job_id == job_id)
            )
            exports = db.scalar(
                select(func.count())
                .select_from(EvaluationExportJob)
                .where(EvaluationExportJob.job_id == job_id)
            )
            row = db.scalar(
                select(ParameterEvaluation).where(
                    ParameterEvaluation.job_id == job_id,
                    ParameterEvaluation.parameter_id == parameter["parameter_id"],
                )
            )
            evidence = db.scalar(
                select(func.count())
                .select_from(EvaluationEvidence)
                .where(EvaluationEvidence.parameter_evaluation_id == row.id)
            )
            assert reviews == 1
            assert exports == 2
            assert row.verdict == parameter["verdict"]
            assert evidence >= 1

        get_engine().dispose()
        get_engine.cache_clear()
        config = Config("alembic.ini")
        columns = {
            column["name"] for column in inspect(self.engine).get_columns("evaluation_export_jobs")
        }
        assert {"download_expires_at", "file_sha256"} <= columns
        command.downgrade(config, "0011")
        columns = {
            column["name"] for column in inspect(self.engine).get_columns("evaluation_export_jobs")
        }
        assert "download_expires_at" not in columns
        assert "file_sha256" not in columns
        command.upgrade(config, "head")
        columns = {
            column["name"] for column in inspect(self.engine).get_columns("evaluation_export_jobs")
        }
        assert {"download_expires_at", "file_sha256"} <= columns
        command.downgrade(config, "0010")
        tables = set(inspect(self.engine).get_table_names())
        assert "evaluation_reviews" not in tables
        assert "evaluation_export_jobs" not in tables
        command.upgrade(config, "head")
        tables = set(inspect(self.engine).get_table_names())
        assert "evaluation_reviews" in tables
        assert "evaluation_export_jobs" in tables
        get_engine.cache_clear()

        print(
            "P23_PG_METRICS",
            json.dumps(
                {
                    "job_id": job_id,
                    "parameter_count": xlsx_export["parameter_count"],
                    "atomic_count": xlsx_export["atomic_count"],
                    "review_count": xlsx_export["review_count"],
                    "docx_bytes": docx_export["size_bytes"],
                    "xlsx_bytes": xlsx_export["size_bytes"],
                },
                ensure_ascii=False,
            ),
        )
