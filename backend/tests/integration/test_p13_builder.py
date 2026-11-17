"""FunctionModel仅用于无外网确定性测试，绝不冒充真实模型能力验收。"""

import json

import pytest
from pydantic import ValidationError
from pydantic_ai import models
from pydantic_ai.messages import ModelResponse, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel
from sqlalchemy import func, select

from app.agents.runtime import BuilderDeps, run_builder
from app.core.security import AppError
from app.db.index_models import Module
from app.db.knowledge_models import CapabilityClaim, Evidence, Investigation, InvestigationTrace
from app.db.models import KnowledgeJob
from app.db.session import session_factory
from app.knowledge.evidence_registry import read_evidence, register
from app.knowledge.schemas import EvidenceRequest, ModuleInvestigationOutput
from app.retrieval.schemas import RunContext, ToolRequest
from tests.conftest import ADMIN, READ
from tests.integration.test_p12_index_retrieval import indexed, tool


@pytest.fixture(autouse=True)
def no_external_models(monkeypatch):
    monkeypatch.setattr(models, "ALLOW_MODEL_REQUESTS", False)
    for key in ["BUILDER_MODEL", "BUILDER_BASE_URL", "BUILDER_API_KEY"]:
        monkeypatch.setenv(key, "")
    monkeypatch.setenv("BUILDER_ALLOW_SYNTHETIC", "false")
    monkeypatch.setenv("BUILDER_ALLOW_ENTERPRISE", "false")


def setup_job(client, cid, sid, name="notify"):
    with session_factory()() as db:
        mid = db.scalar(select(Module.id).where(Module.snapshot_id == sid, Module.name == name))
    response = client.post(
        f"/api/v1/snapshots/{sid}/investigate",
        headers=ADMIN,
        json={"module_ids": [mid], "data_classification": "synthetic_public"},
    )
    assert response.status_code == 202, response.text
    jid = response.json()["job_id"]
    with session_factory().begin() as db:
        db.get(KnowledgeJob, jid).dispatch_token = "p13-test"
        rid = db.scalar(select(Investigation.id).where(Investigation.job_id == jid))
    return jid, rid, mid


def context(cid, sid, jid=None):
    return RunContext(
        category_id=cid, snapshot_id=sid, principal_id="admin", job_id=jid, policy="admin_staging"
    )


def output(mid, eids=None):
    return {
        "module_id": mid,
        "module_summary": "通知模块候选",
        "capabilities": [
            {
                "key": "notify",
                "name": "机器人通知候选",
                "category": "通知",
                "action": "格式化并发送通知",
                "target": "通知事件",
                "business_goal": "通知接收方",
                "implementation_mechanism": "RobotSender.send调用format_event；未证明网络投递",
                "evidence_ids": eids or [],
                "status": "evidence_found",
            }
        ],
        "open_questions": ["HTTP投递与入口可达性需核查"],
    }


def model_for(file, mid, malicious=False, wrong_evidence=False):
    async def respond(messages, info):
        returns = [p for m in messages for p in m.parts if isinstance(p, ToolReturnPart)]
        if not returns:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "query_knowledge",
                        {
                            "request": {
                                "tool": "read_source",
                                "file_id": file["file_id"],
                                "start_line": 2,
                                "end_line": 3,
                            }
                        },
                    )
                ]
            )
        last = returns[-1]
        if last.tool_name == "query_knowledge":
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "register_evidence",
                        {
                            "request": {
                                "kind": "source",
                                "file_id": file["file_id"],
                                "start_line": 2,
                                "end_line": 3,
                                "expected_sha256": file["sha256"],
                                "supported_statement": "格式化通知",
                            }
                        },
                    )
                ]
            )
        receipt = last.content
        if hasattr(receipt, "model_dump"):
            receipt = receipt.model_dump()
        if isinstance(receipt, str):
            receipt = json.loads(receipt)
        data = output(mid, ["ev-forged"] if wrong_evidence else [receipt["evidence_id"]])
        if malicious:
            data["capabilities"][0]["status"] = "approved"
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, data)])

    return FunctionModel(respond)


def job_row(jid):
    with session_factory()() as db:
        return db.get(KnowledgeJob, jid)


def test_real_pydantic_ai_loop_registers_evidence_and_drafts(client):
    cid, sid = indexed(client)
    file = tool(client, cid, sid, "search_paths", query="notify/sender.py").json()["results"][0]
    jid, rid, mid = setup_job(client, cid, sid)
    run_builder(jid, "p13-test", model_for(file, mid))
    job = job_row(jid)
    assert job.status == "succeeded", job.metrics_json
    assert job.metrics_json["model_requests"] == 3
    assert job.metrics_json["model_mode"] == "TEST_FUNCTION_MODEL"
    assert job.metrics_json["model_cost_usd"] is None
    with session_factory()() as db:
        cap = db.scalar(select(CapabilityClaim).where(CapabilityClaim.investigation_id == rid))
        assert cap.status == "evidence_found" and cap.draft_json["unresolved_questions"]
        eid = cap.draft_json["evidence_ids"][0]
        evidence = read_evidence(db, context(cid, sid), eid)
        assert evidence["quote"].endswith("return format_event(event)")
        assert evidence["verification_state"] == "source_verified"
        assert evidence["semantic_verification"] == "unverified"
        trace = list(
            db.scalars(select(InvestigationTrace).where(InvestigationTrace.investigation_id == rid))
        )
        assert any(t.kind == "model_response" for t in trace)
        assert all("return format_event" not in json.dumps(t.details_json) for t in trace)
    assert client.get(f"/api/v1/jobs/{jid}/investigations", headers=ADMIN).json()[0]["result"][
        "claim_ids"
    ]
    assert (
        client.get(
            f"/api/v1/evidence/{eid}",
            headers=ADMIN,
            params={"category_id": cid, "snapshot_id": sid},
        ).status_code
        == 200
    )
    assert (
        client.get(
            f"/api/v1/evidence/{eid}", headers=READ, params={"category_id": cid, "snapshot_id": sid}
        ).status_code
        == 403
    )


@pytest.mark.parametrize("malicious,forged", [(True, False), (False, True)])
def test_model_cannot_forge_ids_or_approve_capabilities(client, malicious, forged):
    cid, sid = indexed(client)
    file = tool(client, cid, sid, "search_paths", query="notify/sender.py").json()["results"][0]
    jid, rid, mid = setup_job(client, cid, sid)
    run_builder(jid, "p13-test", model_for(file, mid, malicious=malicious, wrong_evidence=forged))
    assert job_row(jid).status == "failed"
    with session_factory()() as db:
        assert db.scalar(select(func.count()).select_from(CapabilityClaim)) == 0


def test_retry_reuses_completed_modules_and_avoids_duplicate_claims(client):
    cid, sid = indexed(client)
    file = tool(client, cid, sid, "search_paths", query="notify/sender.py").json()["results"][0]
    jid, rid, mid = setup_job(client, cid, sid)
    model = model_for(file, mid)
    run_builder(jid, "p13-test", model)
    with session_factory().begin() as db:
        job = db.get(KnowledgeJob, jid)
        job.status = "failed"
        before = db.scalar(select(func.count()).select_from(CapabilityClaim))
    assert client.post(f"/api/v1/jobs/{jid}/retry", headers=ADMIN).status_code == 202
    with session_factory().begin() as db:
        db.get(KnowledgeJob, jid).dispatch_token = "second"
    run_builder(jid, "second", model)
    assert job_row(jid).status == "succeeded"
    with session_factory()() as db:
        assert db.scalar(select(func.count()).select_from(CapabilityClaim)) == before
        assert db.get(Investigation, rid).attempt == 1


def test_budget_and_failure_retry_keeps_cost_and_counters(client, environment):
    cid, sid = indexed(client)
    file = tool(client, cid, sid, "search_paths", query="notify/sender.py").json()["results"][0]
    jid, rid, mid = setup_job(client, cid, sid)
    object.__setattr__(environment, "builder_max_requests", 1)
    run_builder(jid, "p13-test", model_for(file, mid))
    assert job_row(jid).status == "failed"
    with session_factory()() as db:
        assert db.get(Investigation, rid).error_code == "BUILDER_BUDGET_EXCEEDED"
        before = db.get(Investigation, rid).metrics_json["model_requests"]
    assert client.post(f"/api/v1/jobs/{jid}/retry", headers=ADMIN).status_code == 202
    with session_factory().begin() as db:
        db.get(KnowledgeJob, jid).dispatch_token = "next"
    run_builder(jid, "next", model_for(file, mid))
    assert job_row(jid).status == "failed"
    with session_factory()() as db:
        assert db.get(Investigation, rid).metrics_json["model_requests"] == before


def test_model_not_configured_is_not_mock_success(client):
    cid, sid = indexed(client)
    jid, _, _ = setup_job(client, cid, sid)
    run_builder(jid, "p13-test")
    assert job_row(jid).error_code == "MODEL_NOT_CONFIGURED"
    assert job_row(jid).metrics_json["model_requests"] == 0


def test_module_and_role_scope_are_not_user_overridable(client):
    cid, sid = indexed(client, "one")
    other, osid = indexed(client, "two")
    with session_factory()() as db:
        foreign = db.scalar(select(Module.id).where(Module.snapshot_id == osid))
    assert (
        client.post(
            f"/api/v1/snapshots/{sid}/investigate", headers=ADMIN, json={"module_ids": [foreign]}
        ).status_code
        == 404
    )
    assert (
        client.post(f"/api/v1/snapshots/{sid}/investigate", headers=READ, json={}).status_code
        == 403
    )
    assert (
        client.get(
            f"/api/v1/snapshots/{sid}/candidates", headers=ADMIN, params={"category_id": other}
        ).status_code
        == 404
    )


def test_evidence_hash_range_scope_and_immutability(client, environment):
    cid, sid = indexed(client)
    file = tool(client, cid, sid, "search_paths", query="notify/sender.py").json()["results"][0]
    request = EvidenceRequest(
        kind="source",
        file_id=file["file_id"],
        start_line=2,
        end_line=3,
        expected_sha256=file["sha256"],
        supported_statement="回读验证",
    )
    with session_factory().begin() as db:
        a = register(db, context(cid, sid), request)
        b = register(db, context(cid, sid), request)
        assert a.evidence_id == b.evidence_id
        assert db.scalar(select(func.count()).select_from(Evidence)) == 1
        with pytest.raises(AppError):
            register(
                db, context(cid, sid), request.model_copy(update={"expected_sha256": "0" * 64})
            )
        with pytest.raises(AppError):
            register(db, context(cid, sid), request.model_copy(update={"end_line": 999}))
    other, osid = indexed(client, "other")
    with session_factory()() as db:
        with pytest.raises(AppError):
            read_evidence(db, context(other, osid), a.evidence_id)
    path = environment.source_root / sid / "source/notify/sender.py"
    path.chmod(0o640)
    path.write_text("altered")
    with session_factory()() as db:
        with pytest.raises(AppError):
            read_evidence(db, context(cid, sid), a.evidence_id)


def test_register_requires_actual_full_read_in_run(client):
    cid, sid = indexed(client)
    file = tool(client, cid, sid, "search_paths", query="notify/sender.py").json()["results"][0]
    jid, rid, _ = setup_job(client, cid, sid)
    with session_factory().begin() as db:
        db.get(KnowledgeJob, jid).status = "running"
        db.get(Investigation, rid).attempt = 1
    deps = BuilderDeps(rid, "p13-test", context(cid, sid, jid))
    req = EvidenceRequest(
        kind="source",
        file_id=file["file_id"],
        start_line=2,
        end_line=3,
        expected_sha256=file["sha256"],
        supported_statement="候选",
    )
    with pytest.raises(AppError, match="登记前"):
        deps.register(req)
    deps.query(ToolRequest(tool="read_source", file_id=file["file_id"], start_line=2, end_line=3))
    assert deps.register(req).evidence_id.startswith("ev-")


def test_schema_rejects_verified_and_unknown_fields():
    with pytest.raises(ValidationError):
        ModuleInvestigationOutput.model_validate({**output("module"), "category_id": "override"})
    data = output("module")
    data["capabilities"][0]["status"] = "verified"
    with pytest.raises(ValidationError):
        ModuleInvestigationOutput.model_validate(data)
