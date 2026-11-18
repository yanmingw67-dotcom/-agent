"""P1.5 Compose 验收：复用P1.3真实模型记录与P1.4发布记录，不重新调用模型。

只对真实PostgreSQL与运行中的Compose服务执行；新上传/索引使用人工公开样例。
报告只保存标识、哈希、计数和Probe响应，不保存源码全文或凭据。
"""

import argparse
import json
import subprocess
import time
from pathlib import Path
from uuid import uuid4

import httpx
from dotenv import dotenv_values

ROOT = Path("reports/p15")
FIXTURE = Path("tests/fixtures/p13_autonomous/source.zip")
P13 = Path("reports/p13-autonomous/real-compact-final.json")
P14 = Path("reports/p14/round1/real-api-postgres-success.json")
P1 = "1af83dd5-e04e-4517-a81c-ab8a15201280"
P2 = "db315185-3b36-4c8f-b9a4-3a7e055a1369"
P1_HASH = "877338d1623d38d6e913b19634f57dee69183e28c5afa9069028612b501d0f90"
P2_HASH = "2ba20577968bff3844d8cce6eabb017defe93769307b17096d2ac3cd966a3bfd"


def read_json(path):
    return json.loads(Path(path).read_text())


def docker_limits(service):
    container = (
        subprocess.check_output(["docker", "compose", "ps", "-q", service], text=True)
        .strip()
        .splitlines()[0]
    )
    output = subprocess.check_output(
        ["docker", "inspect", container, "--format", "{{json .HostConfig}}"], text=True
    )
    config = json.loads(output)
    return {
        "container": container[:12],
        "nano_cpus": config.get("NanoCpus"),
        "cpu_quota": config.get("CpuQuota"),
        "cpu_period": config.get("CpuPeriod"),
        "memory_bytes": config.get("Memory"),
        "pids_limit": config.get("PidsLimit"),
        "read_only_rootfs": config.get("ReadonlyRootfs"),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", default="compose")
    args = parser.parse_args()
    destination = ROOT / f"p15-acceptance-{args.label}.json"
    assert not destination.exists(), "不可覆盖既有验收轨迹"

    env = dotenv_values(".env")
    assert env.get("BUILDER_ALLOW_ENTERPRISE", "").lower() != "true"
    api = httpx.Client(
        base_url="http://127.0.0.1:8000",
        headers={"Authorization": "Bearer " + env["ADMIN_TOKEN"]},
        timeout=60,
    )
    api_read = httpx.Client(
        base_url="http://127.0.0.1:8000",
        headers={"Authorization": "Bearer " + env["READ_TOKEN"]},
        timeout=60,
    )
    probe = httpx.Client(
        base_url="http://127.0.0.1:8001",
        headers={"Authorization": "Bearer " + env["READ_TOKEN"]},
        timeout=60,
    )
    probe_admin = httpx.Client(
        base_url="http://127.0.0.1:8001",
        headers={"Authorization": "Bearer " + env["ADMIN_TOKEN"]},
        timeout=60,
    )
    probe_anon = httpx.Client(base_url="http://127.0.0.1:8001", timeout=60)

    source = read_json(P13)
    publication = read_json(P14)
    cid, sid = source["category_id"], source["snapshot_id"]
    assert cid == publication["category_id"] and sid == publication["snapshot_id"]
    assert source["mode"].startswith("REAL_APPROVED_MODEL")
    assert source["passed"] is True
    assert publication["passed"] is True
    assert publication["model_calls"] == 0
    assert publication["database_states"]["after_rollback"]["active_publication_id"] == P1
    assert publication["comparison"]["exact_response_restored"] in (True, "True")

    report = {
        "mode": "P1.5_COMPOSE_REAL_POSTGRES_HISTORICAL_REAL_MODEL_RECORD",
        "criteria": "AgentA_Phase1_Development_Spec_v1.0.md §11 AC-14/AC-16",
        "model_calls_this_phase": 0,
        "cost_this_phase_usd": None,
        "category_id": cid,
        "snapshot_id": sid,
        "reused_p13_job_id": source["job_id"],
        "reused_publications": {"p1": P1, "p2": P2},
        "steps": [],
        "checks": {},
    }

    def save():
        destination.write_text(json.dumps(report, ensure_ascii=False, indent=2))

    def record(label, response, extra=None):
        try:
            body = response.json()
        except ValueError:
            body = None
        item = {"label": label, "status": response.status_code}
        if isinstance(body, dict) and body.get("error_code"):
            item["error_code"] = body["error_code"]
        if extra:
            item["detail"] = extra
        report["steps"].append(item)
        save()
        return body

    def check(name, condition, detail=None):
        report["checks"][name] = {"passed": bool(condition), "detail": detail}
        save()
        assert condition, (name, detail)

    def wait_job(job_id, timeout=180):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            response = api.get(f"/api/v1/jobs/{job_id}")
            if response.status_code == 200:
                job = response.json()
                if job["status"] in {"succeeded", "partial", "failed"}:
                    return job
            time.sleep(0.5)
        raise AssertionError("任务等待超时")

    def probe_call(category_id=None, client=probe, requirement="事件 预览 模式 分发 筛选", **extra):
        response = client.post(
            "/api/v1/probes",
            json={"category_id": category_id or cid, "requirement_text": requirement, **extra},
        )
        return response

    def compact(data):
        return {
            "status": data.get("status"),
            "publication_id": data.get("publication_id"),
            "snapshot_id": data.get("snapshot_id"),
            "publication_hash": data.get("publication_hash"),
            "capability_ids": [c["capability_id"] for c in data.get("candidates", [])],
            "revision_ids": [c["revision_id"] for c in data.get("candidates", [])],
            "evidence_ids": [e["id"] for e in data.get("evidence", [])],
            "coverage": data.get("coverage"),
            "gaps": [g["code"] for g in data.get("unverified_gaps", [])],
            "warnings": data.get("warnings"),
            "formal_compliance_decision": data.get("formal_compliance_decision"),
            "support_status": data.get("support_status"),
            "truncated": data.get("truncated"),
            "duration_ms": data.get("duration_ms"),
        }

    try:
        report["environment"] = {
            "compose": subprocess.check_output(
                ["docker", "compose", "ps", "--format", "json"], text=True
            ).splitlines(),
            "api_limits": docker_limits("api"),
            "retrieval_limits": docker_limits("retrieval"),
            "builder_limits": docker_limits("builder"),
            "note": "Docker Compose cgroup配额，不是独立4C8G虚拟机实测",
        }

        # 0. 已知状态归位：若上轮中断留下P2，先恢复P1。
        current = probe_call().json()
        if current.get("publication_id") != P1:
            response = api.post(
                f"/api/v1/categories/{cid}/rollback",
                json={
                    "publication_id": P1,
                    "expected_active_publication_id": current.get("publication_id"),
                    "comment": "P1.5验收前恢复已知P1状态",
                },
            )
            record("验收前恢复P1", response)
            assert response.status_code == 200, response.text

        before_response = probe_call()
        before = before_response.json()
        record("空闲Probe读取P1", before_response)
        check("probe_p1_status", before_response.status_code == 200)
        check("probe_p1_publication", before["publication_id"] == P1)
        check("probe_p1_hash", before["publication_hash"] == P1_HASH)
        check(
            "probe_p1_candidate",
            before["candidates"][0]["capability_id"]
            == "cap-f250d060c9ed1b8de3f29122973a0c863e980fec2db261ceb23dd26e52c34555",
        )
        check("probe_p1_formal_decision_null", before["formal_compliance_decision"] is None)
        check("probe_p1_support_not_assessed", before["support_status"] == "not_assessed")
        check("probe_p1_evidence_present", bool(before["evidence"]))
        check("probe_p1_coverage_present", bool(before["coverage"]))
        check(
            "probe_p1_coverage_incomplete_gap",
            "KNOWLEDGE_COVERAGE_INCOMPLETE" in {g["code"] for g in before["unverified_gaps"]},
        )
        check(
            "probe_p1_semantic_unverified",
            all(e["semantic_verification"] == "unverified" for e in before["evidence"]),
        )
        report["probe_p1"] = compact(before)
        save()

        # 1. 证据回读：Probe返回的每条证据必须与正式证据接口逐字段一致。
        evidence_mismatch = []
        for item in before["evidence"]:
            response = api.get(
                f"/api/v1/evidence/{item['id']}",
                params={"category_id": cid, "snapshot_id": sid},
            )
            if response.status_code != 200:
                evidence_mismatch.append({"id": item["id"], "status": response.status_code})
                continue
            official = response.json()
            for key in (
                "snapshot_id",
                "source_ref",
                "locator",
                "source_hash",
                "quote_hash",
                "quote",
                "verification_state",
                "semantic_verification",
            ):
                if official.get(key) != item.get(key):
                    evidence_mismatch.append({"id": item["id"], "field": key})
        check("evidence_traceability", not evidence_mismatch, evidence_mismatch)
        check(
            "evidence_relative_paths",
            all(
                not str(e["source_ref"].get("path", "")).startswith("/") for e in before["evidence"]
            ),
        )

        # 2. 权限与作用域。
        anon = probe_anon.post(
            "/api/v1/probes", json={"category_id": cid, "requirement_text": "事件"}
        )
        record("匿名Probe拒绝", anon)
        check("probe_anonymous_401", anon.status_code == 401)
        bad = probe_anon.post(
            "/api/v1/probes",
            headers={"Authorization": "Bearer " + "x" * 48},
            json={"category_id": cid, "requirement_text": "事件"},
        )
        record("错误Token拒绝", bad)
        check("probe_bad_token_401", bad.status_code == 401)
        admin_ok = probe_admin.post(
            "/api/v1/probes", json={"category_id": cid, "requirement_text": "事件"}
        )
        record("管理员Probe允许", admin_ok)
        check("probe_admin_allowed", admin_ok.status_code == 200)

        for label, body in [
            (
                "额外snapshot_id",
                {"category_id": cid, "requirement_text": "事件", "snapshot_id": "internal"},
            ),
            (
                "额外publication_id",
                {"category_id": cid, "requirement_text": "事件", "publication_id": P1},
            ),
            ("越界limit", {"category_id": cid, "requirement_text": "事件", "limit": 21}),
            ("空白需求", {"category_id": cid, "requirement_text": "   "}),
        ]:
            response = probe.post("/api/v1/probes", json=body)
            record(label, response)
            check("reject_" + label, response.status_code == 422)
        query_override = probe.post(
            "/api/v1/probes?snapshot_id=internal",
            json={"category_id": cid, "requirement_text": "事件"},
        )
        record("查询参数覆盖拒绝", query_override)
        check("reject_query_override", query_override.status_code == 422)

        api_mode = api_read.post(
            "/api/v1/probes", json={"category_id": cid, "requirement_text": "事件"}
        )
        record("后台API不执行Probe", api_mode)
        check("api_probe_503", api_mode.status_code == 503)

        for label, path, body in [
            ("品类写", "/api/v1/categories", {}),
            ("发布", f"/api/v1/snapshots/{sid}/publish", {}),
            ("回滚", f"/api/v1/categories/{cid}/rollback", {}),
        ]:
            response = probe_admin.post(path, json=body)
            record("检索服务无" + label + "入口", response)
            check("retrieval_no_write_" + label, response.status_code == 404)

        unknown = probe_call(category_id="missing-product")
        record("未知品类", unknown)
        check("unknown_category_404", unknown.status_code == 404)

        # 3. 新品类真实上传、安全解压、静态索引（不调用模型）。
        created = api.post(
            "/api/v1/categories",
            json={"code": "p15-acceptance-" + uuid4().hex[:8], "name": "P1.5端到端人工公开样例"},
        )
        record("创建新品类", created)
        assert created.status_code == 201, created.text
        new_cid = created.json()["id"]
        uploaded = api.post(
            f"/api/v1/categories/{new_cid}/source",
            files={"file": ("source.zip", FIXTURE.read_bytes(), "application/zip")},
        )
        record("上传人工公开ZIP", uploaded)
        assert uploaded.status_code == 202, uploaded.text
        imported = wait_job(uploaded.json()["job_id"])
        check("fresh_import_succeeded", imported["status"] == "succeeded", imported["status"])
        new_sid = uploaded.json()["snapshot_id"]
        built = api.post(f"/api/v1/snapshots/{new_sid}/build")
        record("触发静态索引", built)
        assert built.status_code == 202, built.text
        indexed_job = wait_job(built.json()["job_id"])
        check("fresh_index_succeeded", indexed_job["status"] == "succeeded", indexed_job["status"])
        project = api.post(
            f"/api/v1/retrieval/{new_cid}",
            params={"snapshot_id": new_sid},
            json={"tool": "get_project_map"},
        )
        record("读取新快照模块图", project)
        check("fresh_project_map", project.status_code == 200 and bool(project.json()["results"]))
        unpublished = probe_call(category_id=new_cid)
        record("未发布品类不可见", unpublished)
        check("fresh_unpublished_409", unpublished.status_code == 409)
        report["fresh_ingestion"] = {
            "category_id": new_cid,
            "snapshot_id": new_sid,
            "import_job_id": uploaded.json()["job_id"],
            "index_job_id": built.json()["job_id"],
            "modules": len(project.json().get("results", [])),
        }
        save()

        # 4. 历史真实模型记录仍可追溯，不重跑模型。
        historical_job = api.get(f"/api/v1/jobs/{source['job_id']}")
        record("读取P1.3真实模型任务", historical_job)
        check("historical_job_exists", historical_job.status_code == 200)
        check("historical_job_status", historical_job.json()["status"] in {"succeeded", "partial"})
        investigations = api.get(f"/api/v1/jobs/{source['job_id']}/investigations")
        record("读取历史调查记录", investigations)
        check("historical_investigations", investigations.status_code == 200)
        claim_ids = {c["id"] for c in source["candidates"]}
        check("historical_claims", bool(claim_ids))
        check("historical_evidence", bool(source["evidence"]))
        check("historical_real_mode", "REAL_APPROVED_MODEL" in source["mode"])
        report["historical_chain"] = {
            "job_id": source["job_id"],
            "job_status": historical_job.json()["status"],
            "claims": len(claim_ids),
            "evidence": len(source["evidence"]),
            "investigations_status": investigations.status_code,
            "real_model_record": True,
            "model_calls_this_phase": 0,
        }
        save()

        # 5. 发布切换与回滚：P1 -> P2 -> P1，读取必须跟随当前清单。
        switch = api.post(
            f"/api/v1/categories/{cid}/rollback",
            json={
                "publication_id": P2,
                "expected_active_publication_id": P1,
                "comment": "P1.5验收切换到P2并读取",
            },
        )
        record("切换到P2发布", switch)
        assert switch.status_code == 200, switch.text
        p2_response = probe_call()
        p2 = p2_response.json()
        record("读取P2", p2_response)
        check("p2_read_200", p2_response.status_code == 200)
        check("p2_publication", p2["publication_id"] == P2)
        check("p2_hash", p2["publication_hash"] == P2_HASH)
        check("p2_three_capabilities", len(p2["candidates"]) == 3)
        check("p2_evidence_present", bool(p2["evidence"]))
        check("p2_evidence_snapshot", all(e["snapshot_id"] == sid for e in p2["evidence"]))
        report["probe_p2"] = compact(p2)
        save()

        restored = api.post(
            f"/api/v1/categories/{cid}/rollback",
            json={
                "publication_id": P1,
                "expected_active_publication_id": P2,
                "comment": "P1.5验收回滚到P1",
            },
        )
        record("回滚到P1发布", restored)
        assert restored.status_code == 200, restored.text
        after_response = probe_call()
        after = after_response.json()
        record("回滚后读取P1", after_response)
        before.pop("duration_ms", None)
        after.pop("duration_ms", None)
        check("rollback_exact_restore", before == after)
        check("rollback_hash", after["publication_hash"] == P1_HASH)
        report["probe_after_rollback"] = compact(after)
        report["comparison"] = {
            "p1_candidates": report["probe_p1"]["capability_ids"],
            "p2_candidates": sorted(report["probe_p2"]["capability_ids"]),
            "restored_candidates": report["probe_after_rollback"]["capability_ids"],
            "p1_hash": report["probe_p1"]["publication_hash"],
            "p2_hash": report["probe_p2"]["publication_hash"],
            "restored_hash": report["probe_after_rollback"]["publication_hash"],
        }
        check("final_publication_is_p1", after["publication_id"] == P1)

        report["passed"] = all(v["passed"] for v in report["checks"].values())
        save()
        print(
            json.dumps(
                {
                    "passed": report["passed"],
                    "checks": len(report["checks"]),
                    "steps": len(report["steps"]),
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
        # 无论验收在哪一步失败，都尝试恢复P1，避免影响后续读取。
        try:
            current = probe_call().json().get("publication_id")
            if current and current != P1:
                api.post(
                    f"/api/v1/categories/{cid}/rollback",
                    json={
                        "publication_id": P1,
                        "expected_active_publication_id": current,
                        "comment": "P1.5验收异常收尾恢复P1",
                    },
                )
        except Exception:
            pass
        api.close()
        api_read.close()
        probe.close()
        probe_admin.close()
        probe_anon.close()


if __name__ == "__main__":
    main()
