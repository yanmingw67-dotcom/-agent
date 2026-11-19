"""P3.1-B Gate 3 结构预检：真实 WAF 招标文件的确定性提取与评估前置条件。

真实招标文件只在独立验收环境内做结构解析；不调用任何模型，不生成符合性结论。
没有真实发布知识库时，评估入口必须明确拒绝，因此 Gate 3 整体标记 BLOCKED。
"""

import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

import httpx

ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = ROOT / ".env.p31b"
EVIDENCE = ROOT / "docs" / "acceptance" / "p31b_gate3_evidence.json"
EXPECTED_SHA256 = "94052a70677167d92a2ec50b8933024450283f7e65d5dfc652f602b57b32af3c"
CANDIDATES = [
    ROOT.parent / ".monkeyai" / "uploads" / "attachment-3792846006" / "1-WAF招标技术参数表.docx",
    *sorted((ROOT / "storage" / "p31_acceptance" / "tender").glob("*.docx")),
]
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


def wait_job(client, admin, job_id, timeout=180):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        response = client.get(f"/api/v1/tender/extractions/{job_id}", headers=admin)
        response.raise_for_status()
        job = response.json()
        if job["status"] != last:
            print(
                f"tender job {job_id} {job['status']} "
                f"coverage={job['coverage_status']} error={job['error_code']}"
            )
            last = job["status"]
        if job["status"] in {"succeeded", "partial", "failed"}:
            return job
        time.sleep(0.3)
    raise AssertionError(f"招标提取任务在 {timeout}s 内未结束")


def main():
    path = Path(os.environ["P31B_TENDER_FILE"]) if os.environ.get("P31B_TENDER_FILE") else None
    if path is None:
        path = next((candidate for candidate in CANDIDATES if candidate.is_file()), None)
    evidence = {
        "gate": "P3.1-B Gate 3",
        "mode": "REAL_TENDER_STRUCTURAL_ONLY",
        "build_commit": tag(),
        "result": "BLOCKED",
        "checks": {},
        "limitations": [
            "未完成 Gate 2 真实知识发布，评估入口必须拒绝。",
            "本次没有产生真实产品的 All/Some/None 符合性结论。",
            "不得把本文件的参数提取结果表述为生产泛化准确率。",
        ],
    }
    if path is None or not path.is_file():
        evidence["checks"]["real_tender_present"] = {
            "result": "BLOCKED",
            "detail": "未找到真实招标文件",
        }
        EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
        EVIDENCE.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
        print("P31B_GATE3=BLOCKED 未找到真实招标文件")
        return 0
    data = path.read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    evidence["tender_file"] = {"filename": path.name, "size": len(data), "sha256": sha}

    def check(name, ok, detail):
        result = "PASS" if ok else "FAIL"
        evidence["checks"][name] = {"result": result, "detail": detail}
        print(f"{result} {name} {detail}")
        return ok

    check("real_tender_hash", sha == EXPECTED_SHA256, sha)
    env = env_values()
    admin = {"Authorization": f"Bearer {env['ADMIN_TOKEN']}"}
    port = env.get("P31B_API_PORT", "18190")
    with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=60) as client:
        category = client.post(
            "/api/v1/categories",
            headers=admin,
            json={"code": "gate3-waf-" + uuid4().hex[:10], "name": "P3.1-B Gate3 真实招标"},
        )
        category.raise_for_status()
        category_id = category.json()["id"]
        response = client.post(
            "/api/v1/tender/extractions/upload",
            headers=admin,
            data={"category_id": category_id, "idempotency_key": uuid4().hex},
            files={
                "file": (
                    path.name,
                    data,
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                )
            },
        )
        assert response.status_code == 202, response.text
        accepted = response.json()
        job = wait_job(client, admin, accepted["job_id"])
        evidence["job_id"] = accepted["job_id"]
        evidence["job"] = {
            key: job.get(key)
            for key in [
                "status",
                "input_type",
                "original_filename",
                "original_file_hash",
                "extracted_count",
                "skipped_count",
                "ambiguous_count",
                "coverage_status",
                "coverage_incomplete",
                "error_code",
                "metrics",
            ]
        }
        check("extraction_terminal", job["status"] in {"succeeded", "partial"}, job["status"])
        check(
            "extraction_hash",
            job.get("original_file_hash") == EXPECTED_SHA256,
            str(job.get("original_file_hash")),
        )
        check(
            "top_level_parameters",
            job.get("extracted_count") == 15,
            str(job.get("extracted_count")),
        )
        check(
            "coverage_complete",
            job.get("coverage_status") == "complete",
            str(job.get("coverage_status")),
        )
        check(
            "atomic_requirements",
            job.get("metrics", {}).get("requirements") == 200,
            str(job.get("metrics", {}).get("requirements")),
        )

        parameters = job.get("parameters", [])
        subclauses = sum(
            len(re.findall(r"[（(]\d+[）)]", parameter.get("original_text", "")))
            for parameter in parameters
        )
        atoms = sum(len(parameter.get("atomic_requirements", [])) for parameter in parameters)
        check("numbered_subclauses", subclauses == 46, str(subclauses))
        check("atomic_rows", atoms == 200, str(atoms))

        def blank_profile(order):
            parameter = next((row for row in parameters if row.get("order_index") == order), None)
            if not parameter:
                return None
            blanks = [
                row
                for row in parameter.get("atomic_requirements", [])
                if row.get("ambiguity") == "blank_threshold"
            ]
            return {
                "count": len(blanks),
                "units": sorted({row.get("unit") for row in blanks}),
                "all_threshold_none": all(row.get("threshold") is None for row in blanks),
            }

        profiles = {"10": blank_profile(10), "14": blank_profile(14), "15": blank_profile(15)}
        evidence["blank_thresholds"] = profiles
        check(
            "blank_thresholds_preserved",
            profiles
            == {
                "10": {"count": 4, "units": ["Gbps", "万"], "all_threshold_none": True},
                "14": {"count": 2, "units": ["%"], "all_threshold_none": True},
                "15": {"count": 2, "units": ["GB", "vCPU"], "all_threshold_none": True},
            },
            json.dumps(profiles, ensure_ascii=False),
        )

        evaluation = client.post(
            "/api/v1/tender/evaluations",
            headers=admin,
            json={"extraction_job_id": accepted["job_id"], "idempotency_key": uuid4().hex},
        )
        body = evaluation.json()
        evidence["evaluation_attempt"] = {
            "status_code": evaluation.status_code,
            "error_code": body.get("error_code"),
            "message": body.get("message"),
        }
        check(
            "evaluation_requires_publication",
            evaluation.status_code == 409 and body.get("error_code") == "KNOWLEDGE_NOT_PUBLISHED",
            f"{evaluation.status_code} {body.get('error_code')}",
        )
        evidence["result"] = "BLOCKED"
        evidence["blockers"] = [
            "当前品类没有真实发布知识库，评估按设计返回 KNOWLEDGE_NOT_PUBLISHED。",
            "Gate 2 未完成，不能生成真实产品符合性结论或 Word/Excel 评估导出。",
        ]
    EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
    EVIDENCE.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    failed = [name for name, row in evidence["checks"].items() if row["result"] == "FAIL"]
    print(f"P31B_GATE3=BLOCKED structural_failed={failed} evidence={EVIDENCE}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
