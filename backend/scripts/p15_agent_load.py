"""在真实 Agent A 运行时（FunctionModel）下测量 Probe 的 P50/P95/错误率。

流程：停止真实 builder → 在未发布快照上排队 → 一次性 builder 容器执行
run_builder(FunctionModel) → 同时压测 Probe → 恢复 builder。全程不调用真实模型，
不产生 Token；作业记录为 TEST_FUNCTION_MODEL。
"""

import argparse
import json
import os
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import httpx
from dotenv import dotenv_values

ROOT = Path("reports/p15")
ACCEPTANCE = ROOT / "p15-acceptance-compose.json"
P13 = Path("reports/p13-autonomous/real-compact-final.json")


def percentile(values, fraction):
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(len(ordered) * fraction))] if ordered else None


def summarize(values):
    if not values:
        return {"count": 0}
    return {
        "count": len(values),
        "min_ms": round(min(values), 3),
        "p50_ms": round(percentile(values, 0.50), 3),
        "p95_ms": round(percentile(values, 0.95), 3),
        "p99_ms": round(percentile(values, 0.99), 3),
        "max_ms": round(max(values), 3),
    }


def stats_rows():
    output = subprocess.check_output(
        ["docker", "stats", "--no-stream", "--format", "{{json .}}"], text=True
    )
    rows = {}
    for line in output.splitlines():
        if not line.strip():
            continue
        item = json.loads(line)
        name = item.get("Name", "")
        if any(
            part in name
            for part in ("builder-run", "builder-1", "retrieval-1", "api-1", "postgres-1")
        ):
            rows[name] = {
                "cpu_percent": item.get("CPUPerc"),
                "memory": item.get("MemUsage"),
                "pids": item.get("PIDs"),
            }
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", default="agent-load")
    parser.add_argument("--samples", type=int, default=160)
    parser.add_argument("--concurrency", type=int, default=4)
    args = parser.parse_args()
    destination = ROOT / f"p15-probe-agent-load-{args.label}.json"
    assert not destination.exists(), "不可覆盖既有 Agent 负载基线"
    acceptance = json.loads(ACCEPTANCE.read_text())
    cid = acceptance["fresh_ingestion"]["category_id"]
    sid = acceptance["fresh_ingestion"]["snapshot_id"]
    published = json.loads(P13.read_text())
    probe_cid = published["category_id"]
    probe_sid = published["snapshot_id"]
    env = dotenv_values(".env")
    api = httpx.Client(
        base_url="http://127.0.0.1:8000",
        headers={"Authorization": "Bearer " + env["ADMIN_TOKEN"]},
        timeout=60,
    )
    probe = httpx.Client(
        base_url="http://127.0.0.1:8001",
        headers={"Authorization": "Bearer " + env["READ_TOKEN"]},
        timeout=30,
    )
    payload = {"category_id": probe_cid, "requirement_text": "事件 预览 模式 分发 筛选"}
    report = {
        "mode": "P1.5_PROBE_DURING_FUNCTION_MODEL_BUILDER_RUNTIME",
        "builder_category_id": cid,
        "builder_snapshot_id": sid,
        "probe_category_id": probe_cid,
        "probe_snapshot_id": probe_sid,
        "model_calls_this_phase": 0,
        "cost_this_phase_usd": 0,
        "builder_model_mode": "TEST_FUNCTION_MODEL",
        "note": "FunctionModel驱动真实Agent A运行时；不是真实模型能力验收",
        "steps": [],
    }

    def save():
        destination.write_text(json.dumps(report, ensure_ascii=False, indent=2))

    def one(index):
        started = time.perf_counter()
        try:
            response = probe.post("/api/v1/probes", json=payload)
            return {
                "index": index,
                "status": response.status_code,
                "client_ms": (time.perf_counter() - started) * 1000,
                "server_ms": response.json().get("duration_ms")
                if response.status_code == 200
                else None,
                "error": None if response.status_code == 200 else response.text[:200],
            }
        except Exception as error:
            return {
                "index": index,
                "status": None,
                "client_ms": (time.perf_counter() - started) * 1000,
                "server_ms": None,
                "error": repr(error)[:200],
            }

    builder = None
    builder_log = ROOT / f"p15-agent-load-{args.label}.log"
    builder_stopped = False
    try:
        # 选一个已索引但未发布的模块，确保不会触发真实 builder worker。
        project = api.post(
            f"/api/v1/retrieval/{cid}",
            params={"snapshot_id": sid},
            json={"tool": "get_project_map"},
        )
        assert project.status_code == 200, project.text
        modules = project.json()["results"]
        chosen_modules = [module for module in modules if module.get("name") in {"api", "storage"}][
            :2
        ]
        if not chosen_modules:
            chosen_modules = [modules[0]]
        report["modules"] = [
            {"name": module.get("name"), "id": module["module_id"]} for module in chosen_modules
        ]
        approved = api.post(f"/api/v1/snapshots/{sid}/approve-public-fixture")
        assert approved.status_code == 200, approved.text
        report["classification"] = approved.json().get("data_classification")

        subprocess.check_call(["docker", "compose", "stop", "builder"], stdout=subprocess.DEVNULL)
        builder_stopped = True
        for _ in range(20):
            running = subprocess.check_output(
                ["docker", "compose", "ps", "--status", "running", "-q", "builder"], text=True
            ).strip()
            if not running:
                break
            time.sleep(0.5)
        assert not running, "Builder 未能停止，拒绝与真实 worker 争抢任务"
        queued = api.post(
            f"/api/v1/snapshots/{sid}/investigate",
            json={
                "module_ids": [module["module_id"] for module in chosen_modules],
                "data_classification": "synthetic_public",
                "investigation_mode": "autonomous",
                "capability_limit": 1,
                "include_equivalence": False,
                "exploration_max_fragments": 4,
            },
        )
        assert queued.status_code == 202, queued.text
        job_id = queued.json()["job_id"]
        token = "p15-" + uuid4().hex
        # RQ worker 可能在 enqueue 后瞬间领取任务；Builder 已确认停止且尚未调用模型时，
        # 将任务重置为 queued 并注入本地令牌，交给一次性 FunctionModel 容器执行。
        claimed = (
            subprocess.check_output(
                [
                    "docker",
                    "compose",
                    "exec",
                    "-T",
                    "postgres",
                    "psql",
                    "-U",
                    "knowledge",
                    "-d",
                    "knowledge",
                    "-tAc",
                    "update knowledge_jobs set dispatch_token='"
                    + token
                    + "', status='queued', stage='module_planning', started_at=null,"
                    + " finished_at=null, error_code=null where id='"
                    + job_id
                    + "' and coalesce(metrics_json->>'model_requests','0')='0'"
                    + " returning dispatch_token",
                ],
                text=True,
            )
            .strip()
            .splitlines()[0]
        )
        assert claimed == token, "任务已被真实模型执行，拒绝注入 FunctionModel"
        report["job_id"] = job_id
        save()

        with builder_log.open("w") as log:
            builder_env = os.environ.copy()
            builder_env["P15_JOB_TOKEN"] = token
            builder = subprocess.Popen(
                [
                    "docker",
                    "compose",
                    "run",
                    "--rm",
                    "--no-deps",
                    "-v",
                    f"{Path.cwd()}/scripts:/app/scripts:ro",
                    "-e",
                    "PYTHONPATH=/app",
                    "-e",
                    "P15_JOB_TOKEN",
                    "builder",
                    "python",
                    "/app/scripts/p15_builder_functional_load.py",
                    "--job-id",
                    job_id,
                ],
                stdout=log,
                stderr=subprocess.STDOUT,
                env=builder_env,
            )
            for _ in range(40):
                state = api.get(f"/api/v1/jobs/{job_id}").json()
                if state["status"] == "running":
                    break
                assert state["status"] not in {"failed", "partial", "succeeded"}, (
                    "Builder 任务未进入运行状态",
                    state["status"],
                    state["error_code"],
                )
                time.sleep(0.2)
            else:
                raise AssertionError("Builder 任务未在预期时间内进入运行状态")
            report["job_started_before_probe"] = True
            report["resources_during"] = stats_rows()
            started = time.monotonic()
            with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
                rows = list(pool.map(one, range(args.samples)))
            wall = time.monotonic() - started
            builder.wait(timeout=180)
        successful = [r for r in rows if r["status"] == 200]
        report["probe"] = {
            "requests": args.samples,
            "concurrency": args.concurrency,
            "wall_s": round(wall, 3),
            "throughput_rps": round(args.samples / wall, 2) if wall else None,
            "status_counts": {
                str(code): sum(1 for r in rows if r["status"] == code)
                for code in sorted({r["status"] for r in rows}, key=lambda x: str(x))
            },
            "errors": [r["error"] for r in rows if r["error"]][:5],
            "client": summarize([r["client_ms"] for r in successful]),
            "server": summarize([r["server_ms"] for r in successful if r["server_ms"] is not None]),
        }
        final = api.get(f"/api/v1/jobs/{job_id}")
        assert final.status_code == 200, final.text
        job = final.json()
        job_metrics = job["metrics_json"]
        report["job"] = {
            "status": job["status"],
            "stage": job["stage"],
            "error_code": job["error_code"],
            "model_mode": job_metrics.get("model_mode"),
            "model_requests": job_metrics.get("model_requests"),
            "tool_calls": job_metrics.get("tool_calls"),
            "duration_ms": job_metrics.get("duration_ms"),
            "peak_cpu_percent": job_metrics.get("sampled_peak_cpu_percent"),
            "peak_rss_bytes": job_metrics.get("sampled_peak_rss_bytes"),
        }
        report["job_status"] = job["status"]
        report["builder_returncode"] = builder.returncode
        report["builder_log"] = builder_log.read_text()[-2000:]
        errors = args.samples - report["probe"]["status_counts"].get("200", 0)
        report["summary"] = {
            "errors": errors,
            "p95_ms": report["probe"]["client"].get("p95_ms"),
            "job_status": job["status"],
            "function_model": True,
        }
        report["passed"] = (
            errors == 0
            and builder.returncode == 0
            and job["status"] in {"succeeded", "partial"}
            and job_metrics.get("model_mode") == "TEST_FUNCTION_MODEL"
        )
        save()
        print(
            json.dumps(
                {
                    "passed": report["passed"],
                    "summary": report["summary"],
                    "output": str(destination),
                },
                ensure_ascii=False,
            )
        )
        assert report["passed"]
    except Exception:
        report["passed"] = False
        save()
        raise
    finally:
        if builder is not None and builder.poll() is None:
            builder.terminate()
        if builder_stopped:
            subprocess.run(["docker", "compose", "start", "builder"], check=False)
        api.close()
        probe.close()


if __name__ == "__main__":
    main()
