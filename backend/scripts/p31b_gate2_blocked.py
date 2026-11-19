"""P3.1-B Gate 2 前置核查：真实知识构建、审核、发布所需输入与授权是否齐备。

本脚本刻意不上传任何真实源码或正式文档，只核对服务器侧授权状态和输入缺口。
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


def env_values():
    values = {}
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    return values


def compose(*args):
    environment = dict(os.environ)
    environment["P31B_IMAGE_TAG"] = environment.get("P31B_IMAGE_TAG") or subprocess.run(
        ["git", "-C", str(ROOT.parent), "rev-parse", "--short=7", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    result = subprocess.run(
        [
            "docker", "compose", "--project-name", PROJECT,
            "--env-file", ".env.p31b", "-f", "docker-compose.p31b.yml", *args,
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env=environment,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    return result.stdout.strip()


def main():
    env = env_values()
    admin = {"Authorization": f"Bearer {env['ADMIN_TOKEN']}"}
    port = env.get("P31B_API_PORT", "18190")
    evidence = {
        "gate": "P3.1-B Gate 2",
        "mode": "PREFLIGHT_NO_REAL_INPUT",
        "result": "BLOCKED",
        "checks": {},
        "missing_inputs": [],
        "blockers": [],
    }

    def record(name, status, detail):
        evidence["checks"][name] = {"result": status, "detail": detail}
        print(f"{status} {name} {detail}")

    with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=30) as client:
        ready = client.get("/health/ready")
        record("api_ready", "PASS" if ready.status_code == 200 else "FAIL", str(ready.status_code))
        categories = client.get("/api/v1/admin/categories", headers=admin)
        categories.raise_for_status()
        items = categories.json()["items"]
        if items:
            category_id = items[0]["id"]
        else:
            created = client.post(
                "/api/v1/categories",
                headers=admin,
                json={"code": "gate2-preflight", "name": "P3.1-B Gate2 预检"},
            )
            created.raise_for_status()
            category_id = created.json()["id"]
        workspace = client.get(
            f"/api/v1/admin/categories/{category_id}", headers=admin
        )
        workspace.raise_for_status()
        auth = workspace.json()["model_authorization"]
        evidence["model_authorization"] = auth
        builder = auth["builder"]
        record(
            "model_not_configured",
            "BLOCKED" if not builder["model_configured"] else "PASS",
            f"model_configured={builder['model_configured']}",
        )
        record(
            "enterprise_egress_disabled",
            "PASS" if not builder["allow_enterprise"] else "FAIL",
            f"allow_enterprise={builder['allow_enterprise']}",
        )
        record(
            "synthetic_egress_disabled",
            "PASS" if not builder["allow_synthetic"] else "FAIL",
            f"allow_synthetic={builder['allow_synthetic']}",
        )
        record(
            "frontend_cannot_override_authorization",
            "PASS" if not auth["frontend_can_override"] else "FAIL",
            f"frontend_can_override={auth['frontend_can_override']}",
        )
        record(
            "discovery_structural_only",
            "PASS" if builder["document_discovery_mode"] == "structural" else "FAIL",
            builder["document_discovery_mode"],
        )

    values = compose(
        "exec", "-T", "api", "printenv",
        "BUILDER_ALLOW_ENTERPRISE", "BUILDER_ALLOW_SYNTHETIC", "BUILDER_MODEL",
    ).splitlines()
    evidence["container_environment"] = {
        "BUILDER_ALLOW_ENTERPRISE": values[0] if values else "",
        "BUILDER_ALLOW_SYNTHETIC": values[1] if len(values) > 1 else "",
        "BUILDER_MODEL_SET": bool(values[2]) if len(values) > 2 else False,
    }
    record(
        "container_enterprise_egress_disabled",
        "PASS" if values and values[0] == "false" else "FAIL",
        f"BUILDER_ALLOW_ENTERPRISE={values[0] if values else ''}",
    )
    record(
        "container_synthetic_egress_disabled",
        "PASS" if len(values) > 1 and values[1] == "false" else "FAIL",
        f"BUILDER_ALLOW_SYNTHETIC={values[1] if len(values) > 1 else ''}",
    )

    synthetic_zip = ROOT / "tests" / "fixtures" / "demo_waf_source.zip"
    synthetic_doc = ROOT / "tests" / "fixtures" / "p12" / "demo_waf_manual.docx"
    evidence["local_synthetic_only"] = {
        "source_zip": synthetic_zip.name if synthetic_zip.exists() else None,
        "document": synthetic_doc.name if synthetic_doc.exists() else None,
    }
    evidence["missing_inputs"] = [
        "未由管理员上传真实 WAF 源码 ZIP（仅存在 tests/fixtures 合成样例）",
        "未由管理员上传正式技术文档（仅存在 p12 合成 DOCX/PDF 样例）",
        "企业数据模型外发授权未完成，服务器侧模型未配置",
    ]
    evidence["blockers"] = [
        "真实知识构建需要真实源码与正式文档，不能以合成样例替代。",
        "文档语义发现与企业资料模型步骤在授权完成前必须保持 BLOCKED。",
        "没有发布物时不能进入审核发布验收，也不能宣称知识库已就绪。",
    ]
    for item in evidence["missing_inputs"]:
        print("BLOCKED missing_input " + item)
    record("real_source_provided", "BLOCKED", "无管理员提供的真实 WAF 源码 ZIP")
    record("real_document_provided", "BLOCKED", "无管理员提供的正式技术文档")
    record(
        "gate2_overall",
        "BLOCKED",
        "输入与模型授权均未就绪；按任务书不得以合成资料替代真实知识验收",
    )
    evidence["result"] = "BLOCKED"
    EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
    EVIDENCE.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"P31B_GATE2=BLOCKED evidence={EVIDENCE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
