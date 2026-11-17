"""四服务持久化/重启恢复验证；只操作本项目容器，不删除数据。"""

import json
import os
import subprocess
from pathlib import Path
from uuid import uuid4

import httpx
from dotenv import dotenv_values

from scripts.compose_smoke import wait_job


def compose(*args):
    result = subprocess.run(["docker", "compose", *args], capture_output=True, text=True)
    print(f"docker compose {' '.join(args)} -> exit={result.returncode}")
    print(result.stdout)
    print(result.stderr)
    result.check_returncode()
    return result.stdout


def main():
    env = dotenv_values(".env")
    headers = {"Authorization": f"Bearer {env['ADMIN_TOKEN']}"}
    port = os.environ.get("API_PORT", "8000")
    report = {"mode": "REAL_FOUR_SERVICE_RESTART", "checks": {}}
    with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=15) as client:
        response = client.post(
            "/api/v1/categories",
            headers=headers,
            json={"code": "recovery-" + uuid4().hex[:12], "name": "重启恢复人工测试"},
        )
        assert response.status_code == 201
        cid = response.json()["id"]
        compose("stop", "worker")
        try:
            response = client.post(
                f"/api/v1/categories/{cid}/source",
                headers=headers,
                files={
                    "file": (
                        "demo.zip",
                        Path("tests/fixtures/demo_waf_source.zip").read_bytes(),
                        "application/zip",
                    )
                },
            )
            assert response.status_code == 202, response.text
            ids = response.json()
            assert (
                client.get(f"/api/v1/jobs/{ids['job_id']}", headers=headers).json()["status"]
                == "queued"
            )
            compose(
                "exec",
                "-T",
                "api",
                "python",
                "-c",
                "from app.jobs.queue import dispatch_pending; print(dispatch_pending())",
            )
            # Worker 停机期间已有真实 Redis 消息；重启验证 AOF 和数据库持久化。
            compose("restart", "postgres", "redis")
        finally:
            compose("up", "-d", "--wait", "--wait-timeout", "120")
        first = wait_job(client, headers, ids["job_id"])
        assert first["status"] == "succeeded" and first["stage"] == "extracted"
        report["checks"]["queued_upload_while_worker_stopped"] = True
        report["checks"]["redis_postgres_restart_then_worker_consumes"] = True
        compose("restart")
        compose("up", "-d", "--wait", "--wait-timeout", "120")
        after = client.get(f"/api/v1/jobs/{ids['job_id']}", headers=headers)
        assert after.status_code == 200 and after.json() == first
        verify = compose(
            "exec",
            "-T",
            "worker",
            "python",
            "-c",
            "from pathlib import Path; from app.core.config import get_settings; "
            f"p=get_settings().source_root/'{ids['snapshot_id']}'/'manifest.json'; "
            "assert p.is_file(); print('manifest_persisted')",
        )
        assert "manifest_persisted" in verify
        report["checks"]["completed_job_and_manifest_persist_after_full_restart"] = True
        report.update(ids)
        report["result"] = first
    output = Path(os.environ.get("REPORT_DIR", "reports"))
    output.mkdir(parents=True, exist_ok=True)
    (output / "compose-recovery.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
