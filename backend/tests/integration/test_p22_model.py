"""P2.2 模型辅助边界：FunctionModel 只验证受控管线，不代表真实模型质量。"""

from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from app.evaluation.atomic import AtomicEvaluator
from app.evaluation.retrieval import Candidate
from tests.unit.test_p22_atomic import evidence, requirement


def candidate(cap_id, text, quote, limitations=None):
    return Candidate(
        capability_id=cap_id,
        revision_id=f"rev-{cap_id}",
        declaration_type="product_function",
        content={
            "name": cap_id,
            "action": text,
            "target": "",
            "business_goal": "",
            "implementation_mechanism": "",
            "supported_scope": ["已发布范围"],
            "conditions": [],
            "limitations": limitations or ["不证明性能上限"],
        },
        assertions=[],
        text=text,
        evidence={"ev-1": evidence("ev-1", quote)},
        verified=True,
    )


def negative(cap_id, limitation):
    item = candidate(cap_id, "提供 IPv4 接入", "提供 IPv4 接入", [limitation])
    item.assertions = [
        {
            "field": "limitations.0",
            "text": limitation,
            "evidence_ids": ["ev-1"],
            "semantic_decision": "supported",
        }
    ]
    return item


class FakeKnowledge:
    def __init__(self, items):
        self.items = items

    def retrieve(self, requirement_text, parameter_name):
        from app.evaluation.retrieval import RetrievalResult

        return RetrievalResult(candidates=self.items, gaps=[])


def judgment(**overrides):
    value = {
        "requirement_id": "req-1",
        "relation": "supports",
        "capability_id": "cap-ipv6",
        "evidence_ids": ["ev-1"],
        "anchors": [{"evidence_id": "ev-1", "quote": "提供 IPv6 与 IPv4 双栈接入"}],
        "rationale": "双栈接入在同等条件下完成 IPv6 协议解析",
        "equivalence": "equivalent_effect",
        "mechanism_compatible": True,
    }
    value.update(overrides)
    return value


def model(payloads, calls):
    async def respond(messages, info):
        calls.append(info.output_tools[0].name)
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, payloads.pop(0))])

    return FunctionModel(respond)


def evaluator(items, function_model, mode="model"):
    return AtomicEvaluator(
        FakeKnowledge(items),
        job_id="job-p22-model",
        principal_id="alice",
        model=function_model,
        model_mode=mode,
    )


def test_function_model_can_resolve_unknown_only_with_verified_anchor(client, environment):
    item = candidate("cap-ipv6", "提供 IPv6 与 IPv4 双栈接入", "提供 IPv6 与 IPv4 双栈接入")
    calls = []
    result = evaluator([item], model([{"judgments": [judgment()]}], calls)).evaluate(
        [requirement("支持 IPv6 协议解析")], "示例参数"
    )[0]
    assert result["verdict"] == "支持"
    assert result["assessment_method"] == "model"
    assert result["evidence_ids"] == ["ev-1"]
    assert result["citations"][0]["verification_state"] == "verified_support"
    assert calls


def test_function_model_cannot_supply_or_forge_evidence(client, environment):
    item = candidate("cap-ipv6", "提供 IPv6 与 IPv4 双栈接入", "提供 IPv6 与 IPv4 双栈接入")
    forged_id = evaluator(
        [item],
        model([{"judgments": [judgment(evidence_ids=["ev-forged"])]}], []),
    ).evaluate([requirement("支持 IPv6 协议解析")], "示例参数")[0]
    assert forged_id["verdict"] == "无法确认"
    forged_quote = evaluator(
        [item],
        model(
            [{"judgments": [judgment(anchors=[{"evidence_id": "ev-1", "quote": "伪造引文"}])]}], []
        ),
    ).evaluate([requirement("支持 IPv6 协议解析")], "示例参数")[0]
    assert forged_quote["verdict"] == "无法确认"
    reasons = {gap["reason"] for gap in forged_quote["details_json"].get("model_gaps", [])}
    assert "quote_not_verified" in reasons


def test_model_budget_exhaustion_degrades_without_fabricating_support(client, environment):
    object.__setattr__(environment, "evaluation_model_max_requests", 0)
    item = candidate("cap-ipv6", "提供 IPv6 与 IPv4 双栈接入", "提供 IPv6 与 IPv4 双栈接入")
    calls = []
    instance = evaluator([item], model([{"judgments": [judgment()]}], calls))
    result = instance.evaluate([requirement("支持 IPv6 协议解析")], "示例参数")[0]
    assert result["verdict"] == "无法确认"
    assert not calls
    assert instance.audit.status == "DEGRADED"
    assert instance.audit.reason_code == "EVALUATION_MODEL_BUDGET_EXCEEDED"


def test_model_cannot_overturn_deterministic_contradiction(client, environment):
    item = negative("cap-ipv4", "不支持 IPv6 协议解析")
    calls = []
    instance = evaluator([item], model([{"judgments": [judgment()]}], calls))
    result = instance.evaluate([requirement("支持 IPv6 协议解析")], "示例参数")[0]
    assert result["verdict"] == "不支持"
    assert result["assessment_method"] == "deterministic"
    assert not calls
