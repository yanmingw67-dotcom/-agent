"""真实七服务P1.3验收；无模型配置时明确BLOCKED，不自动降级为Mock。"""

import json
import subprocess
import time
from pathlib import Path
from uuid import uuid4

import httpx
from dotenv import dotenv_values

from scripts.p13_assets import MANUAL, source_zip

ROOT = Path("reports/p13")


def main():
    env = dotenv_values(".env")
    headers = {"Authorization": f"Bearer {env['ADMIN_TOKEN']}"}
    api = httpx.Client(base_url="http://127.0.0.1:8000", headers=headers, timeout=15)
    retrieval = httpx.Client(base_url="http://127.0.0.1:8001", headers=headers, timeout=10)
    report = {"mode": "REAL_SERVICES_REAL_MODEL_ONLY", "checks": {}, "latencies_ms": []}

    def wait(jid, timeout=180):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            r = api.get(f"/api/v1/jobs/{jid}")
            r.raise_for_status()
            job = r.json()
            if job["status"] in {"succeeded", "partial", "failed"}:
                return job
            time.sleep(0.2)
        raise AssertionError("任务超时")

    r = api.post(
        "/api/v1/categories", json={"code": "p13-" + uuid4().hex[:10], "name": "P1.3 人工公开验收"}
    )
    r.raise_for_status()
    cid = r.json()["id"]
    r = api.post(
        f"/api/v1/categories/{cid}/source",
        files={"file": ("p13.zip", source_zip(), "application/zip")},
    )
    assert r.status_code == 202, r.text
    sid = r.json()["snapshot_id"]
    assert wait(r.json()["job_id"])["status"] == "succeeded"
    r = api.post(f"/api/v1/snapshots/{sid}/build")
    assert r.status_code == 202, r.text
    assert wait(r.json()["job_id"])["status"] == "succeeded"
    r = api.post(
        f"/api/v1/categories/{cid}/documents",
        data={"snapshot_id": sid},
        files={"file": ("manual.md", MANUAL.encode(), "text/markdown")},
    )
    assert r.status_code == 202, r.text
    assert wait(r.json()["job_id"])["status"] == "succeeded"
    result = retrieval.post(
        f"/api/v1/retrieval/{cid}", params={"snapshot_id": sid}, json={"tool": "get_project_map"}
    ).json()
    mid = next(i["module_id"] for i in result["results"] if i["name"] == "notify")
    started = time.monotonic()
    r = api.post(
        f"/api/v1/snapshots/{sid}/investigate",
        json={"module_ids": [mid], "data_classification": "synthetic_public"},
    )
    assert r.status_code == 202, r.text
    report["enqueue_latency_ms"] = round((time.monotonic() - started) * 1000, 3)
    jid = r.json()["job_id"]
    report.update(category_id=cid, snapshot_id=sid, job_id=jid)
    deadline = time.monotonic() + 240
    while time.monotonic() < deadline:
        job = api.get(f"/api/v1/jobs/{jid}").json()
        start = time.perf_counter()
        response = retrieval.post(
            f"/api/v1/retrieval/{cid}",
            params={"snapshot_id": sid},
            json={"tool": "search_symbols", "query": "notify"},
        )
        assert response.status_code == 200, response.text
        report["latencies_ms"].append((time.perf_counter() - start) * 1000)
        if job["status"] in {"succeeded", "partial", "failed"}:
            break
        time.sleep(0.1)
    else:
        raise AssertionError("知识构建未在验收预算内结束")
    report["job"] = job
    report["investigations"] = api.get(f"/api/v1/jobs/{jid}/investigations").json()
    traces = []
    cursor = 0
    while True:
        r = api.get(f"/api/v1/jobs/{jid}/investigation-traces", params={"cursor": cursor}).json()
        traces += r["results"]
        if not r["truncated"]:
            break
        cursor = r["next_cursor"]
    report["actual_model_tool_trace"] = traces
    report["checks"]["async_queue_and_retrieval"] = True
    blocked = job.get("error_code") in {"MODEL_NOT_CONFIGURED", "MODEL_DATA_NOT_AUTHORIZED"}
    if blocked:
        report["real_model_status"] = "BLOCKED"
        report["blocker"] = job["error_code"]
        report["source_to_claim_evidence_example"] = None
    else:
        if job["status"] != "succeeded":
            report["real_model_status"] = "FAILED"
            ROOT.mkdir(parents=True, exist_ok=True)
            (ROOT / ("real-failed-" + jid + ".json")).write_text(
                json.dumps(report, ensure_ascii=False, indent=2)
            )
            raise AssertionError(job)
        report["real_model_status"] = "EXECUTED"
        candidates = api.get(
            f"/api/v1/snapshots/{sid}/candidates", params={"category_id": cid}
        ).json()
        claims = candidates["results"]
        assert claims and any(c["draft"]["evidence_ids"] for c in claims), candidates
        evidence_ids = sorted({eid for c in claims for eid in c["draft"]["evidence_ids"]})
        evidence = []
        for eid in evidence_ids:
            r = api.get(f"/api/v1/evidence/{eid}", params={"category_id": cid, "snapshot_id": sid})
            r.raise_for_status()
            evidence.append(r.json())
        assert any(t["kind"] == "evidence_registered" for t in traces)
        assert job["metrics_json"]["model_requests"] > 0
        report["source_to_claim_evidence_example"] = {
            "input": "p13_assets.source_zip + MANUAL",
            "candidates": candidates,
            "evidence": evidence,
        }
        report["checks"]["model_tool_calls_and_real_evidence"] = True
    ids = subprocess.check_output(["docker", "compose", "ps", "-q"], text=True).split()
    raw = json.loads(subprocess.check_output(["docker", "inspect", *ids], text=True))
    report["containers"] = [
        {
            "name": c["Name"],
            "cpu_limit": c["HostConfig"]["NanoCpus"] / 1e9,
            "memory_limit": c["HostConfig"]["Memory"],
            "oom_killed": c["State"]["OOMKilled"],
            "restart_count": c["RestartCount"],
            "health": c["State"].get("Health", {}).get("Status"),
        }
        for c in raw
    ]
    assert sum(c["cpu_limit"] for c in report["containers"]) <= 4
    assert sum(c["memory_limit"] for c in report["containers"]) <= 8 * 1024**3
    report["checks"]["container_resource_budget"] = True
    report["target_vm_status"] = "NOT_TESTED"
    ROOT.mkdir(parents=True, exist_ok=True)
    (ROOT / "real-acceptance.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
