"""本机 Python + 真实 PostgreSQL/Redis/RQ；不替代四服务 Compose 验收。"""

import json
import os
from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
from dotenv import dotenv_values
from fastapi.testclient import TestClient
from rq import SimpleWorker
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError

from app.core.config import get_settings
from app.db.models import Category, Snapshot
from app.db.session import get_engine, session_factory
from app.jobs.queue import connection, dispatch_pending
from app.main import create_app


def main():
    env = dotenv_values(".env")
    os.environ.update(
        {
            "ADMIN_TOKEN": env["ADMIN_TOKEN"],
            "READ_TOKEN": env["READ_TOKEN"],
            "DATABASE_URL": f"postgresql+psycopg://knowledge:{env['POSTGRES_PASSWORD']}@127.0.0.1:15432/knowledge",
            "REDIS_URL": "redis://127.0.0.1:16379/0",
            "ARCHIVE_ROOT": str(Path("storage/archives").resolve()),
            "INGESTION_ROOT": str(Path("storage/ingestion").resolve()),
            "SOURCE_ROOT": str(Path("storage/ingestion").resolve()),
            "QUEUE_NAME": "p11-infrastructure-check",
        }
    )
    get_settings.cache_clear()
    get_engine.cache_clear()
    settings = get_settings()
    config = Config("alembic.ini")
    command.upgrade(config, "head")
    command.check(config)
    report = {
        "mode": "REAL_POSTGRES_REDIS_RQ_LOCAL_PYTHON",
        "cases": [],
        "four_service_compose": "UNVERIFIED_IMAGE_DOWNLOAD_BLOCKED",
    }
    headers = {"Authorization": f"Bearer {env['ADMIN_TOKEN']}"}
    with TestClient(create_app()) as client:
        assert client.get("/health/ready").status_code == 200
        import io
        import stat
        import zipfile

        def package(entries):
            data = io.BytesIO()
            with zipfile.ZipFile(data, "w", zipfile.ZIP_DEFLATED) as z:
                for name, value in entries:
                    z.writestr(name, value)
            return data.getvalue()

        link = zipfile.ZipInfo("link")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        cases = [
            ("valid", Path("tests/fixtures/demo_waf_source.zip").read_bytes(), None),
            ("slip", package([("../escape", "bad")]), "UNSAFE_PATH"),
            ("link", package([(link, "/etc/passwd")]), "LINK_OR_SPECIAL_FILE"),
            ("bomb", package([("bomb", "x" * 200000)]), "COMPRESSION_RATIO_EXCEEDED"),
        ]
        for label, data, expected_error in cases:
            cid = client.post(
                "/api/v1/categories",
                headers=headers,
                json={"code": "infra-" + uuid4().hex[:12], "name": "人工真实集成验证"},
            ).json()["id"]
            accepted = client.post(
                f"/api/v1/categories/{cid}/source",
                headers=headers,
                files={"file": ("demo.zip", data, "application/zip")},
            )
            assert accepted.status_code == 202, accepted.text
            ids = accepted.json()
            assert (
                client.get(f"/api/v1/jobs/{ids['job_id']}", headers=headers).json()["status"]
                == "queued"
            )
            assert dispatch_pending() == 1
            worker = SimpleWorker([settings.queue_name], connection=connection())
            worker.work(burst=True, logging_level="WARNING")
            result = client.get(f"/api/v1/jobs/{ids['job_id']}", headers=headers).json()
            assert result["error_code"] == expected_error, result
            assert result["status"] == ("failed" if expected_error else "succeeded")
            if expected_error:
                retry = client.post(f"/api/v1/jobs/{ids['job_id']}/retry", headers=headers)
                assert retry.status_code == 202
                assert dispatch_pending() == 1
                worker.work(burst=True, logging_level="WARNING")
                again = client.get(f"/api/v1/jobs/{ids['job_id']}", headers=headers).json()
                assert again["error_code"] == expected_error and again["attempt"] == 2
            report["cases"].append({"label": label, **ids, "result": result})
    with session_factory()() as db:
        snapshot = db.scalar(select(Snapshot).where(Snapshot.status == "extracted"))
        category = db.get(Category, snapshot.category_id)
        category.active_snapshot_id = snapshot.id
        try:
            db.commit()
        except DBAPIError:
            db.rollback()
        else:
            raise AssertionError("PostgreSQL 不应允许未发布快照生效")
        snapshot.source_sha256 = "0" * 64
        try:
            db.commit()
        except DBAPIError:
            db.rollback()
        else:
            raise AssertionError("PostgreSQL 不应允许篡改快照哈希")
    report["database_guards"] = {"unpublished_pointer_blocked": True, "immutable_hash": True}
    report["alembic_check"] = "passed"
    report["model_requests"] = report["tokens"] = report["model_cost"] = 0
    Path("reports/infrastructure-check.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2)
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
