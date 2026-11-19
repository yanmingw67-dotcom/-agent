"""P3.1-B Gate 2：真实知识构建、审核、发布所需输入与授权核查。

只读管理员 API 与环境变量，不上传任何资料、不调用模型。真实源码/文档按
“非合成样例哈希且已完成索引/解析”判定；没有真实发布物时 Gate 2 必须 BLOCKED。
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = ROOT / ".env.p31b"
EVIDENCE = ROOT / "docs" / "acceptance" / "p31b_gate2_evidence.json"
PROJECT = "product-knowledge-p31b"

# 验收夹具的固定哈希；只用于排除合成样例，不代表任何真实产品资料。
SYNTHETIC_SOURCE_SHA256 = {
    "d082d172f6a6e7d39d36809625a94ef278a6de7d65df718bc247d0c1bc55c65e",
}
SYNTHETIC_DOCUMENT_SHA256 = {
    "fda28d7f561e47ff1aa9d7584d0b6322f520bc87cab06257901c6dd06f076d39",
}


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


def collect_real_inputs(client, admin):
    """遍历品类与快照，区分合成样例和真实资料，不输出文件名。"""
    categories = client.get("/api/v1/admin/categories", headers=admin)
    categories.raise_for_status()
    items = categories.json()["items"]
    observed = {
        "categories": len(items),
        "real_source_snapshots": [],
        "real_documents": [],
        "publications": [],
        "semantic_discoveries": [],
        "model_authorization": None,
    }
    seen_documents = set()
    seen_discoveries = set()
    for item in items:
        workspace = client.get(f"/api/v1/admin/categories/{item['id']}", headers=admin)
        workspace.raise_for_status()
        detail = workspace.json()
        observed["model_authorization"] = detail["model_authorization"]
        for snapshot in detail["snapshots"]:
            if (
                snapshot["derivation_type"] is None
                and snapshot["index_status"] == "indexed"
                and snapshot["source_sha256"] not in SYNTHETIC_SOURCE_SHA256
            ):
                observed["real_source_snapshots"].append(
                    {
                        "category_code": item["code"],
                        "snapshot_id": snapshot["id"],
                        "sha256": snapshot["source_sha256"],
                    }
                )
        for snapshot in detail["snapshots"]:
            if snapshot["status"] not in {"extracted", "published"}:
                continue
            snapshot_detail = client.get(
                f"/api/v1/admin/categories/{item['id']}",
                headers=admin,
                params={"snapshot_id": snapshot["id"]},
            )
            snapshot_detail.raise_for_status()
            row = snapshot_detail.json()
            for document in row["documents"]:
                digest = document["sha256"]
                if (
                    document["parse_status"] == "parsed"
                    and digest not in SYNTHETIC_DOCUMENT_SHA256
                    and digest not in seen_documents
                ):
                    seen_documents.add(digest)
                    observed["real_documents"].append(
                        {
                            "category_code": item["code"],
                            "snapshot_id": snapshot["id"],
                            "sha256": digest,
                            "segments": document["segment_count"],
                        }
                    )
            discovery = row.get("document_discovery")
            if (
                discovery
                and discovery.get("status") == "succeeded"
                and discovery.get("mode") != "structural"
                and discovery.get("run_id") not in seen_discoveries
            ):
                seen_discoveries.add(discovery["run_id"])
                observed["semantic_discoveries"].append(
                    {
                        "category_code": item["code"],
                        "snapshot_id": snapshot["id"],
                        "run_id": discovery["run_id"],
                        "mode": discovery["mode"],
                    }
                )
        publication = detail.get("active_publication")
        if publication and publication.get("capability_count", 0) > 0:
            observed["publications"].append(
                {
                    "category_code": item["code"],
                    "publication_id": publication["publication_id"],
                    "capability_count": publication["capability_count"],
                }
            )
    return observed


def main():
    env = env_values()
    admin = {"Authorization": f"Bearer {env['ADMIN_TOKEN']}"}
    port = env.get("P31B_API_PORT", "18190")
    evidence = {
        "gate": "P3.1-B Gate 2",
        "mode": "REAL_INPUT_AND_AUTHORIZATION_CHECK",
        "build_commit": tag(),
        "result": "BLOCKED",
        "checks": {},
        "missing_inputs": [],
        "blockers": [],
        "observed": {},
    }

    def record(name, status, detail):
        evidence["checks"][name] = {"result": status, "detail": detail}
        print(f"{status} {name} {detail}")

    with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=60) as client:
        ready = client.get("/health/ready")
        record("api_ready", "PASS" if ready.status_code == 200 else "FAIL", str(ready.status_code))
        observed = collect_real_inputs(client, admin)
        evidence["observed"] = observed
        auth = observed["model_authorization"] or {}
        builder = auth.get("builder", {})
        evidence["model_authorization"] = auth
        configured = bool(builder.get("model_configured"))
        allow_enterprise = bool(builder.get("allow_enterprise"))
        allow_synthetic = bool(builder.get("allow_synthetic"))
        record(
            "model_configured",
            "PASS" if configured else "BLOCKED",
            f"model_configured={configured}",
        )
        record(
            "enterprise_authorization",
            "PASS" if configured and allow_enterprise else "BLOCKED",
            f"model_configured={configured} allow_enterprise={allow_enterprise}",
        )
        record(
            "synthetic_egress_disabled",
            "PASS" if not allow_synthetic else "FAIL",
            f"allow_synthetic={allow_synthetic}",
        )
        record(
            "frontend_cannot_override_authorization",
            "PASS" if not auth.get("frontend_can_override") else "FAIL",
            f"frontend_can_override={auth.get('frontend_can_override')}",
        )

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
    record(
        "container_synthetic_egress_disabled",
        "PASS" if len(values) > 1 and values[1] == "false" else "FAIL",
        f"BUILDER_ALLOW_SYNTHETIC={values[1] if len(values) > 1 else ''}",
    )

    sources = observed["real_source_snapshots"]
    documents = observed["real_documents"]
    discoveries = observed["semantic_discoveries"]
    publications = observed["publications"]
    record(
        "real_source_indexed",
        "PASS" if sources else "BLOCKED",
        f"count={len(sources)}",
    )
    record(
        "real_document_parsed",
        "PASS" if documents else "BLOCKED",
        f"count={len(documents)}",
    )
    record(
        "semantic_discovery_succeeded",
        "PASS" if discoveries else "BLOCKED",
        f"count={len(discoveries)}",
    )
    record(
        "real_publication_present",
        "PASS" if publications else "BLOCKED",
        f"count={len(publications)}",
    )

    if not sources:
        evidence["missing_inputs"].append("未由管理员上传真实 WAF 源码 ZIP 并完成索引")
    if not documents:
        evidence["missing_inputs"].append("未由管理员上传正式技术文档并完成解析")
    if not configured:
        evidence["missing_inputs"].append("服务器侧模型未配置")
    elif not allow_enterprise:
        evidence["missing_inputs"].append("企业数据模型外发授权未开启")
    elif not discoveries:
        evidence["missing_inputs"].append("模型授权已就绪但尚未完成非 structural 文档发现")
    if not publications:
        evidence["missing_inputs"].append("尚无包含能力的真实知识发布物")
    for item in evidence["missing_inputs"]:
        print("BLOCKED missing_input " + item)

    results = [row["result"] for row in evidence["checks"].values()]
    if "FAIL" in results:
        evidence["result"] = "FAIL"
    elif all(result == "PASS" for result in results):
        evidence["result"] = "PASS"
    else:
        evidence["result"] = "BLOCKED"
    record("gate2_overall", evidence["result"], "真实输入、模型授权、审核发布必须全部就绪")
    evidence["blockers"] = [
        "真实知识构建需要真实源码与正式文档，不能以合成样例替代。",
        "模型未配置或企业外发授权未完成时，模型步骤必须保持 BLOCKED。",
        "没有真实发布物时不能进入审核发布验收，也不能宣称知识库已就绪。",
    ]
    EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
    EVIDENCE.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"P31B_GATE2={evidence['result']} evidence={EVIDENCE}")
    return 0 if evidence["result"] in {"PASS", "BLOCKED"} else 1


if __name__ == "__main__":
    sys.exit(main())
