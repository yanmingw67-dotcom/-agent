"""人工审核与原子发布负例；不自动升级模型语义。"""

from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.db.index_models import CodeSymbol, SourceFile
from app.db.knowledge_models import CapabilityClaim, Investigation
from app.db.models import Category, KnowledgeJob
from app.db.review_models import Publication, PublicationSwitch
from app.db.session import session_factory
from app.knowledge.evidence_registry import register
from app.knowledge.quality import fields
from app.knowledge.schemas import EvidenceRequest
from tests.conftest import ADMIN, READ
from tests.integration.test_p12_index_retrieval import indexed
from tests.integration.test_p13_builder import context

FIXTURE = Path("tests/fixtures/p13_autonomous/source.zip")


def setup(client):
    from uuid import uuid4

    cid, sid = indexed(client, code="p14-" + uuid4().hex[:8], data=FIXTURE.read_bytes())
    with session_factory().begin() as db:
        files = {
            f.path: f for f in db.scalars(select(SourceFile).where(SourceFile.snapshot_id == sid))
        }
        job = KnowledgeJob(
            category_id=cid,
            snapshot_id=sid,
            job_type="knowledge_build",
            status="partial",
            metrics_json={"model_mode": "NOT_USED"},
        )
        db.add(job)
        db.flush()
        module_id = files["core/i_preview.py"].module_id
        run = Investigation(job_id=job.id, snapshot_id=sid, module_id=module_id, status="partial")
        db.add(run)
        db.flush()
        evidence = {}
        for path in [
            "core/i_preview.py",
            "core/h_engine.py",
            "storage/durable.py",
            "api/endpoint.py",
        ]:
            f = files[path]
            start = 118 if path == "core/h_engine.py" else 1
            receipt = register(
                db,
                context(cid, sid),
                EvidenceRequest(
                    kind="source",
                    file_id=f.id,
                    start_line=start,
                    end_line=f.line_count,
                    expected_sha256=f.sha256,
                    supported_statement="人工回读",
                ),
            )
            evidence[path] = receipt.evidence_id
        run.evidence_ids = list(evidence.values())
        ids = []
        for index in range(3):
            c = CapabilityClaim(
                id=f"p14-{sid}-{index}",
                snapshot_id=sid,
                investigation_id=run.id,
                local_key=str(index),
                name="原始模型草稿",
                status="evidence_found",
                draft_json={
                    "name": "展开commit提交路径",
                    "action": "检索实现",
                    "target": "事件",
                    "business_goal": "调查",
                    "implementation_mechanism": "待核验",
                    "evidence_ids": [evidence["core/i_preview.py"]],
                },
            )
            db.add(c)
            ids.append(c.id)
    return cid, sid, ids, evidence


def content(name="事件列表首项保留机制"):
    return {
        "name": name,
        "action": "按标识保留首项",
        "target": "事件列表",
        "business_goal": "生成预览列表",
        "implementation_mechanism": "使用局部集合记录标识并保留首次出现项",
        "supported_scope": ["单次函数调用"],
        "conditions": ["标识可哈希"],
        "limitations": ["不证明存储行为或网络送达"],
    }


def request(eid, name="事件列表首项保留机制"):
    c = content(name)
    return {
        "action": "accept",
        "expected_revision": 0,
        "declaration_type": "technical_mechanism",
        "content": c,
        "assertions": [
            {
                "field": k,
                "text": v,
                "evidence_ids": [eid],
                "semantic_decision": "supported",
                "rationale": "人工逐项核对源码局部集合与分支，限于单函数",
            }
            for k, v in fields(c).items()
        ],
        "comment": "人工确认窄范围静态机制，不承诺产品接入",
        "human_confirmed": True,
    }


def accept(client, claim, body):
    r = client.post(f"/api/v1/capabilities/{claim}/review", headers=ADMIN, json=body)
    assert r.status_code == 200, r.text
    return r.json()


def publish(client, sid, revisions, expected=None):
    return client.post(
        f"/api/v1/snapshots/{sid}/publish",
        headers=ADMIN,
        json={
            "revision_ids": revisions,
            "expected_active_publication_id": expected,
            "acknowledge_coverage_gaps": True,
            "comment": "明确接受局部机制发布，缺口保留",
        },
    )


def test_model_original_immutable_and_action_cannot_publish(client):
    _, sid, ids, ev = setup(client)
    b = request(ev["core/i_preview.py"])
    b["content"]["name"] = "展开commit提交路径"
    b["assertions"][0]["text"] = b["content"]["name"]
    r = client.post(f"/api/v1/capabilities/{ids[0]}/review", headers=ADMIN, json=b)
    assert r.status_code == 422
    b["action"] = "return"
    r = accept(client, ids[0], b)
    assert "INVESTIGATION_ACTION_NOT_CAPABILITY" in r["gate"]["errors"]
    assert publish(client, sid, [r["id"]]).status_code == 422
    original = client.get(f"/api/v1/admin/capabilities/{ids[0]}", headers=ADMIN).json()
    assert original["original_model_output"]["name"] == "展开commit提交路径"
    assert original["review_events"][0]["action"] == "return"


@pytest.mark.parametrize("declaration", ["unknown", "investigation_action", "invalid"])
def test_unpublishable_declarations_cannot_accept(client, declaration):
    _, _, ids, ev = setup(client)
    b = request(ev["core/i_preview.py"])
    b["declaration_type"] = declaration
    assert (
        client.post(f"/api/v1/capabilities/{ids[0]}/review", headers=ADMIN, json=b).status_code
        == 422
    )


@pytest.mark.parametrize("change", ["unverified", "missing", "mismatch", "no_human"])
def test_assertion_gate_cannot_be_bypassed(client, change):
    _, _, ids, ev = setup(client)
    b = request(ev["core/i_preview.py"])
    if change == "unverified":
        b["assertions"][0]["semantic_decision"] = "unverified"
    if change == "missing":
        b["assertions"].pop()
    if change == "mismatch":
        b["assertions"][0]["text"] = "另一个断言"
    if change == "no_human":
        b["human_confirmed"] = False
    assert (
        client.post(f"/api/v1/capabilities/{ids[0]}/review", headers=ADMIN, json=b).status_code
        == 422
    )


def test_persistent_wrapper_is_not_underlying_store_proof(client):
    _, sid, ids, ev = setup(client)
    b = request(ev["core/h_engine.py"])
    c = b["content"]
    c["action"] = "持久化幂等去重"
    with session_factory()() as db:
        symbol = db.scalar(
            select(CodeSymbol)
            .join(SourceFile, SourceFile.id == CodeSymbol.file_id)
            .where(SourceFile.snapshot_id == sid, SourceFile.path == "storage/durable.py")
        )
        dependency = {
            "file_id": symbol.file_id,
            "symbol_id": symbol.id,
            "evidence_ids": [ev["storage/durable.py"]],
        }
    b["assertions"] = [
        {
            "field": k,
            "text": v,
            "evidence_ids": [ev["core/h_engine.py"]],
            "semantic_decision": "supported",
            "rationale": "人工尝试，但底层store未提供",
            "dependencies": [dependency],
            "implementation_evidence_ids": [ev["storage/durable.py"]],
        }
        for k, v in fields(c).items()
    ]
    r = client.post(f"/api/v1/capabilities/{ids[0]}/validate", headers=ADMIN, json=b)
    assert r.status_code == 200
    assert "DEPENDENCY_EVIDENCE_GAP" in r.json()["errors"]
    assert any(d.get("symbol") == "put_if_absent" for d in r.json()["dependency_gaps"])
    assert (
        client.post(f"/api/v1/capabilities/{ids[0]}/review", headers=ADMIN, json=b).status_code
        == 422
    )


def test_review_modify_reject_stale_and_roles(client):
    _, sid, ids, ev = setup(client)
    b = request(ev["core/i_preview.py"])
    assert (
        client.post(f"/api/v1/capabilities/{ids[0]}/review", headers=READ, json=b).status_code
        == 403
    )
    r = accept(client, ids[0], b)
    assert (
        client.post(f"/api/v1/capabilities/{ids[0]}/review", headers=ADMIN, json=b).status_code
        == 409
    )
    b.update(expected_revision=1, action="modify")
    modified = accept(client, ids[0], b)
    assert publish(client, sid, [r["id"]]).status_code == 422
    assert publish(client, sid, [modified["id"]]).status_code == 422
    b.update(expected_revision=2, action="reject")
    assert accept(client, ids[0], b)["decision"] == "reject"


def test_product_requires_wiring_evidence(client):
    _, _, ids, ev = setup(client)
    b = request(ev["core/i_preview.py"])
    b["declaration_type"] = "product_function"
    assert (
        client.post(f"/api/v1/capabilities/{ids[0]}/review", headers=ADMIN, json=b).status_code
        == 422
    )


def test_atomic_publish_current_read_update_and_rollback(client):
    cid, sid, ids, ev = setup(client)
    first = accept(client, ids[0], request(ev["core/i_preview.py"]))
    assert (
        client.get("/api/v1/capabilities", headers=READ, params={"category_id": cid}).status_code
        == 409
    )
    p1 = publish(client, sid, [first["id"]])
    assert p1.status_code == 200, p1.text
    pid = p1.json()["publication_id"]
    before = client.get("/api/v1/capabilities", headers=READ, params={"category_id": cid}).json()
    assert before["capabilities"][0]["review_status"] == "human_accepted"
    assert (
        client.get(
            f"/api/v1/evidence/{ev['core/i_preview.py']}", headers=READ, params={"category_id": cid}
        ).status_code
        == 200
    )
    assert (
        client.get(
            f"/api/v1/evidence/{ev['core/h_engine.py']}", headers=READ, params={"category_id": cid}
        ).status_code
        == 404
    )
    assert (
        client.get(
            f"/api/v1/evidence/{ev['core/i_preview.py']}",
            headers=READ,
            params={"category_id": cid, "snapshot_id": sid},
        ).status_code
        == 403
    )
    second = accept(client, ids[1], request(ev["core/i_preview.py"], "同机制别名"))
    assert publish(client, sid, [second["id"]]).status_code == 409
    p2 = publish(client, sid, [second["id"]], pid)
    assert p2.status_code == 200
    assert (
        client.get("/api/v1/capabilities", headers=READ, params={"category_id": cid}).json()[
            "capabilities"
        ][0]["id"]
        == ids[1]
    )
    r = client.post(
        f"/api/v1/categories/{cid}/rollback",
        headers=ADMIN,
        json={
            "publication_id": pid,
            "expected_active_publication_id": p2.json()["publication_id"],
            "comment": "恢复已审核旧清单",
        },
    )
    assert r.status_code == 200, r.text
    assert (
        client.get("/api/v1/capabilities", headers=READ, params={"category_id": cid}).json()
        == before
    )


def test_publication_failure_retains_pointer_and_no_partial_record(client, monkeypatch):
    from app.knowledge import publisher

    cid, sid, ids, ev = setup(client)
    r = accept(client, ids[0], request(ev["core/i_preview.py"]))
    pid = publish(client, sid, [r["id"]]).json()["publication_id"]
    original = publisher.coverage

    def fail(db, snapshot_id):
        raise AppError(422, "CONTROLLED_FAILURE", "受控失败")

    from app.core.security import AppError

    monkeypatch.setattr(publisher, "coverage", fail)
    assert publish(client, sid, [r["id"]], pid).status_code == 422
    with session_factory()() as db:
        assert db.get(Category, cid).active_publication_id == pid
        assert db.scalar(select(func.count()).select_from(Publication)) == 1
        assert db.scalar(select(func.count()).select_from(PublicationSwitch)) == 1
    monkeypatch.setattr(publisher, "coverage", original)


def test_same_as_merge_preserves_scope_no_union(client):
    _, sid, ids, ev = setup(client)
    a = accept(client, ids[0], request(ev["core/i_preview.py"]))
    b = accept(client, ids[1], request(ev["core/i_preview.py"], "别名"))
    body = {
        "source_claim_id": ids[1],
        "target_claim_id": ids[0],
        "source_revision": 1,
        "target_revision": 1,
        "relation_type": "same_as",
        "human_confirmed": True,
        "comment": "边界完全一致，保留别名谱系",
    }
    r = client.post("/api/v1/capabilities/merge", headers=ADMIN, json=body)
    assert r.status_code == 200, r.text
    assert r.json()["merged_into"] == ids[0]
    assert publish(client, sid, [b["id"]]).status_code == 422
    assert publish(client, sid, [a["id"]]).status_code == 200


def test_merge_rejects_different_limits(client):
    _, _, ids, ev = setup(client)
    accept(client, ids[0], request(ev["core/i_preview.py"]))
    b = request(ev["core/i_preview.py"])
    b["content"]["limitations"] = ["另一个限制"]
    b["assertions"] = [
        {
            "field": k,
            "text": v,
            "evidence_ids": [ev["core/i_preview.py"]],
            "semantic_decision": "supported",
            "rationale": "人工范围核对",
        }
        for k, v in fields(b["content"]).items()
    ]
    accept(client, ids[1], b)
    r = client.post(
        "/api/v1/capabilities/merge",
        headers=ADMIN,
        json={
            "source_claim_id": ids[1],
            "target_claim_id": ids[0],
            "source_revision": 1,
            "target_revision": 1,
            "relation_type": "same_as",
            "human_confirmed": True,
            "comment": "错误扩大范围",
        },
    )
    assert r.status_code == 422


def test_cross_product_evidence_and_rollback_rejected(client):
    cid, sid, ids, ev = setup(client)
    other_cid, _, other_ids, other_ev = setup(client)
    b = request(other_ev["core/i_preview.py"])
    assert (
        client.post(f"/api/v1/capabilities/{ids[0]}/review", headers=ADMIN, json=b).status_code
        == 404
    )
    a = accept(client, ids[0], request(ev["core/i_preview.py"]))
    pid = publish(client, sid, [a["id"]]).json()["publication_id"]
    assert (
        client.post(
            f"/api/v1/categories/{other_cid}/rollback",
            headers=ADMIN,
            json={
                "publication_id": pid,
                "expected_active_publication_id": None,
                "comment": "跨品类不允许",
            },
        ).status_code
        == 404
    )
    assert (
        client.get(
            f"/api/v1/capabilities/{ids[0]}", headers=READ, params={"category_id": other_cid}
        ).status_code
        != 200
    )


def test_coverage_never_conflates_read_with_semantic_coverage(client):
    _, sid, _, _ = setup(client)
    r = client.get(f"/api/v1/snapshots/{sid}/coverage", headers=ADMIN).json()
    assert r["coverage_incomplete"] and r["semantic_coverage"] == "unknown"
    assert r["uninvestigated_files"] and r["unexplained_entrypoints"] and r["unverified_assertions"]


def test_tampered_source_blocks_publish(client, environment):
    cid, sid, ids, ev = setup(client)
    a = accept(client, ids[0], request(ev["core/i_preview.py"]))
    path = environment.source_root / sid / "source" / "core/i_preview.py"
    path.chmod(0o640)
    path.write_text("changed")
    assert publish(client, sid, [a["id"]]).status_code == 409
    with session_factory()() as db:
        assert db.get(Category, cid).active_publication_id is None


@pytest.mark.parametrize(
    "relation_type",
    ["implements", "depends_on", "composes_with", "subset_of", "possible_equivalent", "related_to"],
)
def test_relation_types_never_become_same_as_implicitly(client, relation_type):
    _, _, ids, ev = setup(client)
    a = accept(client, ids[0], request(ev["core/i_preview.py"]))
    body = request(ev["core/h_engine.py"], "条件筛选机制")
    b = accept(client, ids[1], body)
    response = client.post(
        "/api/v1/capability-relations/review",
        headers=ADMIN,
        json={
            "source_revision_id": a["id"],
            "target_revision_id": b["id"],
            "relation_type": relation_type,
            "decision": "accept",
            "evidence_ids": [ev["core/i_preview.py"], ev["core/h_engine.py"]],
            "rationale": "人工比较双方实现",
            "differences": "两侧机制不同，不作合并",
            "input_contract": "明确双方入参",
            "output_contract": "明确双方返回值",
            "conditions_and_errors": "保留不同条件与异常",
            "human_confirmed": True,
        },
    )
    if relation_type in {"implements", "depends_on", "composes_with"}:
        assert response.status_code == 422
    else:
        assert response.status_code == 200, response.text
        assert response.json()["type"] == relation_type
        if relation_type == "possible_equivalent":
            assert response.json()["details"]["status"] == "hypothesis"


def test_publication_requires_coverage_ack_and_refuses_inflight(client):
    cid, sid, ids, ev = setup(client)
    a = accept(client, ids[0], request(ev["core/i_preview.py"]))
    b = {
        "revision_ids": [a["id"]],
        "expected_active_publication_id": None,
        "comment": "未确认覆盖缺口",
    }
    assert client.post(f"/api/v1/snapshots/{sid}/publish", headers=ADMIN, json=b).status_code == 422
    with session_factory().begin() as db:
        db.add(KnowledgeJob(category_id=cid, snapshot_id=sid, status="queued"))
    assert publish(client, sid, [a["id"]]).status_code == 409


def test_rollback_with_stale_expected_pointer_fails(client):
    cid, sid, ids, ev = setup(client)
    a = accept(client, ids[0], request(ev["core/i_preview.py"]))
    pid = publish(client, sid, [a["id"]]).json()["publication_id"]
    assert (
        client.post(
            f"/api/v1/categories/{cid}/rollback",
            headers=ADMIN,
            json={
                "publication_id": pid,
                "expected_active_publication_id": None,
                "comment": "过期指针",
            },
        ).status_code
        == 409
    )


def test_failed_late_transaction_does_not_switch_or_insert(client, monkeypatch):
    from sqlalchemy import event
    from sqlalchemy.orm import Session

    from app.core.security import AppError

    cid, sid, ids, ev = setup(client)
    a = accept(client, ids[0], request(ev["core/i_preview.py"]))

    def reject_switch(session, flush_context, instances):
        if any(isinstance(item, PublicationSwitch) for item in session.new):
            raise AppError(422, "INJECTED_ATOMIC_FAILURE", "在指针赋值后、事务提交前受控失败")

    event.listen(Session, "before_flush", reject_switch)
    try:
        assert publish(client, sid, [a["id"]]).status_code == 422
    finally:
        event.remove(Session, "before_flush", reject_switch)
    with session_factory()() as db:
        assert db.get(Category, cid).active_publication_id is None
        assert db.get(Category, cid).active_snapshot_id is None
        assert db.scalar(select(func.count()).select_from(Publication)) == 0


def test_larger_fixture_contains_config_api_callbacks_and_wiring_coverage(client):
    from tests.integration.test_p12_index_retrieval import tool

    cid, sid = indexed(
        client, code="p14-large", data=Path("tests/fixtures/p14/source.zip").read_bytes()
    )
    with session_factory()() as db:
        assert (
            db.scalar(
                select(func.count()).select_from(SourceFile).where(SourceFile.snapshot_id == sid)
            )
            == 17
        )
    for name in ["install", "register_routes", "notify_callbacks", "subscribe", "on_audit"]:
        response = tool(client, cid, sid, "search_symbols", query=name)
        assert response.status_code == 200 and response.json()["results"]
    coverage = client.get(f"/api/v1/snapshots/{sid}/coverage", headers=ADMIN).json()
    assert coverage["source_file_count"] == 17
    assert coverage["uninvestigated_files"] and coverage["unexplained_entrypoints"]
    assert coverage["semantic_coverage"] == "unknown"


def test_normal_budget_stop_does_not_report_module_failure(client):
    from app.agents.runtime import run_builder
    from tests.integration.test_p13_autonomous import model
    from tests.integration.test_p13_autonomous import setup as auto_setup

    _, _, jid, _ = auto_setup(client, count=1)
    m, _ = model()
    run_builder(jid, "auto-test", m)
    with session_factory()() as db:
        job = db.get(KnowledgeJob, jid)
        assert job.status == "partial" and job.error_code is None
        assert job.metrics_json["partial_reason"] == "fragment_budget"


def test_actual_runtime_failure_keeps_stable_error(client):
    from app.agents.runtime import run_builder
    from tests.integration.test_p13_autonomous import model
    from tests.integration.test_p13_autonomous import setup as auto_setup

    _, _, jid, _ = auto_setup(client)
    m, _ = model(fail_at=2)
    run_builder(jid, "auto-test", m)
    with session_factory()() as db:
        job = db.get(KnowledgeJob, jid)
        assert job.error_code == "BUILDER_TIMEOUT"
        assert job.metrics_json["partial_reason"] == "failed"


def test_unrelated_non_test_source_is_not_wiring_proof(client):
    _, _, ids, ev = setup(client)
    body = request(ev["core/i_preview.py"])
    body["declaration_type"] = "product_function"
    body["reachability_evidence_ids"] = [ev["core/h_engine.py"]]
    assert (
        client.post(f"/api/v1/capabilities/{ids[0]}/review", headers=ADMIN, json=body).status_code
        == 422
    )


def test_high_risk_claim_cannot_hide_behind_unrelated_disclaimer(client):
    _, _, ids, ev = setup(client)
    body = request(ev["core/i_preview.py"])
    body["content"]["action"] = "保证持久化，认证未知"
    body["assertions"] = [
        {
            "field": k,
            "text": v,
            "evidence_ids": [ev["core/i_preview.py"]],
            "semantic_decision": "supported",
            "rationale": "试图用不相关否定绕过",
        }
        for k, v in fields(body["content"]).items()
    ]
    response = client.post(f"/api/v1/capabilities/{ids[0]}/validate", headers=ADMIN, json=body)
    assert "HIGH_RISK_IMPLEMENTATION_GAP" in response.json()["errors"]


def test_review_changes_do_not_mutate_existing_publication(client):
    cid, sid, ids, ev = setup(client)
    b = request(ev["core/i_preview.py"])
    a = accept(client, ids[0], b)
    publish(client, sid, [a["id"]])
    before = client.get("/api/v1/capabilities", headers=READ, params={"category_id": cid}).json()
    b.update(action="return", expected_revision=1)
    accept(client, ids[0], b)
    assert (
        client.get("/api/v1/capabilities", headers=READ, params={"category_id": cid}).json()
        == before
    )
