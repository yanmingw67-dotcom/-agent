"""P1.6 文档语义提取：FunctionModel仅用于确定性测试，不代表真实模型验收。"""

import json

from pydantic_ai.messages import ModelResponse, ToolCallPart, UserPromptPart
from pydantic_ai.models.function import FunctionModel
from sqlalchemy import select

from app.db.knowledge_models import CapabilityClaim, DocumentDiscoveryRun
from app.db.models import AuditEvent, KnowledgeJob
from app.db.session import session_factory
from app.knowledge.document_discovery import run_document_discovery
from tests.conftest import ADMIN
from tests.integration.test_p16_discovery import setup


def prompt_payload(messages):
    for message in messages:
        for part in getattr(message, "parts", []):
            if isinstance(part, UserPromptPart):
                content = part.content
                return json.loads(content if isinstance(content, str) else json.dumps(content))
    raise AssertionError("模型请求缺少用户提示")


def unit_with(payload, needle):
    for unit in payload["units"]:
        if needle in unit["text"]:
            return unit
    raise AssertionError(f"没有找到包含 {needle} 的文档单元")


def segment_with(unit, needle):
    for segment in unit["segments"]:
        if needle in segment["text"]:
            return segment["segment_id"]
    raise AssertionError(f"没有找到包含 {needle} 的原生分段")


def synthetic_model(payloads):
    async def respond(messages, info):
        payload = prompt_payload(messages)
        payloads.append(payload)
        bridge = unit_with(payload, "透明桥")
        bypass = unit_with(payload, "旁路镜像")
        exclusion = unit_with(payload, "不支持规则同步至旧版本节点")
        xlsx = unit_with(payload, "SQL 注入")
        bridge_segment = segment_with(bridge, "当前版本支持透明桥")
        bypass_segment = segment_with(bypass, "旁路镜像")
        exclusion_segment = segment_with(exclusion, "不支持规则同步至旧版本节点")
        xlsx_segment = segment_with(xlsx, "支持 SQL 注入与 XSS 攻击检测和阻断")
        candidates = [
            {
                "name": "透明桥部署",
                "action": "支持透明桥接入",
                "target": "透明桥",
                "business_goal": "提供透明桥接入方式",
                "supported_scope": ["透明桥"],
                "conditions": [],
                "limitations": [],
                "implementation_mechanism": "文档未记录底层实现机制",
                "claim_type": "current_capability",
                "anchors": [{"segment_id": bridge_segment, "quote": "当前版本支持透明桥"}],
            },
            {
                "name": "旁路镜像部署",
                "action": "支持旁路镜像接入",
                "target": "旁路镜像",
                "business_goal": "提供旁路镜像接入方式",
                "supported_scope": ["旁路镜像"],
                "conditions": [],
                "limitations": [],
                "implementation_mechanism": "文档未记录底层实现机制",
                "claim_type": "deployment",
                "anchors": [{"segment_id": bypass_segment, "quote": "旁路镜像"}],
            },
            {
                "name": "旧版本规则同步",
                "action": "不支持规则同步至旧版本节点",
                "target": "规则同步",
                "business_goal": "",
                "supported_scope": [],
                "conditions": [],
                "limitations": ["不支持规则同步至旧版本节点"],
                "implementation_mechanism": "文档未记录底层实现机制",
                "claim_type": "exclusion",
                "anchors": [
                    {
                        "segment_id": exclusion_segment,
                        "quote": "不支持规则同步至旧版本节点",
                    }
                ],
            },
            {
                "name": "伪造锚点",
                "action": "支持伪造",
                "target": "伪造",
                "claim_type": "current_capability",
                "anchors": [{"segment_id": "seg-forged", "quote": "不存在的引文"}],
            },
            {
                "name": "引文不匹配",
                "action": "支持引文不匹配",
                "target": "引文",
                "claim_type": "current_capability",
                "anchors": [{"segment_id": bridge_segment, "quote": "这段引文不在分段中"}],
            },
            {
                "name": "量子加密",
                "action": "支持量子加密",
                "target": "量子加密",
                "claim_type": "current_capability",
                "anchors": [{"segment_id": bridge_segment, "quote": "当前版本支持透明桥"}],
            },
            {
                "name": "透明桥部署",
                "action": "支持透明桥接入",
                "target": "透明桥",
                "claim_type": "current_capability",
                "anchors": [
                    {"segment_id": bridge_segment, "quote": "当前版本支持透明桥"},
                    {
                        "segment_id": xlsx_segment,
                        "quote": "支持 SQL 注入与 XSS 攻击检测和阻断",
                    },
                ],
            },
            {
                "name": "所有流量透明桥",
                "action": "支持所有流量透明桥",
                "target": "透明桥",
                "claim_type": "current_capability",
                "anchors": [{"segment_id": bridge_segment, "quote": "当前版本支持透明桥"}],
            },
            {
                "name": "旁路镜像 10000 QPS",
                "action": "支持旁路镜像",
                "target": "旁路镜像",
                "claim_type": "current_capability",
                "anchors": [{"segment_id": bypass_segment, "quote": "旁路镜像"}],
            },
            {
                "name": "友商对比",
                "action": "友商也支持透明桥",
                "target": "透明桥",
                "claim_type": "current_capability",
                "anchors": [{"segment_id": bridge_segment, "quote": "当前版本支持透明桥"}],
            },
        ]
        return ModelResponse(
            parts=[ToolCallPart(info.output_tools[0].name, {"candidates": candidates})]
        )

    return FunctionModel(respond)


def test_semantic_extraction_rejects_forgery_and_non_product_content(client, environment):
    payloads = []
    object.__setattr__(environment, "document_discovery_mode", "model")
    object.__setattr__(environment, "builder_allow_enterprise", True)
    object.__setattr__(environment, "builder_allow_synthetic", False)
    object.__setattr__(environment, "document_discovery_batch_units", 100)
    object.__setattr__(environment, "document_discovery_max_context_chars", 1_000_000)
    object.__setattr__(environment, "document_discovery_max_tokens", 1_000_000)
    _, sid = setup(client)
    response = client.post(f"/api/v1/snapshots/{sid}/discover-documents", headers=ADMIN)
    # setup() 已经把文档上传并索引；这里用真实 API 创建运行，再注入测试模型执行 worker。
    if response.status_code != 202:
        raise AssertionError(response.text)
    body = response.json()
    with session_factory().begin() as db:
        db.get(KnowledgeJob, body["job_id"]).dispatch_token = "p16-semantic"
    run_document_discovery(body["job_id"], "p16-semantic", model=synthetic_model(payloads))
    with session_factory()() as db:
        job = db.get(KnowledgeJob, body["job_id"])
        run = db.scalar(
            select(DocumentDiscoveryRun).where(DocumentDiscoveryRun.id == body["run_id"])
        )
        claims = list(
            db.scalars(
                select(CapabilityClaim).where(
                    CapabilityClaim.snapshot_id == sid,
                    CapabilityClaim.source_channel == "document",
                )
            )
        )
        audits = list(
            db.scalars(
                select(AuditEvent).where(
                    AuditEvent.resource_id == body["run_id"],
                    AuditEvent.action.like("documents.discovery.model_%"),
                )
            )
        )
    assert payloads
    assert job.status in {"succeeded", "partial"}, run.coverage_json
    assert run.metrics_json["model_mode"] == "TEST_FUNCTION_MODEL"
    assert run.metrics_json["semantic"]["model_candidates"] == 3
    assert run.metrics_json["semantic"]["model_invalid"] == 7
    names = {claim.draft_json["name"] for claim in claims}
    assert {"透明桥部署", "旁路镜像部署", "旧版本规则同步"} <= names
    assert (
        not {
            "伪造锚点",
            "引文不匹配",
            "量子加密",
            "所有流量透明桥",
            "旁路镜像 10000 QPS",
            "友商对比",
        }
        & names
    )
    semantic = next(claim for claim in claims if claim.draft_json["name"] == "透明桥部署")
    assert semantic.draft_json["supported_scope"] == ["透明桥"]
    assert semantic.origin_json["extraction"]["model_mode"] == "TEST_FUNCTION_MODEL"
    assert semantic.origin_json["extraction"]["anchors"]
    assert semantic.draft_json["evidence_ids"]
    assert all("伪造" not in item.details_json.get("candidate", "") for item in audits)
    assert all("透明桥" not in json.dumps(item.details_json, ensure_ascii=False) for item in audits)


def test_model_mode_fails_when_enterprise_exporting_not_authorized(client, environment):
    object.__setattr__(environment, "document_discovery_mode", "model")
    object.__setattr__(environment, "builder_allow_enterprise", False)
    object.__setattr__(environment, "builder_allow_synthetic", False)
    payloads = []
    _, sid = setup(client)
    response = client.post(f"/api/v1/snapshots/{sid}/discover-documents", headers=ADMIN)
    assert response.status_code == 202, response.text
    body = response.json()
    with session_factory().begin() as db:
        db.get(KnowledgeJob, body["job_id"]).dispatch_token = "p16-semantic-denied"
    run_document_discovery(body["job_id"], "p16-semantic-denied", model=synthetic_model(payloads))
    with session_factory()() as db:
        job = db.get(KnowledgeJob, body["job_id"])
        run = db.scalar(
            select(DocumentDiscoveryRun).where(DocumentDiscoveryRun.id == body["run_id"])
        )
    assert job.status == "failed"
    assert run.error_code == "DOCUMENT_DISCOVERY_MODEL_NOT_AUTHORIZED"
    assert payloads == []


def test_model_failure_does_not_persist_structural_candidates(client, environment):
    object.__setattr__(environment, "document_discovery_mode", "model")
    object.__setattr__(environment, "builder_allow_enterprise", True)
    object.__setattr__(environment, "builder_allow_synthetic", False)
    object.__setattr__(environment, "document_discovery_max_requests", 0)
    _, sid = setup(client)
    response = client.post(f"/api/v1/snapshots/{sid}/discover-documents", headers=ADMIN)
    assert response.status_code == 202, response.text
    body = response.json()
    with session_factory().begin() as db:
        db.get(KnowledgeJob, body["job_id"]).dispatch_token = "p16-semantic-budget"
    run_document_discovery(body["job_id"], "p16-semantic-budget", model=synthetic_model([]))
    with session_factory()() as db:
        job = db.get(KnowledgeJob, body["job_id"])
        run = db.scalar(
            select(DocumentDiscoveryRun).where(DocumentDiscoveryRun.id == body["run_id"])
        )
        claims = list(
            db.scalars(
                select(CapabilityClaim).where(
                    CapabilityClaim.snapshot_id == sid,
                    CapabilityClaim.source_channel == "document",
                )
            )
        )
    assert job.status == "failed"
    assert run.error_code == "DOCUMENT_DISCOVERY_MODEL_BUDGET_EXCEEDED"
    assert claims == []
