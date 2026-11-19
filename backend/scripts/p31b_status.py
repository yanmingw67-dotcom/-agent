"""P3.1-B 验收环境状态检查：服务、健康、迁移、统一路径和 worker 心跳。"""

import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = ROOT / ".env.p31b"


def env_values():
    values = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    return values


def tag():
    result = subprocess.run(
        ["git", "-C", str(ROOT), "rev-parse", "--short=7", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def compose_env():
    env = dict(os.environ)
    env["P31B_IMAGE_TAG"] = env.get("P31B_IMAGE_TAG") or tag()
    return env


def compose(*args, check=True):
    result = subprocess.run(
        [
            "docker",
            "compose",
            "--project-name",
            "product-knowledge-p31b",
            "--env-file",
            ".env.p31b",
            "-f",
            "docker-compose.p31b.yml",
            *args,
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env=compose_env(),
    )
    if check and result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    return result.stdout.strip()


def http_status(url):
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            return response.status
    except Exception as exc:
        return f"ERROR:{type(exc).__name__}"


def main():
    env = env_values()
    api_port = env.get("P31B_API_PORT", "18190")
    ui_port = env.get("P31B_UI_PORT", "18180")
    retrieval_port = env.get("P31B_RETRIEVAL_PORT", "18191")
    checks = []
    problems = []

    def record(name, ok, detail):
        checks.append({"name": name, "ok": bool(ok), "detail": detail})
        if not ok:
            problems.append(f"{name}: {detail}")

    print("== Compose 服务 ==")
    try:
        raw = compose("ps", "--format", "json")
        rows = json.loads(raw) if raw.startswith("[") else [
            json.loads(line) for line in raw.splitlines() if line.strip().startswith("{")
        ]
    except Exception as exc:
        rows = []
        record("compose_ps", False, str(exc))
    by_service = {}
    for row in rows:
        service = row.get("Service") or row.get("Name", "")
        by_service[service] = row
        print(
            f"{service:12} state={row.get('State',''):10} "
            f"health={row.get('Health','') or '-'}"
        )
    required = {
        "api",
        "worker",
        "indexer",
        "builder",
        "taskworker",
        "retrieval",
        "frontend",
        "postgres",
        "redis",
    }
    missing = sorted(required - set(by_service))
    record("services_present", not missing, f"missing={missing}")
    unhealthy = sorted(
        name
        for name, row in by_service.items()
        if row.get("State") not in {"running", "Running"}
        or row.get("Health") in {"unhealthy", "starting"}
    )
    record("services_running", not unhealthy, f"unhealthy={unhealthy}")

    print("== HTTP 健康 ==")
    for name, url in {
        "api_live": f"http://127.0.0.1:{api_port}/health/live",
        "api_ready": f"http://127.0.0.1:{api_port}/health/ready",
        "retrieval_ready": f"http://127.0.0.1:{retrieval_port}/health/ready",
        "frontend": f"http://127.0.0.1:{ui_port}/",
    }.items():
        status = http_status(url)
        print(f"{name:18} {status}")
        record(name, status == 200, str(status))

    print("== 数据库迁移 ==")
    try:
        head = compose("exec", "-T", "api", "alembic", "heads").split()[0]
        version = compose(
            "exec", "-T", "postgres", "psql", "-U", "knowledge", "-d", "knowledge_p31b",
            "-Atc", "select version_num from alembic_version",
        )
        print(f"alembic_head={head} database_version={version}")
        record("migration_current", head == version, f"head={head} db={version}")
    except Exception as exc:
        record("migration_current", False, str(exc))

    print("== 容器存储路径 ==")
    path_code = (
        "import json; from app.core.config import get_settings as g; s=g(); "
        "print(json.dumps({'ingestion':str(s.ingestion_root),'source':str(s.source_root),"
        "'documents':str(s.document_store),'archive':str(s.archive_root),"
        "'control':str(s.control_store),"
        "'same':s.source_root.resolve()==s.ingestion_root.resolve(),"
        "'symlink':s.source_root.is_symlink(),'source_dir':s.source_root.is_dir()}))"
    )
    for service in ["api", "worker", "indexer", "builder", "retrieval", "taskworker"]:
        try:
            payload = json.loads(compose("exec", "-T", service, "python", "-c", path_code))
            ok = payload["same"] and not payload["symlink"] and payload["source_dir"]
            print(f"{service:12} {payload}")
            record(f"paths_{service}", ok, json.dumps(payload, ensure_ascii=False))
        except Exception as exc:
            record(f"paths_{service}", False, str(exc))

    print("== worker 心跳 ==")
    heartbeat_code = (
        "import time,pathlib; p=pathlib.Path('{path}'); "
        "print('missing' if not p.exists() else round(time.time()-float(p.read_text()),1))"
    )
    for service, path in {
        "worker": "/tmp/knowledge-worker-heartbeat",
        "indexer": "/tmp/knowledge-worker-heartbeat",
        "builder": "/tmp/knowledge-worker-heartbeat",
        "taskworker": "/tmp/knowledge-taskworker-heartbeat",
    }.items():
        try:
            value = compose(
                "exec", "-T", service, "python", "-c", heartbeat_code.format(path=path)
            )
            age = float(value) if value != "missing" else 999999
            print(f"{service:12} age_seconds={value}")
            record(f"heartbeat_{service}", age < 120, str(value))
        except Exception as exc:
            record(f"heartbeat_{service}", False, str(exc))

    print("== 汇总 ==")
    failed = [item for item in checks if not item["ok"]]
    print(f"checks={len(checks)} failed={len(failed)}")
    if problems:
        for problem in problems:
            print("FAIL " + problem)
        return 1
    print("P31B_ENV_STATUS=PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
