"""模型工具定位失败可恢复，预算/权限失败仍终止。"""

from pydantic_ai.messages import ModelResponse, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel

from app.agents.knowledge_builder import AgentToolEnvelope
from app.agents.runtime import run_builder
from tests.integration.test_p12_index_retrieval import indexed, tool
from tests.integration.test_p13_builder import (  # noqa: F401
    job_row,
    no_external_models,
    output,
    setup_job,
)


def test_invalid_segment_is_retryable_and_followed_by_valid_read(client):
    cid, sid = indexed(client)
    file = tool(client, cid, sid, "search_paths", query="notify/sender.py").json()["results"][0]
    jid, _, mid = setup_job(client, cid, sid)

    async def respond(messages, info):
        retries = [
            p
            for m in messages
            for p in m.parts
            if isinstance(p, ToolReturnPart) and p.outcome == "retried"
        ]
        returns = [
            p
            for m in messages
            for p in m.parts
            if isinstance(p, ToolReturnPart) and p.outcome == "success"
        ]
        if not retries:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "query_knowledge",
                        {
                            "request": {
                                "tool": "read_document",
                                "segment_id": "nonexistent-synthetic-id",
                            }
                        },
                    )
                ]
            )
        if not returns:
            assert "SEGMENT_NOT_FOUND" in retries[-1].content
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
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, output(mid))])

    run_builder(jid, "p13-test", FunctionModel(respond))
    job = job_row(jid)
    from sqlalchemy import select

    from app.db.knowledge_models import Investigation, InvestigationTrace
    from app.db.session import session_factory

    with session_factory()() as db:
        rows = list(
            db.scalars(
                select(InvestigationTrace).join(Investigation).where(Investigation.job_id == jid)
            )
        )
        errors = [
            (r.kind, r.details_json) for r in rows if r.kind in {"module_error", "tool_error"}
        ]
    assert job.status == "succeeded", errors
    assert job.metrics_json["model_requests"] == 3


def test_snapshot_document_fallback_and_reasoning_setting_are_audited(client, environment):
    from tests.conftest import ADMIN
    from tests.integration.test_p12_index_retrieval import run_job

    cid, sid = indexed(client)
    response = client.post(
        f"/api/v1/categories/{cid}/documents",
        headers=ADMIN,
        data={"snapshot_id": sid},
        files={"file": ("manual.md", "机器人通知手册".encode(), "text/markdown")},
    )
    run_job(response.json()["job_id"])
    jid, _, mid = setup_job(client, cid, sid)
    object.__setattr__(environment, "builder_reasoning_effort", "low")

    async def respond(messages, info):
        import json

        seed = json.loads(messages[0].parts[0].content)["untrusted_seed_data"]
        assert not seed[2]["results"]
        assert seed[3]["results"][0]["id"]
        assert "document_id" in seed[3]["results"][0]
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, output(mid))])

    run_builder(jid, "p13-test", FunctionModel(respond))
    assert job_row(jid).status == "succeeded"
    from sqlalchemy import select

    from app.db.knowledge_models import Investigation, InvestigationTrace
    from app.db.session import session_factory

    with session_factory()() as db:
        run = db.scalar(select(Investigation).where(Investigation.job_id == jid))
        assert run.output_json["coverage_incomplete"]
        assert any("相关性" in q for q in run.output_json["output"]["open_questions"])
        request = db.scalar(
            select(InvestigationTrace).where(
                InvestigationTrace.investigation_id == run.id,
                InvestigationTrace.kind == "model_request",
            )
        )
        assert request.details_json["reasoning_effort"] == "low"


def test_nonretryable_security_and_budget_errors_are_not_model_retries():
    import pytest

    from app.agents.knowledge_builder import retryable_tool_error
    from app.core.security import AppError

    for status, code in [
        (403, "SOURCE_RESTRICTED"),
        (409, "EVIDENCE_HASH_MISMATCH"),
        (429, "BUILDER_BUDGET_EXCEEDED"),
    ]:
        error = AppError(status, code, "安全边界")
        with pytest.raises(AppError) as caught:
            retryable_tool_error(error)
        assert caught.value is error


def test_agent_compact_envelope_keeps_complete_flags():
    envelope = AgentToolEnvelope(
        snapshot_id="synthetic",
        tool="search_code_batch",
        coverage_incomplete=True,
        truncated=True,
        next_cursor="0",
    )
    data = envelope.model_dump()
    assert data["truncated"] and data["coverage_incomplete"] and data["next_cursor"] == "0"
    assert "module_context" not in data
