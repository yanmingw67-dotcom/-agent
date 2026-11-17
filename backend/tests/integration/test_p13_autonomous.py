"""仅无外网调度反例；真实模型验收另有独立脚本和原始轨迹。"""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic_ai.messages import ModelResponse, ToolCallPart, UserPromptPart
from pydantic_ai.models.function import FunctionModel
from sqlalchemy import func, select

from app.agents.autonomous import add_task, reuse_completed
from app.agents.runtime import BuilderDeps, run_builder
from app.core.security import AppError
from app.db.index_models import Module, SourceFile
from app.db.knowledge_models import CapabilityClaim, ClaimEvidenceLink, Investigation
from app.db.models import KnowledgeJob
from app.db.session import session_factory
from tests.conftest import ADMIN
from tests.integration.test_p12_index_retrieval import indexed, run_job
from tests.integration.test_p13_builder import context, job_row, no_external_models  # noqa: F401

FIXTURE = Path("tests/fixtures/p13_autonomous")


def setup(client, count=3, equivalence=False):
    cid, sid = indexed(client, data=(FIXTURE / "source.zip").read_bytes())
    with session_factory()() as db:
        mid = db.scalar(select(Module.id).where(Module.snapshot_id == sid, Module.name == "api"))
    response = client.post(
        f"/api/v1/snapshots/{sid}/investigate",
        headers=ADMIN,
        json={
            "module_ids": [mid],
            "investigation_mode": "autonomous",
            "exploration_max_fragments": count,
            "include_equivalence": equivalence,
        },
    )
    assert response.status_code == 202, response.text
    jid = response.json()["job_id"]
    with session_factory().begin() as db:
        db.get(KnowledgeJob, jid).dispatch_token = "auto-test"
        rid = db.scalar(select(Investigation.id).where(Investigation.job_id == jid))
    return cid, sid, jid, rid


def prompt(messages):
    return json.loads(
        next(p.content for m in messages for p in m.parts if isinstance(p, UserPromptPart))
    )


def model(fail_at=None, no_candidate=False, combine_initial=False):
    calls = []

    async def respond(messages, info):
        p = prompt(messages)
        name = info.output_tools[0].name
        calls.append((name, p))
        if fail_at and len(calls) == fail_at:
            raise TimeoutError("无外网后续故障")
        if name == "plan_investigation":
            chosen = next(t for t in p["tasks"] if t["symbol"] == "handle_events")
            data = {
                "proposal": {
                    "task_ids": [chosen["id"]],
                    "search_queries": [],
                    "reason": "先入口，再由真实引用扩展",
                }
            }
            if combine_initial:
                source = p["initial_source"]
                data["proposal"]["initial_result"] = {
                    "candidate": {
                        "name": source["path"],
                        "action": "按模式分发",
                        "target": "事件",
                        "business_goal": "分发事件",
                        "mechanism": "按mode选择委托函数",
                        "start_line": source["start_line"],
                        "end_line": source["end_line"],
                        "limitations": ["被调函数未核验"],
                    },
                    "observation": "规划同时完成首片段",
                    "follow_symbols": ["process_batch", "preview_unique"],
                    "search_queries": [],
                }
        elif name == "finish_fragment":
            source = p["source"]
            follows = (
                ["process_batch", "preview_unique"] if source["path"].startswith("api/") else []
            )
            candidate = (
                None
                if no_candidate
                else {
                    "name": source["path"],
                    "action": "记录直接源码机制",
                    "target": source["path"],
                    "business_goal": "调查事件处理",
                    "mechanism": source["snippet"][:400],
                    "start_line": source["start_line"],
                    "end_line": source["end_line"],
                    "limitations": ["仅源码机制，持久性和调用可达性未核验"],
                }
            )
            data = {
                "result": {
                    "candidate": candidate,
                    "observation": "当前片段已分析",
                    "follow_symbols": follows,
                    "search_queries": [],
                }
            }
        else:
            keys = [c["key"] for c in p["candidates"]]
            data = {
                "comparison": {
                    "source_key": keys[0],
                    "target_key": keys[1],
                    "verdict": "not_equivalent",
                    "input_contract": "tenant与id，另一侧仅id",
                    "output_contract": "提交过滤和本地预览",
                    "conditions_and_errors": "存储和配置前提不同",
                    "rationale": "不同作用范围不能互换",
                    "gaps": ["持久store实现未提供"],
                }
            }
        return ModelResponse(parts=[ToolCallPart(name, data)])

    return FunctionModel(respond), calls


def state(rid):
    with session_factory()() as db:
        return db.get(Investigation, rid).metrics_json["exploration"]


def test_feedback_finds_eighth_file_beyond_line_100_and_cross_module(client):
    _, _, jid, rid = setup(client)
    m, calls = model()
    run_builder(jid, "auto-test", m)
    s = state(rid)
    deep = [t for t in s["tasks"] if t["path"] == "core/h_engine.py" and t["status"] == "done"]
    assert deep and deep[0]["start_line"] > 100
    assert any(t["path"] == "core/i_preview.py" and t["status"] == "done" for t in s["tasks"])
    assert len({t["module_id"] for t in s["tasks"] if t["status"] == "done"}) >= 2
    assert job_row(jid).status == "partial"
    assert s["stop_reason"] == "fragment_budget"
    assert s["coverage"]["coverage_incomplete"]
    assert any(t["status"] == "pending" for t in s["tasks"])
    assert all("h_engine" not in str(p) for name, p in calls if name == "plan_investigation")
    with session_factory()() as db:
        assert db.scalar(select(func.count()).select_from(CapabilityClaim)) == 3
        assert db.scalar(select(func.count()).select_from(ClaimEvidenceLink)) == 3


def test_retry_skips_completed_fragment_and_planning(client):
    _, _, jid, rid = setup(client)
    m, calls = model(fail_at=3)
    run_builder(jid, "auto-test", m)
    assert job_row(jid).status == "partial"
    assert sum(t["status"] == "done" for t in state(rid)["tasks"]) == 1
    assert client.post(f"/api/v1/jobs/{jid}/retry", headers=ADMIN).status_code == 202
    with session_factory().begin() as db:
        db.get(KnowledgeJob, jid).dispatch_token = "retry-auto"
    m2, resumed = model()
    run_builder(jid, "retry-auto", m2)
    assert not any(name == "plan_investigation" for name, _ in resumed)
    assert not any(p.get("source", {}).get("path") == "api/endpoint.py" for _, p in resumed)
    assert job_row(jid).metrics_json["model_requests"] > len(resumed)
    with session_factory()() as db:
        assert db.scalar(select(func.count()).select_from(CapabilityClaim)) == 3


def test_no_candidate_is_also_checkpointed(client):
    _, _, jid, rid = setup(client, count=1)
    m, _ = model(no_candidate=True)
    run_builder(jid, "auto-test", m)
    assert sum(t["status"] == "done" for t in state(rid)["tasks"]) == 1
    assert job_row(jid).status == "partial"
    with session_factory()() as db:
        assert db.scalar(select(func.count()).select_from(CapabilityClaim)) == 0


def test_equivalence_reads_two_sources_and_rejects_false_similarity(client):
    _, _, jid, rid = setup(client, equivalence=True)
    m, calls = model()
    run_builder(jid, "auto-test", m)
    eq = state(rid)["equivalence"]
    assert eq["status"] == "investigated"
    assert eq["verdict"] == "not_equivalent"
    assert len(eq["source_locations"]) >= 2
    assert all(v["snippet"] for v in eq["source_locations"])
    assert any(name == "assess_equivalence" for name, _ in calls)


def test_task_request_budget_not_multiplied_by_modules(client, environment):
    cid, sid, jid, rid = setup(client)
    object.__setattr__(environment, "builder_max_requests", 2)
    m, calls = model()
    run_builder(jid, "auto-test", m)
    assert len(calls) <= 2
    assert job_row(jid).status == "partial"
    assert state(rid)["stop_reason"] == "task_request_budget"
    with session_factory().begin() as db:
        row = db.get(KnowledgeJob, jid)
        row.status = "running"
        row.dispatch_token = "budget-check"
        other = Investigation(
            job_id=jid, snapshot_id=sid, module_id="test-other", metrics_json={"model_requests": 2}
        )
        # 在同模块之外创建独立调查记录，验证累计检查；不发模型请求。
        other.module_id = db.scalar(
            select(Module.id).where(Module.snapshot_id == sid, Module.name == "core")
        )
        db.add(other)
    deps = BuilderDeps(rid, "budget-check", context(cid, sid, jid))
    with pytest.raises(AppError):
        deps.update("model_request", {}, model_requests=1)


def test_fragment_identity_includes_hash_and_contained_reads_are_deduplicated():
    f = SimpleNamespace(
        id="f",
        sha256="a",
        sensitive=False,
        line_count=300,
        parse_status="parsed",
        path="module/file.py",
        module_id="m",
    )
    s = {"tasks": [], "deferred": []}
    add_task(s, f, 120, 150, 40, "symbol")
    s["tasks"][0]["status"] = "done"
    add_task(s, f, 125, 140, 100, "reference")
    assert len(s["tasks"]) == 1
    f.sha256 = "b"
    add_task(s, f, 120, 150, 40, "changed_snapshot_content")
    assert len(s["tasks"]) == 2


def test_new_fixed_fixture_can_be_approved_but_foreign_document_cannot(client):
    _, sid, _, _ = setup(client)
    # approval requires no active job; leave task failed without model invocation.
    with session_factory().begin() as db:
        for j in db.scalars(select(KnowledgeJob).where(KnowledgeJob.snapshot_id == sid)):
            j.status = "failed"
    response = client.post(f"/api/v1/snapshots/{sid}/approve-public-fixture", headers=ADMIN)
    assert response.status_code == 200, response.text
    with session_factory()() as db:
        assert len(list(db.scalars(select(SourceFile).where(SourceFile.snapshot_id == sid)))) == 12
    with session_factory()() as db:
        cid = db.scalar(
            select(KnowledgeJob.category_id).where(KnowledgeJob.snapshot_id == sid).limit(1)
        )
    uploaded = client.post(
        f"/api/v1/categories/{cid}/documents",
        headers=ADMIN,
        data={"snapshot_id": sid},
        files={"file": ("private.md", b"private manual", "text/markdown")},
    )
    run_job(uploaded.json()["job_id"])
    assert (
        client.post(f"/api/v1/snapshots/{sid}/approve-public-fixture", headers=ADMIN).status_code
        == 403
    )


def test_same_job_cross_module_fragment_reused_without_model(client):
    cid, sid, jid, rid = setup(client)
    m, _ = model()
    run_builder(jid, "auto-test", m)
    deep = next(
        t for t in state(rid)["tasks"] if t["path"] == "core/h_engine.py" and t["status"] == "done"
    )
    with session_factory().begin() as db:
        job = db.get(KnowledgeJob, jid)
        job.status = "running"
        job.dispatch_token = "reuse"
        mid = db.scalar(select(Module.id).where(Module.snapshot_id == sid, Module.name == "core"))
        other = Investigation(job_id=jid, snapshot_id=sid, module_id=mid)
        db.add(other)
        db.flush()
        other_id = other.id
    item = {**deep, "status": "pending"}
    s = {"tasks": [item]}
    deps = BuilderDeps(other_id, "reuse", context(cid, sid, jid))
    assert reuse_completed(deps, mid, s, item)
    assert item["reused_from_investigation"] == rid
    with session_factory()() as db:
        assert db.get(Investigation, other_id).metrics_json.get("model_requests", 0) == 0


def test_autonomous_worker_rejects_forged_public_before_network(client, environment, monkeypatch):
    import httpx
    from pydantic import SecretStr

    _, _, jid, _ = setup(client)
    object.__setattr__(environment, "builder_model", "approved-model")
    object.__setattr__(environment, "builder_base_url", "https://example.invalid")
    object.__setattr__(environment, "builder_api_key", SecretStr("test-only-not-a-real-key"))
    object.__setattr__(environment, "builder_allow_synthetic", True)
    object.__setattr__(environment, "builder_allow_enterprise", False)

    def no_network(*args, **kwargs):
        raise AssertionError("普通企业样例不得外发")

    monkeypatch.setattr(httpx.AsyncClient, "send", no_network)
    with session_factory().begin() as db:
        job = db.get(KnowledgeJob, jid)
        job.metrics_json = {**job.metrics_json, "data_classification": "synthetic_public"}
    run_builder(jid, "auto-test")
    assert job_row(jid).error_code == "MODEL_DATA_NOT_AUTHORIZED"
    assert job_row(jid).metrics_json["model_requests"] == 0


def test_overlapping_symbol_windows_do_not_repeat_analyzed_lines():
    f = SimpleNamespace(
        id="file",
        sha256="hash",
        sensitive=False,
        line_count=400,
        parse_status="parsed",
        path="core/service.py",
        module_id="core",
    )
    s = {"tasks": [], "deferred": []}
    add_task(s, f, 120, 150, 40, "method")
    s["tasks"][0]["status"] = "done"
    add_task(s, f, 100, 220, 70, "enclosing_class")
    ranges = [(t["start_line"], t["end_line"]) for t in s["tasks"]]
    assert sorted(ranges) == [(100, 119), (120, 150), (151, 220)]


def test_trace_event_names_fit_postgresql_column():
    import ast

    from app.db.knowledge_models import InvestigationTrace

    limit = InvestigationTrace.__table__.c.kind.type.length
    for path in Path("app/agents").glob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            fn = node.func
            event = (
                node.args[0]
                if isinstance(fn, ast.Attribute) and fn.attr == "update"
                else (
                    node.args[2]
                    if isinstance(fn, ast.Name) and fn.id == "persist" and len(node.args) >= 3
                    else None
                )
            )
            if isinstance(event, ast.Constant) and isinstance(event.value, str):
                assert len(event.value) <= limit, (path, event.value)


def test_planning_and_initial_bounded_candidate_share_one_model_call(client, environment):
    _, _, jid, rid = setup(client, equivalence=True)
    object.__setattr__(environment, "builder_max_requests", 4)
    m, calls = model(combine_initial=True)
    run_builder(jid, "auto-test", m)
    assert len(calls) == 4
    assert [name for name, _ in calls].count("finish_fragment") == 2
    assert not any(p.get("source", {}).get("path") == "api/endpoint.py" for _, p in calls)
    assert sum(t["status"] == "done" for t in state(rid)["tasks"]) == 3
    assert state(rid)["equivalence"]["status"] == "investigated"
    with session_factory()() as db:
        assert db.scalar(select(func.count()).select_from(CapabilityClaim)) == 3
        assert db.scalar(select(func.count()).select_from(ClaimEvidenceLink)) == 3


def test_initial_planner_does_not_reanalyze_sibling_completed_fragment(client):
    import asyncio

    from app.agents.autonomous import plan

    cid, sid, jid, rid = setup(client)
    m, _ = model()
    run_builder(jid, "auto-test", m)
    deep = next(
        t for t in state(rid)["tasks"] if t["path"] == "core/h_engine.py" and t["status"] == "done"
    )
    with session_factory().begin() as db:
        job = db.get(KnowledgeJob, jid)
        job.status = "running"
        job.dispatch_token = "reuse-planning"
        mid = db.scalar(select(Module.id).where(Module.snapshot_id == sid, Module.name == "core"))
        other = Investigation(job_id=jid, snapshot_id=sid, module_id=mid)
        db.add(other)
        db.flush()
        other_id = other.id
    deps = BuilderDeps(other_id, "reuse-planning", context(cid, sid, jid))
    s = {"tasks": [{**deep, "status": "pending"}], "planned": False}

    async def no_model(messages, info):
        raise AssertionError("已分析片段不得再次调用规划/分析模型")

    asyncio.run(plan(deps, FunctionModel(no_model), s))
    assert s["planned"] and s["tasks"][0]["status"] == "done"
    with session_factory()() as db:
        assert db.get(Investigation, other_id).metrics_json.get("model_requests", 0) == 0
