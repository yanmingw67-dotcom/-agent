"""针对真实 Compose API、PostgreSQL、Redis、RQ Worker 的验收，不使用队列 Mock。"""

import io
import json
import os
import stat
import subprocess
import time
import zipfile
from pathlib import Path
from uuid import uuid4

import httpx
from dotenv import dotenv_values


def archive(entries, compression=zipfile.ZIP_DEFLATED):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=compression) as package:
        for name, value in entries:
            package.writestr(name, value)
    return output.getvalue()


def wait_job(client, headers, job_id):
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        response = client.get(f"/api/v1/jobs/{job_id}", headers=headers)
        response.raise_for_status()
        result = response.json()
        if result["status"] in {"succeeded", "failed"}:
            return result
        time.sleep(0.2)
    raise RuntimeError("真实 Worker 未在 45 秒内完成导入")


def main():
    env = dotenv_values(".env")
    admin = {"Authorization": f"Bearer {env['ADMIN_TOKEN']}"}
    read = {"Authorization": f"Bearer {env['READ_TOKEN']}"}
    port = os.environ.get("API_PORT", "8000")
    report = {"mode": "REAL_COMPOSE_NO_MOCK", "cases": [], "agent_status": "NOT_IMPLEMENTED"}
    link = zipfile.ZipInfo("link")
    link.create_system = 3
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    cases = [
        ("legal", Path("tests/fixtures/demo_waf_source.zip").read_bytes(), None),
        ("slip", archive([("../escape", "bad")]), "UNSAFE_PATH"),
        ("symlink", archive([(link, "/etc/passwd")]), "LINK_OR_SPECIAL_FILE"),
        ("bomb", archive([("bomb.txt", "0" * 200000)]), "COMPRESSION_RATIO_EXCEEDED"),
        ("collision", archive([("a.py", "x"), ("A.py", "y")]), "PATH_COLLISION"),
    ]
    with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=10) as client:
        assert client.get("/health/ready").status_code == 200
        assert client.get("/api/v1/categories").status_code == 401
        for name, data, expected_error in cases:
            code = f"smoke-{name}-{uuid4().hex[:8]}"
            response = client.post(
                "/api/v1/categories", headers=admin, json={"code": code, "name": "人工验收样例"}
            )
            assert response.status_code == 201, response.text
            cid = response.json()["id"]
            denied = client.post(
                f"/api/v1/categories/{cid}/source",
                headers=read,
                files={"file": ("sample.zip", data, "application/zip")},
            )
            assert denied.status_code == 403
            start = time.monotonic()
            response = client.post(
                f"/api/v1/categories/{cid}/source",
                headers=admin,
                files={"file": ("sample.zip", data, "application/zip")},
            )
            assert response.status_code == 202, response.text
            accepted = response.json()
            upload_ms = round((time.monotonic() - start) * 1000)
            result = wait_job(client, admin, accepted["job_id"])
            assert result["error_code"] == expected_error, result
            assert result["status"] == ("failed" if expected_error else "succeeded")
            if expected_error is None:
                assert result["stage"] == "extracted"
                assert result["metrics_json"]["file_count"] == 4
                assert result["metrics_json"]["excluded_count"] == 2
            else:
                # 同一不可变输入重试，仍应明确失败，不能伪装修复或成功。
                retried = client.post(f"/api/v1/jobs/{accepted['job_id']}/retry", headers=admin)
                assert retried.status_code == 202
                again = wait_job(client, admin, accepted["job_id"])
                assert again["error_code"] == expected_error and again["attempt"] == 2
            report["cases"].append(
                {"name": name, **accepted, "upload_response_ms": upload_ms, "result": result}
            )
        assert all(
            not c["knowledge_ready"] for c in client.get("/api/v1/categories", headers=read).json()
        )
    # 直接在真实服务容器内校验迁移一致性、数据库保护与只读挂载，不读取源码原文。
    verification = """
import errno, json, os
from pathlib import Path
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError
from app.core.config import get_settings
from app.db.models import Category, Snapshot, AuditEvent
from app.db.session import session_factory
s = get_settings()
assert os.geteuid() != 0
with session_factory()() as db:
    snapshot = db.scalar(select(Snapshot).where(Snapshot.status == 'extracted'))
    category = db.get(Category, snapshot.category_id)
    category.active_snapshot_id = snapshot.id
    try:
        db.commit()
    except DBAPIError:
        db.rollback()
    else:
        raise AssertionError('未发布快照不应成为生效知识库')
    audit_count = len(db.scalars(select(AuditEvent)).all())
roots = list(s.source_root.glob('*/manifest.json'))
assert roots
try:
    with (s.source_root / 'must-not-write').open('w') as out:
        out.write('bad')
except OSError as exc:
    assert exc.errno == errno.EROFS
else:
    raise AssertionError('SOURCE_ROOT 挂载非只读')
assert not (s.ingestion_root.parent / 'escape').exists()
print(json.dumps({'non_root': True, 'source_mount_read_only': True,
    'unpublished_pointer_blocked': True, 'audit_events': audit_count,
    'manifests': len(roots)}))
"""
    result = subprocess.run(
        ["docker", "compose", "exec", "-T", "worker", "python", "-c", verification],
        check=True,
        capture_output=True,
        text=True,
    )
    report["infrastructure_checks"] = json.loads(result.stdout.strip())
    Path("reports/compose-smoke.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
