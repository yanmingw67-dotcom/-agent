"""P1.5 Probe 性能基线：空闲与 builder 容器合成负载下的 P50/P95/错误率。

只读公开样例发布；不调用模型。Compose cgroup 配额不等于独立 4C8G 虚拟机，
报告必须保留该边界说明。
"""

import argparse
import json
import statistics
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
from dotenv import dotenv_values

ROOT = Path("reports/p15")
P13 = Path("reports/p13-autonomous/real-compact-final.json")


def percentile(values, fraction):
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(len(ordered) * fraction))]


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
        "mean_ms": round(statistics.fmean(values), 3),
    }


def docker_rows():
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
            for part in ("api-1", "retrieval-1", "builder-1", "builder-run", "postgres-1")
        ):
            rows[name] = {
                "cpu_percent": item.get("CPUPerc"),
                "memory": item.get("MemUsage"),
                "memory_percent": item.get("MemPerc"),
                "pids": item.get("PIDs"),
            }
    return rows


def limits(service):
    container = (
        subprocess.check_output(["docker", "compose", "ps", "-q", service], text=True)
        .strip()
        .splitlines()[0]
    )
    config = json.loads(
        subprocess.check_output(
            ["docker", "inspect", container, "--format", "{{json .HostConfig}}"], text=True
        )
    )
    return {
        "nano_cpus": config.get("NanoCpus"),
        "memory_bytes": config.get("Memory"),
        "pids_limit": config.get("PidsLimit"),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", default="compose")
    parser.add_argument("--samples", type=int, default=120)
    parser.add_argument("--load-samples", type=int, default=160)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--load-seconds", type=float, default=45)
    args = parser.parse_args()
    destination = ROOT / f"p15-probe-benchmark-{args.label}.json"
    assert not destination.exists(), "不可覆盖既有性能基线"
    source = json.loads(P13.read_text())
    category_id = source["category_id"]
    env = dotenv_values(".env")
    client = httpx.Client(
        base_url="http://127.0.0.1:8001",
        headers={"Authorization": "Bearer " + env["READ_TOKEN"]},
        timeout=30,
    )
    payload = {
        "category_id": category_id,
        "requirement_text": "事件 预览 模式 分发 筛选",
    }

    def one(index):
        started = time.perf_counter()
        try:
            response = client.post("/api/v1/probes", json=payload)
            elapsed = (time.perf_counter() - started) * 1000
            body = response.json() if response.status_code == 200 else None
            return {
                "index": index,
                "status": response.status_code,
                "client_ms": elapsed,
                "server_ms": body.get("duration_ms") if body else None,
                "bytes": len(response.content),
                "publication_id": body.get("publication_id") if body else None,
                "error": None if response.status_code == 200 else response.text[:200],
            }
        except Exception as error:
            return {
                "index": index,
                "status": None,
                "client_ms": (time.perf_counter() - started) * 1000,
                "server_ms": None,
                "bytes": 0,
                "publication_id": None,
                "error": repr(error)[:200],
            }

    def phase(name, count, workers):
        started = time.monotonic()
        with ThreadPoolExecutor(max_workers=workers) as pool:
            rows = list(pool.map(one, range(count)))
        wall = time.monotonic() - started
        successful = [r for r in rows if r["status"] == 200]
        summary = {
            "requests": count,
            "concurrency": workers,
            "wall_s": round(wall, 3),
            "throughput_rps": round(count / wall, 2) if wall else None,
            "status_counts": {
                str(code): sum(1 for r in rows if r["status"] == code)
                for code in sorted({r["status"] for r in rows}, key=lambda x: str(x))
            },
            "errors": [r["error"] for r in rows if r["error"]][:5],
            "client": summarize([r["client_ms"] for r in successful]),
            "server": summarize([r["server_ms"] for r in successful if r["server_ms"] is not None]),
            "bytes": summarize([r["bytes"] for r in successful]),
            "publication_ids": sorted({r["publication_id"] for r in successful}),
        }
        return summary

    report = {
        "mode": "P1.5_PROBE_COMPOSE_BENCHMARK_SYNTHETIC_BUILDER_LOAD",
        "category_id": category_id,
        "requirement_text": payload["requirement_text"],
        "model_calls_this_phase": 0,
        "cost_this_phase_usd": 0,
        "environment": {
            "api_limits": limits("api"),
            "retrieval_limits": limits("retrieval"),
            "builder_limits": limits("builder"),
            "postgres_limits": limits("postgres"),
            "note": "Docker Compose cgroup配额；不是独立4C8G虚拟机实测",
        },
        "load_mode": "builder容器内CPU自旋+PostgreSQL只读计数，非真实Agent A模型任务",
        "phases": {},
        "resources": {},
        "passed": False,
    }
    destination.parent.mkdir(parents=True, exist_ok=True)

    def save():
        destination.write_text(json.dumps(report, ensure_ascii=False, indent=2))

    load = None
    load_log = ROOT / f"p15-builder-load-{args.label}.log"
    try:
        for _ in range(10):
            assert one(-1)["status"] == 200
        report["resources"]["before"] = docker_rows()
        report["phases"]["idle"] = phase("idle", args.samples, 1)
        report["phases"]["idle_concurrent"] = phase(
            "idle_concurrent", args.load_samples, args.concurrency
        )
        save()

        with load_log.open("w") as log:
            load = subprocess.Popen(
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
                    "builder",
                    "python",
                    "/app/scripts/p15_builder_load.py",
                    "--duration",
                    str(args.load_seconds),
                ],
                stdout=log,
                stderr=subprocess.STDOUT,
            )
            time.sleep(3)
            assert load.poll() is None, "builder合成负载提前退出"
            report["resources"]["during_load"] = docker_rows()
            report["phases"]["under_builder_load"] = phase(
                "under_builder_load", args.load_samples, args.concurrency
            )
            report["phases"]["under_builder_load_sequential"] = phase(
                "under_builder_load_sequential", args.samples, 1
            )
            save()
            load.wait(timeout=args.load_seconds + 60)
        report["load"] = {
            "returncode": load.returncode,
            "log": load_log.read_text()[-2000:],
            "duration_s": args.load_seconds,
        }
        report["resources"]["after"] = docker_rows()
        errors = sum(
            value["requests"] - value["status_counts"].get("200", 0)
            for value in report["phases"].values()
        )
        p95 = report["phases"]["under_builder_load"]["client"]["p95_ms"]
        report["summary"] = {
            "errors": errors,
            "load_p95_ms": p95,
            "load_p95_under_3000ms": p95 is not None and p95 < 3000,
        }
        report["passed"] = (
            errors == 0
            and report["load"]["returncode"] == 0
            and report["summary"]["load_p95_under_3000ms"]
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
        if load is not None and load.poll() is None:
            load.terminate()
        client.close()


if __name__ == "__main__":
    main()
