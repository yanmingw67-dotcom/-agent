"""仅真实批准端点与人工公开素材；每次调用独立保存，绝不使用预置模型结果。"""

import argparse
import json
import subprocess
import time
from pathlib import Path
from uuid import uuid4

import httpx
from dotenv import dotenv_values

ROOT = Path("reports/p13-remediation")
ROOT.mkdir(parents=True, exist_ok=True)
STATE = ROOT / "fixture-state.json"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", required=True)
    parser.add_argument("--count", type=int, choices=[1, 2, 3], default=1)
    parser.add_argument("--module", default="notify")
    parser.add_argument("--equivalence", action="store_true")
    args = parser.parse_args()
    destination = ROOT / (args.label + ".json")
    assert not destination.exists(), "拒绝覆盖既有真实验收材料"
    env = dotenv_values(".env")
    assert env.get("BUILDER_ALLOW_ENTERPRISE", "").lower() != "true"
    api = httpx.Client(
        base_url="http://127.0.0.1:8000",
        headers={"Authorization": "Bearer " + env["ADMIN_TOKEN"]},
        timeout=20,
    )
    retrieval = httpx.Client(
        base_url="http://127.0.0.1:8001",
        headers={"Authorization": "Bearer " + env["ADMIN_TOKEN"]},
        timeout=20,
    )

    def checked(response):
        response.raise_for_status()
        return response.json()

    def wait(jid):
        deadline = time.monotonic() + 210
        while time.monotonic() < deadline:
            j = checked(api.get("/api/v1/jobs/" + jid))
            if j["status"] in ["succeeded", "partial", "failed"]:
                return j
            time.sleep(0.2)
        raise AssertionError("验收轮询时间预算耗尽；不自动创建新任务")

    if not STATE.exists():
        cid = checked(
            api.post(
                "/api/v1/categories",
                json={"code": "p13-remediation-" + uuid4().hex[:8], "name": "P1.3整改人工公开样例"},
            )
        )["id"]
        source = checked(
            api.post(
                f"/api/v1/categories/{cid}/source",
                files={
                    "file": (
                        "fixture.zip",
                        Path("tests/fixtures/p13/knowledge_builder_source.zip").read_bytes(),
                        "application/zip",
                    )
                },
            )
        )
        assert wait(source["job_id"])["status"] == "succeeded"
        sid = source["snapshot_id"]
        assert (
            wait(checked(api.post(f"/api/v1/snapshots/{sid}/build"))["job_id"])["status"]
            == "succeeded"
        )
        doc = checked(
            api.post(
                f"/api/v1/categories/{cid}/documents",
                data={"snapshot_id": sid},
                files={
                    "file": (
                        "manual.md",
                        Path("tests/fixtures/p13/manual.md").read_bytes(),
                        "text/markdown",
                    )
                },
            )
        )
        assert wait(doc["job_id"])["status"] == "succeeded"
        approval = checked(api.post(f"/api/v1/snapshots/{sid}/approve-public-fixture"))
        STATE.write_text(
            json.dumps(
                {"category_id": cid, "snapshot_id": sid, "approval": approval},
                ensure_ascii=False,
                indent=2,
            )
        )
    state = json.loads(STATE.read_text())
    cid, sid = state["category_id"], state["snapshot_id"]
    project = checked(
        retrieval.post(
            f"/api/v1/retrieval/{cid}",
            params={"snapshot_id": sid},
            json={"tool": "get_project_map"},
        )
    )
    mid = next(x["module_id"] for x in project["results"] if x["name"] == args.module)
    started = time.monotonic()
    accepted = checked(
        api.post(
            f"/api/v1/snapshots/{sid}/investigate",
            json={
                "module_ids": [mid],
                "investigation_mode": "bounded",
                "capability_limit": args.count,
                "include_equivalence": args.equivalence,
                "data_classification": "synthetic_public",
            },
        )
    )
    jid = accepted["job_id"]
    report = {
        "mode": "REAL_APPROVED_MODEL_NO_PRESET_OUTPUT",
        "criteria": "docs/P1.3_REMEDIATION_PLAN.md",
        "label": args.label,
        "module": args.module,
        "capability_limit": args.count,
        "equivalence_requested": args.equivalence,
        **state,
        "job_id": jid,
        "enqueue_ms": round((time.monotonic() - started) * 1000, 3),
    }
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    job = wait(jid)
    report["elapsed_seconds"] = round(time.monotonic() - started, 3)
    report["job"] = job
    report["investigations"] = checked(api.get(f"/api/v1/jobs/{jid}/investigations"))
    traces = []
    cursor = 0
    while True:
        page = checked(
            api.get(f"/api/v1/jobs/{jid}/investigation-traces", params={"cursor": cursor})
        )
        traces.extend(page["results"])
        if not page["truncated"]:
            break
        cursor = page["next_cursor"]
    report["trace"] = traces
    claim_ids = {
        v for r in report["investigations"] for v in r["result"].get("claim_ids", {}).values()
    }
    candidates = checked(
        api.get(f"/api/v1/snapshots/{sid}/candidates", params={"category_id": cid, "limit": 100})
    )
    report["candidates"] = [c for c in candidates["results"] if c["id"] in claim_ids]
    report["relations"] = [r for r in candidates["relations"] if r["source_claim_id"] in claim_ids]
    eids = {e for c in report["candidates"] for e in c["draft"]["evidence_ids"]}
    report["evidence"] = [
        checked(api.get(f"/api/v1/evidence/{eid}", params={"category_id": cid, "snapshot_id": sid}))
        for eid in sorted(eids)
    ]
    code = """
import json,sys
from sqlalchemy import select
from app.db.session import session_factory
from app.db.knowledge_models import Investigation,CapabilityClaim,ClaimEvidenceLink,Evidence
from app.knowledge.classification import classification
with session_factory()() as db:
 rows=db.execute(select(CapabilityClaim,ClaimEvidenceLink,Evidence).join(
 ClaimEvidenceLink,ClaimEvidenceLink.claim_id==CapabilityClaim.id).join(
 Evidence,Evidence.id==ClaimEvidenceLink.evidence_id).join(
 Investigation,Investigation.id==CapabilityClaim.investigation_id).where(Investigation.job_id==sys.argv[1])).all()
 print(json.dumps([{'claim_id':c.id,'status':c.status,'evidence_id':e.id,
 'support_type':l.support_type,'source_hash':e.source_hash,'quote_hash':e.quote_hash,
 'source_ref':e.source_ref,'locator':e.locator,'trusted_classification':classification(db,c.snapshot_id)}
 for c,l,e in rows],ensure_ascii=False))
"""
    raw = subprocess.check_output(
        ["docker", "compose", "exec", "-T", "api", "python", "-c", code, jid], text=True
    )
    report["database_records"] = json.loads(raw)
    report["passed"] = (
        job["status"] == "succeeded"
        and len(report["candidates"]) >= args.count
        and len(report["database_records"]) >= args.count
        and bool(report["evidence"])
        and all(c["draft"]["limitations"] for c in report["candidates"])
        and all(e["semantic_verification"] == "unverified" for e in report["evidence"])
        and (not args.equivalence or bool(report["relations"]))
    )
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(
        json.dumps(
            {
                "artifact": str(destination),
                "passed": report["passed"],
                "job_id": jid,
                "status": job["status"],
                "metrics": job["metrics_json"],
                "candidates": [c["draft"] for c in report["candidates"]],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    assert report["passed"], "真实模型本轮未达事前验收门槛；请检查已保存JSON，不自动重跑"


if __name__ == "__main__":
    main()
