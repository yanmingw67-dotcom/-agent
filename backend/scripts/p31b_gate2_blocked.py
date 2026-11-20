"""P3.1-B Gate 2：真实资料来源、模型授权、审核发布与派生链核验。

只读管理员 API 与容器环境，不上传资料、不调用模型。是否属于真实资料不再由
“哈希不在合成集合”单独判定，而必须同时具备管理员来源确认、适用范围、当前
模型端点授权记录，以及源码/文档/发现/发布物的同品类合法派生链。
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import httpx

try:
    from scripts.p31b_gate2_verify import evaluate
except ImportError:  # 直接执行 scripts/p31b_gate2_blocked.py 时的导入路径
    from p31b_gate2_verify import evaluate

ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = ROOT / ".env.p31b"
EVIDENCE = ROOT / "docs" / "acceptance" / "p31b_gate2_evidence.json"
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


def compose(*args):
    environment = dict(os.environ)
    environment["P31B_IMAGE_TAG"] = environment.get("P31B_IMAGE_TAG") or tag()
    result = subprocess.run(
        [
            "docker",
            "compose",
            "--project-name",
            PROJECT,
            "--env-file",
            ".env.p31b",
            "-f",
            "docker-compose.p31b.yml",
            *args,
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env=environment,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    return result.stdout.strip()


def sanitize_auth(auth):
    builder = auth.get("builder") or {}
    active = builder.get("active_authorization") or {}
    return {
        "server_side_authorization_required": auth.get("server_side_authorization_required"),
        "frontend_can_override": auth.get("frontend_can_override"),
        "authorization_source": auth.get("authorization_source"),
        "env_flags_are_approval": auth.get("env_flags_are_approval"),
        "builder": {
            "model_configured": builder.get("model_configured"),
            "allow_synthetic": builder.get("allow_synthetic"),
            "allow_enterprise": builder.get("allow_enterprise"),
            "status": builder.get("status"),
            "reason_code": builder.get("reason_code"),
            "active_authorization": {
                "id": active.get("id"),
                "data_classification": active.get("data_classification"),
                "status": active.get("status"),
                "active": active.get("active"),
                "expires_at": active.get("expires_at"),
                "approval_reference_present": bool(active.get("approval_reference")),
            }
            if active
            else None,
        },
    }


def sanitize_provenance(row):
    return {
        "id": row.get("id"),
        "snapshot_id": row.get("snapshot_id"),
        "document_id": row.get("document_id"),
        "material_type": row.get("material_type"),
        "content_sha256": row.get("content_sha256"),
        "data_classification": row.get("data_classification"),
        "status": row.get("status"),
        "has_declared_origin": bool(row.get("declared_origin")),
        "has_usage_scope": bool(row.get("usage_scope")),
        "has_confirmation_reference": bool(row.get("confirmation_reference")),
        "confirmed_by": row.get("confirmed_by"),
    }


def snapshot_row(row):
    return {
        "id": row.get("id"),
        "category_id": row.get("category_id"),
        "status": row.get("status"),
        "index_status": row.get("index_status"),
        "source_sha256": row.get("source_sha256"),
        "derived_from_snapshot_id": row.get("derived_from_snapshot_id"),
        "derivation_type": row.get("derivation_type"),
        "data_classification": row.get("data_classification"),
    }


def collect_real_inputs(client, admin):
    response = client.get("/api/v1/admin/categories", headers=admin)
    response.raise_for_status()
    items = response.json()["items"]
    observed = {"categories": [], "model_authorization": None}
    for item in items:
        workspace = client.get(f"/api/v1/admin/categories/{item['id']}", headers=admin)
        workspace.raise_for_status()
        base = workspace.json()
        observed["model_authorization"] = sanitize_auth(base["model_authorization"])
        provenance = client.get(
            f"/api/v1/admin/categories/{item['id']}/material-provenance", headers=admin
        )
        provenance.raise_for_status()
        snapshots = {row["id"]: snapshot_row(row) for row in base["snapshots"]}
        publication = base.get("active_publication")
        if publication and publication["snapshot_id"] not in snapshots:
            detail = client.get(
                f"/api/v1/admin/categories/{item['id']}",
                headers=admin,
                params={"snapshot_id": publication["snapshot_id"]},
            )
            detail.raise_for_status()
            working = detail.json().get("working_snapshot")
            if working:
                snapshots[working["id"]] = snapshot_row(working)
        documents = {}
        discoveries = []
        chains = {}
        for snapshot_id in list(snapshots):
            detail = client.get(
                f"/api/v1/admin/categories/{item['id']}",
                headers=admin,
                params={"snapshot_id": snapshot_id},
            )
            detail.raise_for_status()
            body = detail.json()
            for document in body["documents"]:
                documents[document["id"]] = {
                    "id": document["id"],
                    "snapshot_id": document["snapshot_id"],
                    "sha256": document["sha256"],
                    "parse_status": document["parse_status"],
                    "segment_count": document["segment_count"],
                    "data_classification": document.get("data_classification"),
                }
            discovery = body.get("document_discovery")
            if discovery:
                discoveries.append(
                    {
                        "run_id": discovery.get("run_id"),
                        "snapshot_id": snapshot_id,
                        "status": discovery.get("status"),
                        "mode": discovery.get("mode"),
                        "error_code": discovery.get("error_code"),
                    }
                )
            chain = client.get(
                f"/api/v1/admin/snapshots/{snapshot_id}/derivation-chain", headers=admin
            )
            chain.raise_for_status()
            chains[snapshot_id] = chain.json()
        if publication and publication["snapshot_id"] not in chains:
            chain = client.get(
                f"/api/v1/admin/snapshots/{publication['snapshot_id']}/derivation-chain",
                headers=admin,
            )
            chain.raise_for_status()
            chains[publication["snapshot_id"]] = chain.json()
        observed["categories"].append(
            {
                "id": item["id"],
                "code": item["code"],
                "snapshots": list(snapshots.values()),
                "provenance": [sanitize_provenance(row) for row in provenance.json()["items"]],
                "documents": list(documents.values()),
                "discoveries": discoveries,
                "publication": publication,
                "chains": chains,
            }
        )
    return observed


def main():
    env = env_values()
    admin = {"Authorization": f"Bearer {env['ADMIN_TOKEN']}"}
    port = env.get("P31B_API_PORT", "18190")
    evidence = {
        "gate": "P3.1-B Gate 2",
        "mode": "PROVENANCE_AND_AUTHORIZATION_CHECK",
        "build_commit": tag(),
        "result": "BLOCKED",
        "checks": {},
        "missing_inputs": [],
        "violations": [],
        "blockers": [],
        "observed": {},
    }

    with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=60) as client:
        ready = client.get("/health/ready")
        evidence["checks"]["api_ready"] = {
            "result": "PASS" if ready.status_code == 200 else "FAIL",
            "detail": str(ready.status_code),
        }
        print(f"{evidence['checks']['api_ready']['result']} api_ready {ready.status_code}")
        observed = collect_real_inputs(client, admin)
        evidence["observed"] = observed

    values = compose(
        "exec",
        "-T",
        "api",
        "printenv",
        "BUILDER_ALLOW_ENTERPRISE",
        "BUILDER_ALLOW_SYNTHETIC",
        "BUILDER_MODEL",
    ).splitlines()
    evidence["container_environment"] = {
        "BUILDER_ALLOW_ENTERPRISE": values[0] if values else "",
        "BUILDER_ALLOW_SYNTHETIC": values[1] if len(values) > 1 else "",
        "BUILDER_MODEL_SET": bool(values[2]) if len(values) > 2 else False,
    }
    synthetic_ok = len(values) > 1 and values[1] == "false"
    evidence["checks"]["container_synthetic_egress_disabled"] = {
        "result": "PASS" if synthetic_ok else "FAIL",
        "detail": f"BUILDER_ALLOW_SYNTHETIC={values[1] if len(values) > 1 else ''}",
    }
    print(
        f"{evidence['checks']['container_synthetic_egress_disabled']['result']} "
        f"container_synthetic_egress_disabled "
        f"{evidence['checks']['container_synthetic_egress_disabled']['detail']}"
    )

    result = evaluate(observed)
    evidence["checks"].update(result["checks"])
    evidence["missing_inputs"] = result["missing_inputs"]
    evidence["violations"] = result["violations"]
    evidence["result"] = result["result"]
    evidence["qualifying_categories"] = result["qualifying_categories"]
    evidence["counts"] = result["counts"]
    for name, row in evidence["checks"].items():
        if name in {"api_ready", "container_synthetic_egress_disabled"}:
            continue
        print(f"{row['result']} {name} {row['detail']}")
    for item in evidence["missing_inputs"]:
        print("BLOCKED missing_input " + item)
    for item in evidence["violations"]:
        print("FAIL provenance_violation " + item)
    evidence["blockers"] = [
        "真实知识构建需要管理员确认来源与适用范围，不能以合成样例或哈希推断替代。",
        "源码、技术文档、文档发现运行与发布物必须属于同一品类和合法快照派生链。",
        "模型未配置或没有有效数据库授权记录时，模型步骤必须保持 BLOCKED。",
        "没有真实发布物时不能进入审核发布验收，也不能宣称知识库已就绪。",
    ]
    EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
    EVIDENCE.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"P31B_GATE2={evidence['result']} evidence={EVIDENCE}")
    return 0 if evidence["result"] in {"PASS", "BLOCKED"} else 1


if __name__ == "__main__":
    sys.exit(main())
