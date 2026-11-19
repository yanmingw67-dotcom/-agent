"""P3.1-B Gate 1：独立验收环境可复现验收。

只操作 product-knowledge-p31b 项目；使用合成公开样例验证基础设施与后台任务，
不使用任何真实 WAF 源码或正式技术文档。证据写入 docs/acceptance。
"""

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

ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = ROOT / ".env.p31b"
DOCX = ROOT / "tests" / "fixtures" / "p12" / "demo_waf_manual.docx"
EVIDENCE = ROOT / "docs" / "acceptance" / "p31b_gate1_evidence.json"
PROJECT = "product-knowledge-p31b"


def env_values():
    values = {}
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    return values


def tag():
    return subprocess.run(
        ["git", "-C", str(ROOT.parent), "rev-parse", "--short=7", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def compose(*args, timeout=600):
    environment = dict(os.environ)
    environment["P31B_IMAGE_TAG"] = tag()
    result = subprocess.run(
        [
            "docker", "compose", "--project-name", PROJECT,
            "--env-file", ".env.p31b", "-f", "docker-compose.p31b.yml", *args,
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env=environment,
        timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    return result.stdout.strip()


def archive(entries):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as package:
        for name, value in entries:
            package.writestr(name, value)
    return output.getvalue()


def wait_job(client, admin, job_id, timeout=180, expected=None):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        response = client.get(f"/api/v1/jobs/{job_id}", headers=admin)
        response.raise_for_status()
        job = response.json()
        if job["status"] != last:
            print(f"job {job_id} {job['job_type']} {job['status']} stage={job['stage']} "
                  f"attempt={job['attempt']} error={job['error_code']}")
            last = job["status"]
        if job["status"] in {"succeeded", "partial", "failed"}:
            if expected and job["status"] != expected:
                raise AssertionError(f"任务 {job_id} 状态 {job['status']}，期望 {expected}")
            return job
        time.sleep(0.3)
    raise AssertionError(f"任务 {job_id} 在 {timeout}s 内未结束")


def create_category(client, admin, prefix):
    code = f"{prefix}-{uuid4().hex[:10]}"
    response = client.post(
        "/api/v1/categories", headers=admin, json={"code": code, "name": "P3.1-B Gate1 合成验收"}
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def workspace(client, admin, category_id, snapshot_id):
    response = client.get(
        f"/api/v1/admin/categories/{category_id}",
        headers=admin,
        params={"snapshot_id": snapshot_id},
    )
    response.raise_for_status()
    return response.json()


def candidate_count(client, admin, category_id, snapshot_id):
    response = client.get(
        f"/api/v1/snapshots/{snapshot_id}/candidates",
        headers=admin,
        params={"category_id": category_id, "limit": 100},
    )
    response.raise_for_status()
    return len(response.json()["results"])


def main():
    env = env_values()
    admin = {"Authorization": f"Bearer {env['ADMIN_TOKEN']}"}
    port = env.get("P31B_API_PORT", "18190")
    evidence = {
        "gate": "P3.1-B Gate 1",
        "mode": "REAL_COMPOSE_SYNTHETIC_SAMPLE",
        "project": PROJECT,
        "build_commit": tag(),
        "checks": {},
        "stages": {},
        "jobs": {},
    }
    failures = []

    def check(name, ok, detail):
        evidence["checks"][name] = {"result": "PASS" if ok else "FAIL", "detail": detail}
        print(("PASS " if ok else "FAIL ") + name + f" {detail}")
        if not ok:
            failures.append(name)

    compose("ps", "--format", "json")
    with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=60) as client:
        ready = client.get("/health/ready")
        check("api_ready", ready.status_code == 200, str(ready.status_code))
        check("unauthorized_denied", client.get("/api/v1/categories").status_code == 401, "401")
        docx_bytes = DOCX.read_bytes()
        check("synthetic_docx_present", DOCX.is_file() and len(docx_bytes) > 0, f"{DOCX.name} {len(docx_bytes)}B")

        # 1. 队列在 worker 停止时入队，证明任务确实经过 Redis/RQ，而不是 API 内同步执行。
        category_id = create_category(client, admin, "gate1-main")
        compose("stop", "worker", "indexer", "builder", "taskworker")
        started = time.monotonic()
        source = (ROOT / "tests" / "fixtures" / "demo_waf_source.zip").read_bytes()
        response = client.post(
            f"/api/v1/categories/{category_id}/source",
            headers=admin,
            files={"file": ("gate1-source.zip", source, "application/zip")},
        )
        assert response.status_code == 202, response.text
        accepted = response.json()
        queued = client.get(f"/api/v1/jobs/{accepted['job_id']}", headers=admin).json()
        check("job_queued_without_worker", queued["status"] == "queued", queued["status"])
        evidence["jobs"]["source_import"] = accepted["job_id"]
        source_snapshot = accepted["snapshot_id"]

        compose("start", "worker", "indexer", "builder", "taskworker")
        import_job = wait_job(client, admin, accepted["job_id"], expected="succeeded")
        evidence["stages"]["source_import_ms"] = round((time.monotonic() - started) * 1000)
        evidence["jobs"]["source_import_detail"] = {
            "status": import_job["status"],
            "attempt": import_job["attempt"],
            "metrics": import_job["metrics_json"],
        }
        check("source_import_succeeded", import_job["status"] == "succeeded", import_job["status"])
        check(
            "source_import_counts",
            import_job["metrics_json"].get("file_count") == 4
            and import_job["metrics_json"].get("excluded_count") == 2,
            json.dumps(import_job["metrics_json"], ensure_ascii=False),
        )

        # 2. 源码索引。
        started = time.monotonic()
        response = client.post(f"/api/v1/snapshots/{source_snapshot}/build", headers=admin)
        assert response.status_code == 202, response.text
        index_job = response.json()
        evidence["jobs"]["source_index"] = index_job["job_id"]
        indexed = wait_job(client, admin, index_job["job_id"], expected="succeeded")
        evidence["stages"]["source_index_ms"] = round((time.monotonic() - started) * 1000)
        check("source_index_succeeded", indexed["status"] == "succeeded", indexed["status"])
        source_ws = workspace(client, admin, category_id, source_snapshot)
        snapshot_row = next(
            row for row in source_ws["snapshots"] if row["id"] == source_snapshot
        )
        check(
            "source_snapshot_indexed",
            snapshot_row.get("index_status") == "indexed",
            snapshot_row.get("index_status", ""),
        )

        # 3. 派生文档暂存快照。
        response = client.post(
            f"/api/v1/categories/{category_id}/document-staging-snapshots", headers=admin
        )
        assert response.status_code == 201, response.text
        derived = response.json()
        staging_id = derived["snapshot_id"]
        evidence["jobs"]["staging_snapshot"] = staging_id
        check(
            "derived_staging_created",
            derived["derived_from_snapshot_id"] == source_snapshot
            and derived["derivation_type"] == "document_update"
            and derived["index_status"] == "not_indexed",
            json.dumps(derived, ensure_ascii=False),
        )

        # 4. 上传合成 DOCX，真实解析与分段。
        started = time.monotonic()
        response = client.post(
            f"/api/v1/categories/{category_id}/documents",
            headers=admin,
            data={"snapshot_id": staging_id},
            files={
                "file": (
                    DOCX.name,
                    docx_bytes,
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                )
            },
        )
        assert response.status_code == 202, response.text
        document_job = response.json()
        evidence["jobs"]["document_index"] = document_job["job_id"]
        parsed = wait_job(client, admin, document_job["job_id"], expected="succeeded")
        evidence["stages"]["document_index_ms"] = round((time.monotonic() - started) * 1000)
        check("document_index_succeeded", parsed["status"] == "succeeded", parsed["status"])
        ws = workspace(client, admin, category_id, staging_id)
        check("document_present", len(ws["documents"]) == 1, f"documents={len(ws['documents'])}")
        doc = ws["documents"][0]
        check(
            "document_parsed",
            doc["parse_status"] == "parsed" and doc["segment_count"] > 0,
            f"parse_status={doc['parse_status']} segments={doc['segment_count']}",
        )
        evidence["stages"]["document_segments"] = doc["segment_count"]

        # 5. 索引暂存快照后做文档发现。
        started = time.monotonic()
        response = client.post(f"/api/v1/snapshots/{staging_id}/build", headers=admin)
        assert response.status_code == 202, response.text
        staging_index_job = response.json()
        evidence["jobs"]["staging_index"] = staging_index_job["job_id"]
        wait_job(client, admin, staging_index_job["job_id"], expected="succeeded")
        started = time.monotonic()
        response = client.post(
            f"/api/v1/snapshots/{staging_id}/discover-documents", headers=admin
        )
        assert response.status_code == 202, response.text
        discovery = response.json()
        evidence["jobs"]["document_discovery"] = discovery["job_id"]
        discovered = wait_job(client, admin, discovery["job_id"], expected="succeeded")
        evidence["stages"]["document_discovery_ms"] = round(
            (time.monotonic() - started) * 1000
        )
        candidates = candidate_count(client, admin, category_id, staging_id)
        ws = workspace(client, admin, category_id, staging_id)
        check(
            "document_discovery_succeeded",
            discovered["status"] == "succeeded"
            and ws["document_discovery"]["status"] == "succeeded",
            json.dumps(
                {
                    "job": discovered["status"],
                    "run": ws["document_discovery"]["status"],
                    "mode": ws["document_discovery"]["mode"],
                    "metrics": ws["document_discovery"]["metrics"],
                },
                ensure_ascii=False,
            ),
        )
        check("document_candidates_nonempty", candidates > 0, f"candidates={candidates}")
        evidence["stages"]["document_candidates"] = candidates

        # 6. 重启恢复：数据、索引、发现结果和已完成任务状态全部保留。
        before = {
            "documents": len(ws["documents"]),
            "segments": doc["segment_count"],
            "candidates": candidates,
            "job_status": discovered["status"],
            "index_status": ws["working_snapshot"]["index_status"],
        }
        compose("restart", timeout=900)
        compose("up", "-d", "--wait", "--wait-timeout", "240")
        after_ws = workspace(client, admin, category_id, staging_id)
        after_job = client.get(
            f"/api/v1/jobs/{discovery['job_id']}", headers=admin
        ).json()
        after_candidates = candidate_count(client, admin, category_id, staging_id)
        after = {
            "documents": len(after_ws["documents"]),
            "segments": after_ws["documents"][0]["segment_count"],
            "candidates": after_candidates,
            "job_status": after_job["status"],
            "index_status": after_ws["working_snapshot"]["index_status"],
        }
        evidence["stages"]["restart_before"] = before
        evidence["stages"]["restart_after"] = after
        check("restart_recovery", before == after, json.dumps(after, ensure_ascii=False))

        # 7. 失败原因可查、安全重试、失败任务不可越过规则变成功。
        fail_category = create_category(client, admin, "gate1-fail")
        slip = archive([("../escape", "bad")])
        response = client.post(
            f"/api/v1/categories/{fail_category}/source",
            headers=admin,
            files={"file": ("slip.zip", slip, "application/zip")},
        )
        assert response.status_code == 202, response.text
        failed = response.json()
        first = wait_job(client, admin, failed["job_id"], expected="failed")
        check(
            "failure_reason_visible",
            first["error_code"] == "UNSAFE_PATH" and first["attempt"] == 1,
            f"error={first['error_code']} attempt={first['attempt']}",
        )
        retry = client.post(f"/api/v1/jobs/{failed['job_id']}/retry", headers=admin)
        check("retry_accepted", retry.status_code == 202, str(retry.status_code))
        second = wait_job(client, admin, failed["job_id"], expected="failed")
        check(
            "retry_safe_same_failure",
            second["error_code"] == "UNSAFE_PATH" and second["attempt"] == 2,
            f"error={second['error_code']} attempt={second['attempt']}",
        )
        rejected = client.post(f"/api/v1/jobs/{accepted['job_id']}/retry", headers=admin)
        check("successful_job_retry_rejected", rejected.status_code == 409, str(rejected.status_code))

        # 8. 授权边界与管理员页面可用性。
        check(
            "read_token_cannot_upload",
            client.post(
                f"/api/v1/categories/{category_id}/source",
                headers={"Authorization": f"Bearer {env['READ_TOKEN']}"},
                files={"file": ("denied.zip", slip, "application/zip")},
            ).status_code
            == 403,
            "403",
        )
        ui = httpx.get(f"http://127.0.0.1:{env.get('P31B_UI_PORT', '18180')}/", timeout=10)
        check("admin_ui_served", ui.status_code == 200 and b"<div id=\"root\"" in ui.content, str(ui.status_code))

        evidence["result"] = "PASS" if not failures else "FAIL"
        evidence["failed_checks"] = failures
        EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
        EVIDENCE.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"P31B_GATE1={'PASS' if not failures else 'FAIL'} evidence={EVIDENCE}")
        return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
