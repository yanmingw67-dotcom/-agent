"""无外网反例回归；此文件模拟响应只测恢复边界，绝不用于真实模型效果验收。"""

import io
import zipfile
from pathlib import Path

from pydantic import SecretStr
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel
from sqlalchemy import func, select

from app.agents.runtime import run_builder
from app.db.index_models import Document
from app.db.knowledge_models import CapabilityClaim, ClaimEvidenceLink, Evidence, Investigation
from app.db.models import AuditEvent, KnowledgeJob, Snapshot
from app.db.session import session_factory
from app.knowledge.classification import classification
from app.knowledge.evidence_registry import read_evidence
from tests.conftest import ADMIN, READ
from tests.integration.test_p12_index_retrieval import indexed, run_job
from tests.integration.test_p13_builder import (  # noqa: F401
    context,
    job_row,
    no_external_models,
    setup_job,
)


def bounded_job(client, cid, sid, count=1):
    jid, rid, mid = setup_job(client, cid, sid)
    with session_factory().begin() as db:
        job = db.get(KnowledgeJob, jid)
        job.metrics_json = {
            **job.metrics_json,
            "investigation_mode": "bounded",
            "capability_limit": count,
            "include_equivalence": False,
        }
    return jid, rid, mid


def draft(name="格式化通知"):
    return dict(
        name=name,
        action="格式化事件",
        target="通知事件",
        business_goal="生成消息",
        mechanism="调用format_event",
        start_line=2,
        end_line=3,
        limitations=["未证明网络送达或任意Webhook支持"],
    )


def test_enterprise_request_cannot_lower_classification_or_egress(client, environment, monkeypatch):
    cid, sid = indexed(client)
    jid, _, _ = setup_job(client, cid, sid)
    assert job_row(jid).metrics_json["data_classification"] == "enterprise"
    object.__setattr__(environment, "builder_model", "approved-model")
    object.__setattr__(environment, "builder_base_url", "https://example.invalid")
    object.__setattr__(environment, "builder_api_key", SecretStr("test-only-not-a-real-key"))
    object.__setattr__(environment, "builder_allow_synthetic", True)
    object.__setattr__(environment, "builder_allow_enterprise", False)
    import httpx

    def no_network(*args, **kwargs):
        raise AssertionError("企业快照不允许发起网络请求")

    monkeypatch.setattr(httpx.AsyncClient, "send", no_network)
    # 即使旧任务metrics被降级，worker也重新从来源读取分类。
    with session_factory().begin() as db:
        db.get(KnowledgeJob, jid).metrics_json = {"data_classification": "synthetic_public"}
    run_builder(jid, "p13-test")
    job = job_row(jid)
    assert job.error_code == "MODEL_DATA_NOT_AUTHORIZED"
    assert job.metrics_json["model_requests"] == 0


def test_enterprise_document_overrides_public_source(client):
    cid, sid = indexed(client)
    with session_factory().begin() as db:
        db.get(Snapshot, sid).data_classification = "synthetic_public"
        assert classification(db, sid) == "synthetic_public"
    response = client.post(
        f"/api/v1/categories/{cid}/documents",
        headers=ADMIN,
        data={"snapshot_id": sid},
        files={"file": ("private.md", b"enterprise manual", "text/markdown")},
    )
    run_job(response.json()["job_id"])
    with session_factory()() as db:
        assert classification(db, sid) == "enterprise"
        assert (
            db.scalar(select(Document.data_classification).where(Document.snapshot_id == sid))
            == "enterprise"
        )


def test_only_exact_bundled_fixture_can_be_approved_and_audited(client):
    fixture = Path("tests/fixtures/p13/knowledge_builder_source.zip").read_bytes()
    cid, sid = indexed(client, data=fixture)
    response = client.post(f"/api/v1/snapshots/{sid}/approve-public-fixture", headers=ADMIN)
    assert response.status_code == 200, response.text
    with session_factory()() as db:
        assert classification(db, sid) == "synthetic_public"
        assert (
            db.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(
                    AuditEvent.resource_id == sid,
                    AuditEvent.action == "source.approve_public_fixture",
                )
            )
            == 1
        )
    assert (
        client.post(f"/api/v1/snapshots/{sid}/approve-public-fixture", headers=READ).status_code
        == 403
    )


def test_unrecognized_source_cannot_be_approved(client):
    _, sid = indexed(client)
    assert (
        client.post(f"/api/v1/snapshots/{sid}/approve-public-fixture", headers=ADMIN).status_code
        == 403
    )


def test_tampered_fixture_cannot_be_approved(client):
    source = Path("tests/fixtures/p13/knowledge_builder_source.zip").read_bytes()
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(source)) as old, zipfile.ZipFile(out, "w") as new:
        for name in old.namelist():
            new.writestr(
                name, old.read(name) + (b"\n# modified" if name.endswith("sender.py") else b"")
            )
    _, sid = indexed(client, data=out.getvalue())
    assert (
        client.post(f"/api/v1/snapshots/{sid}/approve-public-fixture", headers=ADMIN).status_code
        == 403
    )


def test_single_compact_output_registers_real_evidence_and_claim(client):
    cid, sid = indexed(client)
    jid, rid, _ = bounded_job(client, cid, sid)

    async def respond(messages, info):
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {"draft": draft()})])

    run_builder(jid, "p13-test", FunctionModel(respond))
    assert job_row(jid).status == "succeeded"
    with session_factory()() as db:
        assert db.scalar(select(func.count()).select_from(CapabilityClaim)) == 1
        assert db.scalar(select(func.count()).select_from(ClaimEvidenceLink)) == 1
        eid = db.scalar(select(Evidence.id))
        assert read_evidence(db, context(cid, sid), eid)["semantic_verification"] == "unverified"
        assert db.get(Investigation, rid).output_json["coverage_incomplete"]


def test_later_timeout_keeps_first_candidate_and_retry_does_not_duplicate(client):
    cid, sid = indexed(client)
    jid, rid, _ = bounded_job(client, cid, sid, count=2)
    calls = 0

    async def respond(messages, info):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise TimeoutError("仅模拟后续模型超时，无外网")
        return ModelResponse(
            parts=[ToolCallPart(info.output_tools[0].name, {"draft": draft(str(calls))})]
        )

    model = FunctionModel(respond)
    run_builder(jid, "p13-test", model)
    assert job_row(jid).status == "partial"
    with session_factory()() as db:
        assert db.scalar(select(func.count()).select_from(CapabilityClaim)) == 1
    assert client.post(f"/api/v1/jobs/{jid}/retry", headers=ADMIN).status_code == 202
    with session_factory().begin() as db:
        db.get(KnowledgeJob, jid).dispatch_token = "retry"
    run_builder(jid, "retry", model)
    assert job_row(jid).status == "succeeded"
    assert calls == 3
    with session_factory()() as db:
        assert db.scalar(select(func.count()).select_from(CapabilityClaim)) == 2


def test_compact_output_cannot_register_outside_supplied_range(client):
    cid, sid = indexed(client)
    jid, _, _ = bounded_job(client, cid, sid)

    async def respond(messages, info):
        bad = draft()
        bad["end_line"] = 9999
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {"draft": bad})])

    run_builder(jid, "p13-test", FunctionModel(respond))
    assert job_row(jid).status == "failed"
    with session_factory()() as db:
        assert db.scalar(select(func.count()).select_from(CapabilityClaim)) == 0
        assert db.scalar(select(func.count()).select_from(Evidence)) == 0


def test_foreign_document_prevents_fixture_downgrade(client):
    fixture = Path("tests/fixtures/p13/knowledge_builder_source.zip").read_bytes()
    cid, sid = indexed(client, data=fixture)
    response = client.post(
        f"/api/v1/categories/{cid}/documents",
        headers=ADMIN,
        data={"snapshot_id": sid},
        files={"file": ("private.md", b"private enterprise text", "text/markdown")},
    )
    run_job(response.json()["job_id"])
    assert (
        client.post(f"/api/v1/snapshots/{sid}/approve-public-fixture", headers=ADMIN).status_code
        == 403
    )
    with session_factory()() as db:
        assert classification(db, sid) == "enterprise"
        assert db.get(Snapshot, sid).data_classification == "enterprise"


def test_unavailable_model_creates_no_candidate(client):
    cid, sid = indexed(client)
    jid, _, _ = bounded_job(client, cid, sid)

    async def respond(messages, info):
        raise TimeoutError("受控模型不可用")

    run_builder(jid, "p13-test", FunctionModel(respond))
    assert job_row(jid).status == "failed"
    assert job_row(jid).metrics_json["usage_incomplete"]
    with session_factory()() as db:
        assert db.scalar(select(func.count()).select_from(CapabilityClaim)) == 0
