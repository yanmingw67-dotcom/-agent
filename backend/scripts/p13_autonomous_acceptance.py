"""仅真实批准端点与人工公开素材；每次调用独立保存，绝不使用预置模型结果。"""

import argparse
import json
import subprocess
import time
from pathlib import Path
from uuid import uuid4

import httpx
from dotenv import dotenv_values

ROOT = Path("reports/p13-autonomous")
ROOT.mkdir(parents=True, exist_ok=True)
STATE = ROOT / "fixture-state.json"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", required=True)
    parser.add_argument("--count", type=int, choices=[1, 2, 3, 4, 5, 6], default=3)
    parser.add_argument("--module", default="api")
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

    latency = {"idle": [], "running": [], "errors": []}
    benchmark = {}

    def sample(group):
        if not benchmark:
            return
        before = time.monotonic()
        response = retrieval.post(
            f"/api/v1/retrieval/{benchmark['cid']}",
            params={"snapshot_id": benchmark["sid"]},
            json={"tool": "search_symbols", "query": "", "limit": 20},
        )
        elapsed = round((time.monotonic() - before) * 1000, 3)
        if response.status_code == 200:
            latency[group].append(elapsed)
        else:
            latency["errors"].append({"status": response.status_code})

    def wait(jid):
        deadline = time.monotonic() + 210
        while time.monotonic() < deadline:
            j = checked(api.get("/api/v1/jobs/" + jid))
            if j["status"] in ["succeeded", "partial", "failed"]:
                return j
            if j["job_type"] == "knowledge_build" and j["status"] == "running":
                sample("running")
                time.sleep(1)
            else:
                time.sleep(0.2)
        raise AssertionError("验收轮询时间预算耗尽；不自动创建新任务")

    if not STATE.exists():
        cid = checked(
            api.post(
                "/api/v1/categories",
                json={
                    "code": "p13-autonomous-" + uuid4().hex[:8],
                    "name": "P1.3自主探索人工公开样例",
                },
            )
        )["id"]
        source = checked(
            api.post(
                f"/api/v1/categories/{cid}/source",
                files={
                    "file": (
                        "fixture.zip",
                        Path("tests/fixtures/p13_autonomous/source.zip").read_bytes(),
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
                        Path("tests/fixtures/p13_autonomous/manual.md").read_bytes(),
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
    benchmark.update(cid=cid, sid=sid)
    for _ in range(20):
        sample("idle")
    started = time.monotonic()
    accepted = checked(
        api.post(
            f"/api/v1/snapshots/{sid}/investigate",
            json={
                "module_ids": [mid],
                "investigation_mode": "autonomous",
                "exploration_max_fragments": args.count,
                "include_equivalence": args.equivalence,
                "data_classification": "synthetic_public",
            },
        )
    )
    jid = accepted["job_id"]
    report = {
        "mode": "REAL_APPROVED_MODEL_NO_PRESET_OUTPUT",
        "criteria": "docs/P1.3_AUTONOMOUS_PLAN.md",
        "label": args.label,
        "module": args.module,
        "exploration_max_fragments": args.count,
        "equivalence_requested": args.equivalence,
        **state,
        "job_id": jid,
        "enqueue_ms": round((time.monotonic() - started) * 1000, 3),
    }
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    job = wait(jid)
    report["retrieval_latency_ms"] = latency
    for group in ["idle", "running"]:
        values = sorted(latency[group])
        report[group + "_latency_summary"] = {
            "samples": len(values),
            "p50_ms": values[len(values) // 2] if values else None,
            "p95_ms": values[min(len(values) - 1, int(len(values) * 0.95))] if values else None,
        }
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
    gold = json.loads(Path("tests/fixtures/p13_autonomous/gold.json").read_text())
    report["passed"] = (
        job["status"] in {"succeeded", "partial"}
        and all(row["error_code"] is None for row in report["investigations"])
        and not any(t["kind"] == "module_error" for t in report["trace"])
        and len(report["candidates"]) >= 2
        and len(report["database_records"]) >= 2
        and bool(report["evidence"])
        and all(c["draft"]["limitations"] for c in report["candidates"])
        and all(e["semantic_verification"] == "unverified" for e in report["evidence"])
        and any(
            e["source_ref"].get("path") == gold["hidden"]["path"]
            and e["locator"]["start_line"] > 100
            for e in report["evidence"]
        )
        and (
            not args.equivalence
            or any(
                r["metrics"].get("exploration", {}).get("equivalence", {}).get("status")
                == "investigated"
                and r["metrics"]["exploration"]["equivalence"]["verdict"] == "not_equivalent"
                and {
                    s["path"]
                    for s in r["metrics"]["exploration"]["equivalence"]["source_locations"]
                }
                == {gold["hidden"]["path"], "core/i_preview.py"}
                for r in report["investigations"]
            )
        )
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
