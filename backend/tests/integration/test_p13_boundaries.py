"""来源核验、候选/假设边界和模块失败恢复反例；模型仅FunctionModel。"""

import asyncio
import json

import pytest
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel
from sqlalchemy import select

from app.agents.runtime import BuilderDeps, configured_model, run_builder
from app.core.security import AppError
from app.db.index_models import Module
from app.db.knowledge_models import CapabilityClaim, CapabilityRelation, Investigation
from app.db.models import KnowledgeJob
from app.db.session import session_factory
from app.knowledge.evidence_registry import read_evidence, register
from app.knowledge.schemas import EvidenceRequest, ModuleInvestigationOutput
from app.knowledge.service import save_output, validate_output
from app.retrieval.schemas import ToolRequest
from tests.conftest import ADMIN, make_zip
from tests.integration.test_p12_index_retrieval import indexed, run_job, tool
from tests.integration.test_p13_builder import context, job_row, output, setup_job


def start(client, cid, sid, name="notify"):
    jid, rid, mid = setup_job(client, cid, sid, name)
    with session_factory().begin() as db:
        db.get(KnowledgeJob, jid).status = "running"
        db.get(Investigation, rid).attempt = 1
    return jid, rid, mid


def test_document_evidence_rechecks_original_and_locator(client, environment):
    cid, sid = indexed(client)
    r = client.post(
        f"/api/v1/categories/{cid}/documents",
        headers=ADMIN,
        data={"snapshot_id": sid},
        files={"file": ("notify.txt", b"notify platform manual", "text/plain")},
    )
    run_job(r.json()["job_id"])
    item = tool(client, cid, sid, "search_documents", query="platform").json()["results"][0]
    jid, rid, _ = start(client, cid, sid)
    deps = BuilderDeps(rid, "p13-test", context(cid, sid, jid))
    deps.query(ToolRequest(tool="read_document", segment_id=item["id"]))
    receipt = deps.register(
        EvidenceRequest(
            kind="document",
            segment_id=item["id"],
            expected_sha256=item["sha256"],
            supported_statement="文档平台描述",
        )
    )
    with session_factory()() as db:
        data = read_evidence(db, context(cid, sid), receipt.evidence_id)
        assert data["locator"]["line"] == 1 and data["quote"] == "notify platform manual"
    from app.db.index_models import Document

    with session_factory()() as db:
        key = db.get(Document, item["document_id"]).storage_key
    path = environment.document_store / key
    path.chmod(0o640)
    path.write_bytes(b"changed")
    with session_factory()() as db:
        with pytest.raises(AppError):
            read_evidence(db, context(cid, sid), receipt.evidence_id)


@pytest.mark.parametrize("kind", ["test", "document"])
def test_only_test_or_document_evidence_stays_candidate(client, kind):
    cid, sid = indexed(client)
    if kind == "test":
        file = tool(client, cid, sid, "search_paths", query="tests/test_sender.py").json()[
            "results"
        ][0]
        req = EvidenceRequest(
            kind="source",
            file_id=file["file_id"],
            start_line=1,
            end_line=2,
            expected_sha256=file["sha256"],
            supported_statement="仅测试",
        )
    else:
        r = client.post(
            f"/api/v1/categories/{cid}/documents",
            headers=ADMIN,
            data={"snapshot_id": sid},
            files={"file": ("manual.txt", b"notify manual", "text/plain")},
        )
        run_job(r.json()["job_id"])
        seg = tool(client, cid, sid, "search_documents", query="manual").json()["results"][0]
        req = EvidenceRequest(
            kind="document",
            segment_id=seg["id"],
            expected_sha256=seg["sha256"],
            supported_statement="仅文档",
        )
    jid, rid, mid = start(client, cid, sid)
    with session_factory().begin() as db:
        receipt = register(db, context(cid, sid), req)
        row = db.get(Investigation, rid)
        row.evidence_ids = [receipt.evidence_id]
        result = validate_output(
            db,
            context(cid, sid),
            row,
            ModuleInvestigationOutput.model_validate(output(mid, [receipt.evidence_id])),
        )
        assert result.capabilities[0].status == "candidate"
        assert ("test_only" if kind == "test" else "document_claim_unverified") in " ".join(
            result.capabilities[0].unresolved_questions
        )


def test_relations_persist_only_as_hypotheses_no_auto_merge(client):
    cid, sid = indexed(client)
    jid, rid, mid = start(client, cid, sid)
    data = output(mid)
    second = {**data["capabilities"][0], "key": "webhook", "name": "任意Webhook用途假设"}
    data["capabilities"].append(second)
    data["candidate_relations"] = [
        {
            "source_key": "notify",
            "target_key": "webhook",
            "relation_type": "possible_equivalent",
            "rationale": "待核验载荷和地址配置",
            "unverified_gaps": ["固定平台不代表任意第三方"],
        }
    ]
    with session_factory().begin() as db:
        saved = save_output(
            db,
            context(cid, sid, jid),
            rid,
            "p13-test",
            ModuleInvestigationOutput.model_validate(data),
        )
        assert len(saved["claim_ids"]) == 2
    with session_factory()() as db:
        relation = db.scalar(select(CapabilityRelation))
        assert relation.status == "hypothesis"
        assert len(list(db.scalars(select(CapabilityClaim)))) == 2


def test_flags_are_converted_to_explicit_open_questions(client, environment):
    cid, sid = indexed(
        client, data=make_zip([("one.txt", "match\n" * 100), ("large.txt", "x" * 250)])
    )
    jid, rid, mid = start(client, cid, sid, "__root__")
    deps = BuilderDeps(rid, "p13-test", context(cid, sid, jid))
    page = deps.query(ToolRequest(tool="search_code_batch", query="match", limit=1))
    assert page.truncated
    result = deps.validate(ModuleInvestigationOutput.model_validate(output(mid)))
    assert "truncated" in " ".join(result.open_questions)
    # 未解析引用同样不能从候选升级成可达性证明。


def test_stale_dispatch_cannot_register_or_commit(client):
    cid, sid = indexed(client)
    jid, rid, mid = start(client, cid, sid)
    with session_factory()() as db:
        with pytest.raises(AppError):
            save_output(
                db,
                context(cid, sid, jid),
                rid,
                "stale",
                ModuleInvestigationOutput.model_validate(output(mid)),
            )


def test_model_data_authorization_fails_before_network(client, environment):
    object.__setattr__(environment, "builder_model", "approved-model")
    object.__setattr__(environment, "builder_base_url", "http://127.0.0.1:1/v1")
    from pydantic import SecretStr

    object.__setattr__(environment, "builder_api_key", SecretStr("synthetic-placeholder"))
    object.__setattr__(environment, "builder_allow_enterprise", False)
    with pytest.raises(AppError) as exc:
        configured_model("enterprise")
    assert exc.value.error_code == "MODEL_DATA_NOT_AUTHORIZED"


def test_partial_module_failure_retry_keeps_successful_checkpoint(client):
    cid, sid = indexed(client)
    with session_factory()() as db:
        modules = list(
            db.scalars(
                select(Module).where(Module.snapshot_id == sid, Module.name.in_(["notify", "api"]))
            )
        )
    r = client.post(
        f"/api/v1/snapshots/{sid}/investigate",
        headers=ADMIN,
        json={"module_ids": [m.id for m in modules], "data_classification": "synthetic_public"},
    )
    jid = r.json()["job_id"]
    with session_factory().begin() as db:
        db.get(KnowledgeJob, jid).dispatch_token = "test"
    fail = True
    loops = set()

    async def respond(messages, info):
        loops.add(asyncio.get_running_loop())
        seed = json.loads(messages[0].parts[0].content)
        mid = seed["module_id"]
        if fail and mid == modules[0].id:
            raise RuntimeError("模拟单模块模型失败，非真实网络")
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, output(mid))])

    model = FunctionModel(respond)
    run_builder(jid, "test", model)
    assert job_row(jid).status == "partial"
    assert len(loops) == 1
    with session_factory()() as db:
        completed = db.scalar(
            select(Investigation).where(
                Investigation.job_id == jid, Investigation.status == "succeeded"
            )
        )
        completed_id = completed.id
    fail = False
    assert client.post(f"/api/v1/jobs/{jid}/retry", headers=ADMIN).status_code == 202
    with session_factory().begin() as db:
        db.get(KnowledgeJob, jid).dispatch_token = "retry"
    run_builder(jid, "retry", model)
    assert job_row(jid).status == "succeeded"
    with session_factory()() as db:
        assert db.get(Investigation, completed_id).attempt == 1


def test_retrieval_does_not_register_new_business_writes(client, environment):
    from app.main import create_app

    object.__setattr__(environment, "service_mode", "retrieval")
    paths = {r.path for r in create_app().routes}
    assert "/api/v1/snapshots/{snapshot_id}/investigate" not in paths
    assert "/api/v1/evidence/{evidence_id}" not in paths
