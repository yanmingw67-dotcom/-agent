"""P1.2 六服务真实端到端与配额/检索并行基准；只使用人工公开资产。"""

import json
import os
import subprocess
import threading
import time
from pathlib import Path
from uuid import uuid4

import httpx
from dotenv import dotenv_values

from scripts.generate_p12_fixtures import source_zip

ROOT = Path(os.environ.get("REPORT_DIR", "reports/p12"))
ROOT.mkdir(parents=True, exist_ok=True)


def command(*args):
    result = subprocess.run(list(args), check=True, capture_output=True, text=True)
    return result.stdout


def percentile(values, q):
    ordered = sorted(values)
    return (
        round(ordered[min(len(ordered) - 1, int((len(ordered) - 1) * q))], 3) if ordered else None
    )


def main():
    env = dotenv_values(".env")
    admin = {"Authorization": f"Bearer {env['ADMIN_TOKEN']}"}
    read = {"Authorization": f"Bearer {env['READ_TOKEN']}"}
    api = httpx.Client(
        base_url="http://127.0.0.1:" + os.environ.get("API_PORT", "8000"), timeout=15, headers=admin
    )
    search = httpx.Client(
        base_url="http://127.0.0.1:" + os.environ.get("RETRIEVAL_PORT", "8001"),
        timeout=10,
        headers=admin,
    )
    report = {"mode": "REAL_SIX_CONTAINER_P12", "checks": {}, "model_requests": 0, "tokens": 0}
    raw_samples, stats = [], []
    stop = threading.Event()

    def sampler():
        while not stop.is_set():
            try:
                output = command(
                    "docker",
                    "stats",
                    "--no-stream",
                    "--format",
                    "{{json .}}",
                    "product-knowledge-p11-indexer-1",
                    "product-knowledge-p11-retrieval-1",
                )
                for line in output.splitlines():
                    stats.append({"observed_at": time.time(), **json.loads(line)})
            except subprocess.CalledProcessError:
                pass

    def category(label):
        response = api.post(
            "/api/v1/categories",
            json={"code": "p12-" + label + "-" + uuid4().hex[:8], "name": "P1.2 人工验收"},
        )
        response.raise_for_status()
        return response.json()["id"]

    def wait(jid, timeout=180):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            response = api.get(f"/api/v1/jobs/{jid}")
            response.raise_for_status()
            job = response.json()
            if job["status"] in {"failed", "partial", "succeeded"}:
                return job
            time.sleep(0.2)
        raise AssertionError(f"任务未在 {timeout}s 完成：{jid}")

    def upload(cid, data):
        response = api.post(
            f"/api/v1/categories/{cid}/source",
            files={"file": ("demo.zip", data, "application/zip")},
        )
        assert response.status_code == 202, response.text
        ids = response.json()
        job = wait(ids["job_id"])
        assert job["status"] == "succeeded", job
        return ids["snapshot_id"]

    def build(sid):
        response = api.post(f"/api/v1/snapshots/{sid}/build")
        assert response.status_code == 202, response.text
        return response.json()["job_id"]

    def tool(cid, sid, name, **kwargs):
        response = search.post(
            f"/api/v1/retrieval/{cid}", params={"snapshot_id": sid}, json={"tool": name, **kwargs}
        )
        assert response.status_code == 200, response.text
        return response.json()

    cid = category("small")
    sid = upload(cid, source_zip())
    initial = wait(build(sid))
    assert initial["status"] == "succeeded", initial
    symbol = tool(cid, sid, "search_symbols", query="RobotSender.send")["results"][0]
    reread = tool(cid, sid, "read_source", symbol_id=symbol["symbol_id"])["results"][0]
    assert reread["start_line"] == 2 and reread["end_line"] == 3
    assert "return format_event(event)" in reread["snippet"]
    refs = tool(cid, sid, "find_references", symbol_id=symbol["symbol_id"])
    assert any(item["path"] == "api/routes.py" for item in refs["results"])
    assert tool(cid, sid, "search_code_batch", query="sender.send")["results"]
    report["checks"]["cross_module_real_symbol_read"] = True
    report["example"] = {
        "category_id": cid,
        "snapshot_id": sid,
        "symbol": symbol,
        "source_read": reread,
    }
    report["initial_index"] = initial
    incremental = wait(build(sid))
    assert incremental["metrics_json"]["indexed_files"] == 0
    assert incremental["metrics_json"]["reused_files"] >= 7
    report["incremental_index"] = incremental

    # 实际技术文档异步解析和定位回读
    documents = []
    for name, mime, locator_key in [
        (
            "demo_waf_manual.docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "paragraph",
        ),
        ("demo_waf_manual.pdf", "application/pdf", "page"),
        (
            "demo_capabilities.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "sheet",
        ),
    ]:
        response = api.post(
            f"/api/v1/categories/{cid}/documents",
            data={"snapshot_id": sid},
            files={"file": (name, (Path("tests/fixtures/p12") / name).read_bytes(), mime)},
        )
        assert response.status_code == 202, response.text
        job = wait(response.json()["job_id"])
        assert job["status"] == "succeeded", job
        found = tool(cid, sid, "search_documents", query="")
        assert any(locator_key in item["locator"] for item in found["results"])
        documents.append({"filename": name, "job": job})
    report["documents"] = documents
    report["checks"]["docx_pdf_xlsx_real_async_parse"] = True
    denied = search.post(
        f"/api/v1/retrieval/{cid}",
        headers=read,
        params={"snapshot_id": sid},
        json={"tool": "get_project_map"},
    )
    assert denied.status_code == 403
    unpublished = search.post(
        f"/api/v1/retrieval/{cid}", headers=read, json={"tool": "get_project_map"}
    )
    assert unpublished.status_code == 409
    other_cid = category("isolation")
    other_sid = upload(other_cid, source_zip())
    assert wait(build(other_sid))["status"] == "succeeded"
    foreign = search.post(
        f"/api/v1/retrieval/{other_cid}",
        params={"snapshot_id": other_sid},
        json={"tool": "read_source", "symbol_id": symbol["symbol_id"]},
    )
    assert foreign.status_code == 404
    report["checks"]["product_snapshot_and_staging_isolation"] = True

    for phase in ["idle"]:
        for _i in range(30):
            for name, query in [
                ("search_code_batch", "sender.send"),
                ("search_symbols", "RobotSender"),
            ]:
                start = time.perf_counter()
                tool(cid, sid, name, query=query)
                raw_samples.append(
                    {
                        "phase": phase,
                        "tool": name,
                        "latency_ms": (time.perf_counter() - start) * 1000,
                    }
                )
    large_cid = category("benchmark")
    extra = int(os.environ.get("BENCH_FILES", "600"))
    large_sid = upload(large_cid, source_zip(extra))
    thread = threading.Thread(target=sampler, daemon=True)
    thread.start()
    started = time.monotonic()
    jid = build(large_sid)
    running_samples = 0
    try:
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            job = api.get(f"/api/v1/jobs/{jid}").json()
            if job["status"] in {"failed", "partial", "succeeded"}:
                break
            for name, query in [
                ("search_code_batch", "sender.send"),
                ("search_symbols", "RobotSender"),
            ]:
                start = time.perf_counter()
                tool(cid, sid, name, query=query)
                raw_samples.append(
                    {
                        "phase": "indexing" if job["status"] == "running" else "queued",
                        "tool": name,
                        "latency_ms": (time.perf_counter() - start) * 1000,
                    }
                )
                if job["status"] == "running":
                    running_samples += 1
            time.sleep(0.05)
        else:
            raise AssertionError("规模索引超时")
        assert job["status"] == "succeeded", job
        assert job["metrics_json"]["indexed_files"] == extra + 7, job
        assert running_samples > 0
    finally:
        stop.set()
        thread.join(timeout=10)
    report["benchmark_job"] = job
    report["benchmark_elapsed_seconds"] = time.monotonic() - started
    report["checks"]["retrieval_responds_while_index_running"] = running_samples
    report["latencies"] = {}
    for phase in ["idle", "indexing"]:
        report["latencies"][phase] = {}
        for name in ["search_code_batch", "search_symbols"]:
            values = [
                v["latency_ms"] for v in raw_samples if v["phase"] == phase and v["tool"] == name
            ]
            report["latencies"][phase][name] = {
                "count": len(values),
                "p50_ms": percentile(values, 0.5),
                "p95_ms": percentile(values, 0.95),
                "max_ms": round(max(values), 3) if values else None,
            }
    report["resource_sampling"] = stats
    report["raw_latency_samples"] = raw_samples
    ids = command("docker", "compose", "ps", "-q").splitlines()
    inspected = json.loads(command("docker", "inspect", *ids))
    resources = []
    for row in inspected:
        host = row["HostConfig"]
        resources.append(
            {
                "name": row["Name"],
                "cpu_limit": host["NanoCpus"] / 1e9,
                "memory_limit_bytes": host["Memory"],
                "mounts": [{"destination": m["Destination"], "rw": m["RW"]} for m in row["Mounts"]],
                "user": row["Config"]["User"],
            }
        )
    assert sum(r["cpu_limit"] for r in resources) <= 4
    assert sum(r["memory_limit_bytes"] for r in resources) <= 8 * 1024**3
    indexer = next(r for r in resources if r["name"].endswith("indexer-1"))
    assert all(not m["rw"] for m in indexer["mounts"] if m["destination"].startswith("/data"))
    assert not any(m["destination"] == "/data/archives" for m in indexer["mounts"])
    report["resources"] = resources
    report["host_engine_info"] = json.loads(command("docker", "info", "--format", "{{json .}}"))[
        "NCPU"
    ]
    report["resource_scope"] = "container quotas sum <=4 CPU and <=8GiB; not a dedicated 4C8G VM"
    report["checks"]["indexer_read_only_without_archive_mount"] = True
    report["api_metrics"] = api.get("/api/v1/admin/metrics").json()
    report["retrieval_metrics"] = search.get("/api/v1/admin/metrics").json()
    report["storage"] = api.get("/api/v1/admin/storage").json()
    ROOT.joinpath("acceptance-benchmark.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2)
    )
    summary = {
        k: v for k, v in report.items() if k not in {"resource_sampling", "raw_latency_samples"}
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
