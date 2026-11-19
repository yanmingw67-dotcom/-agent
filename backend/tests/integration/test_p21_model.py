"""R02：有界模型辅助的 FunctionModel 管线测试，不代表真实模型质量验收。"""

import json
from uuid import uuid4

from pydantic_ai.messages import ModelResponse, ToolCallPart, UserPromptPart
from pydantic_ai.models.function import FunctionModel

from app.db.models import AuditEvent, Category
from app.db.session import session_factory
from app.db.tender_models import TenderExtractionJob
from app.tender import service


def prompt_payload(messages):
    for message in messages:
        for part in getattr(message, "parts", []):
            if isinstance(part, UserPromptPart):
                content = part.content
                return json.loads(content if isinstance(content, str) else json.dumps(content))
    raise AssertionError("模型请求缺少用户提示")


def category():
    with session_factory().begin() as db:
        row = Category(code="p21-model-" + uuid4().hex[:10], name="模型辅助测试品类")
        db.add(row)
        db.flush()
        return row.id


def create_job(text):
    with session_factory().begin() as db:
        job, _ = service.create_text_job(
            db, principal_id="model-tester", category_id=category(), text=text
        )
        return job.id


def model_with(payloads, bad_quote=False):
    async def respond(messages, info):
        payload = prompt_payload(messages)
        payloads.append(payload)
        region = next(item for item in payload["regions"] if "量子隧穿" in item["text"])
        rid = region["region_id"]
        quote = "量子隧穿边界待确认"
        suggestions = [
            {
                "region_id": rid,
                "decision": "include",
                "reason": "原文明确描述边界待确认",
                "anchors": [{"region_id": rid, "quote": quote}],
            },
            {
                "region_id": rid,
                "decision": "include",
                "reason": "伪造引文",
                "anchors": [{"region_id": rid, "quote": "原文中不存在的引文"}],
            },
            {
                "region_id": "r-not-provided",
                "decision": "include",
                "reason": "越界区域",
                "anchors": [{"region_id": "r-not-provided", "quote": quote}],
            },
            {
                "region_id": rid,
                "decision": "missing_item",
                "reason": "补充一个原文没有的参数",
                "anchors": [{"region_id": rid, "quote": quote}],
            },
        ]
        if bad_quote:
            suggestions[0]["anchors"][0]["quote"] = "伪造引文"
        return ModelResponse(
            parts=[ToolCallPart(info.output_tools[0].name, {"suggestions": suggestions})]
        )

    return FunctionModel(respond)


def test_function_model_pipeline_accepts_only_verified_anchors(client, environment):
    object.__setattr__(environment, "tender_model_mode", "model")
    object.__setattr__(environment, "builder_allow_enterprise", True)
    object.__setattr__(environment, "builder_allow_synthetic", False)
    object.__setattr__(environment, "tender_model_max_requests", 2)
    payloads = []
    job_id = create_job("量子隧穿边界待确认。\n支持 HTTP 解析。")
    service.run_tender_job(job_id, model=model_with(payloads))
    with session_factory()() as db:
        job = db.get(TenderExtractionJob, job_id)
        audits = db.query(AuditEvent).filter(AuditEvent.action.like("tender.model_%")).all()
    assert payloads
    assert job.status in {"succeeded", "partial"}
    model = job.coverage_json["model"]
    assert model["model_mode"] == "TEST_FUNCTION_MODEL"
    assert model["model_status"] in {"COMPLETED", "COMPLETED_WITH_GAPS"}
    assert model["accepted"] == 1
    assert model["rejected"] >= 3
    parameters = db_parameters(job_id)
    assert any("量子隧穿" in item.original_text for item in parameters)
    assert any(item.original_json.get("model_hints") for item in parameters)
    assert any(item.review_required for item in parameters)
    assert not any("原文中不存在" in item.original_text for item in parameters)
    assert any(item["region_id"] == "r-not-provided" for item in model["gaps"])
    assert audits
    serialized = json.dumps([item.details_json for item in audits], ensure_ascii=False)
    assert "量子隧穿" not in serialized
    assert "支持 HTTP 解析" not in serialized


def test_unavailable_model_is_recorded_as_degraded(client, environment):
    object.__setattr__(environment, "tender_model_mode", "model")
    object.__setattr__(environment, "builder_allow_enterprise", False)
    object.__setattr__(environment, "builder_allow_synthetic", False)
    job_id = create_job("量子隧穿边界待确认。\n支持 HTTP 解析。")
    service.run_tender_job(job_id)
    with session_factory()() as db:
        job = db.get(TenderExtractionJob, job_id)
    model = job.coverage_json["model"]
    assert model["model_status"] == "DEGRADED"
    assert model["degraded"] is True
    assert model["semantic_analysis_completed"] is False
    assert job.coverage_status == "incomplete"
    assert job.status == "partial"


def db_parameters(job_id):
    from sqlalchemy import select

    from app.db.tender_models import TenderParameter

    with session_factory()() as db:
        return list(db.scalars(select(TenderParameter).where(TenderParameter.job_id == job_id)))
