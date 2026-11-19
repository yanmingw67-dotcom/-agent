"""P2.2 原子判断：无证据、明确否定、阈值和模型不可覆盖的边界。"""

from app.evaluation.atomic import AtomicEvaluator
from app.evaluation.retrieval import Candidate, RetrievalResult
from app.evaluation.schemas import ModelBatch, ModelJudgment


def evidence(eid="ev-1", quote="已发布证据"):
    return {
        "id": eid,
        "kind": "source",
        "source_ref": {"file_id": "f1", "path": "src/a.py", "role": "product"},
        "locator": {"start_line": 1, "end_line": 1},
        "source_hash": "a" * 64,
        "quote_hash": "b" * 64,
        "quote": quote,
    }


def candidate(cap_id, text, limitations=None):
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
        evidence={"ev-1": evidence()},
        verified=True,
    )


def negative_candidate(cap_id, action, limitation):
    item = candidate(cap_id, action, [limitation])
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
    def __init__(self, candidates):
        self.candidates = candidates
        self.calls = 0

    def retrieve(self, requirement, parameter_name):
        self.calls += 1
        return RetrievalResult(candidates=self.candidates, gaps=[])


def requirement(text, **extra):
    value = {
        "requirement_id": "req-1",
        "original_text": text,
        "requirement_type": "capability",
        "logical_relation": "AND",
        "is_mandatory": True,
        "mandatory_status": "required",
        "threshold": None,
        "threshold_operator": None,
        "unit": None,
        "conditions": [],
        "ambiguity": None,
        "logic_ambiguity": None,
    }
    value.update(extra)
    return value


def evaluate(text, candidates, **extra):
    knowledge = FakeKnowledge(candidates)
    evaluator = AtomicEvaluator(
        knowledge,
        job_id="job-1",
        principal_id="alice",
        model_mode="structural",
    )
    return evaluator.evaluate([requirement(text, **extra)], "示例参数")[0]


def test_exact_published_evidence_supports():
    result = evaluate(
        "支持 HTTP/1.1 协议解析",
        [candidate("cap-http", "支持 HTTP/1.1 和 HTTP/2 协议解析")],
    )
    assert result["verdict"] == "支持"
    assert result["evidence_ids"]


def test_model_must_establish_equivalence():
    item = candidate("cap-ipv6", "提供 IPv6 与 IPv4 双栈接入")
    item.evidence = {"ev-1": evidence("ev-1", "提供 IPv6 与 IPv4 双栈接入")}
    knowledge = FakeKnowledge([item])
    evaluator = AtomicEvaluator(
        knowledge,
        job_id="job-1",
        principal_id="alice",
        model_mode="structural",
    )
    prepared = evaluator._deterministic(requirement("支持 IPv6 协议解析"), "示例参数")
    judgment = {
        "requirement_id": "req-1",
        "relation": "supports",
        "capability_id": "cap-ipv6",
        "evidence_ids": ["ev-1"],
        "anchors": [{"evidence_id": "ev-1", "quote": "提供 IPv6 与 IPv4 双栈接入"}],
        "rationale": "双栈接入在同等条件下完成 IPv6 协议解析",
        "equivalence": "insufficient",
        "mechanism_compatible": True,
    }
    accepted, gaps = evaluator._validate(
        ModelBatch(judgments=[ModelJudgment(**judgment)]),
        [prepared],
        {"req-1": {"cap-ipv6"}},
    )
    assert not accepted
    assert gaps[0]["reason"] == "equivalence_not_established"


def test_model_contract_accepts_published_evidence_and_capability_ids():
    evidence_id = "ev-" + "a" * 64
    capability_id = "p14-" + "b" * 36 + "-0"
    item = candidate(capability_id, "支持 HTTP/1.1 和 HTTP/2 协议解析")
    item.evidence = {evidence_id: evidence(evidence_id, "支持 HTTP/1.1 和 HTTP/2 协议解析")}
    payload = ModelJudgment(
        requirement_id="7f57a0dd-6215-49ad-9753-7ab5d822bf17",
        relation="supports",
        capability_id=capability_id,
        evidence_ids=[evidence_id],
        anchors=[{"evidence_id": evidence_id, "quote": "支持 HTTP/1.1 和 HTTP/2 协议解析"}],
        rationale="固定发布证据逐字支持协议解析要求",
        equivalence="identical",
        mechanism_compatible=True,
    )
    assert len(evidence_id) == 67
    assert len(capability_id) == 42
    assert payload.evidence_ids == [evidence_id]


def test_pure_ascii_version_is_not_stripped_as_line_number():
    for version in ("HTTP/1.1", "HTTP/2"):
        result = evaluate(
            version,
            [candidate("cap-http", "支持 HTTP/1.1 和 HTTP/2 协议解析")],
        )
        assert result["verdict"] == "支持", (version, result)


def test_no_match_is_unknown_not_unsupported():
    result = evaluate("支持 IPv6 协议解析", [])
    assert result["verdict"] == "无法确认"


def test_explicit_limitation_is_unsupported_only_with_scoped_evidence():
    result = evaluate(
        "支持 IPv6 协议解析",
        [negative_candidate("cap-ipv4", "支持 IPv4 协议解析", "不支持 IPv6 协议解析")],
    )
    assert result["verdict"] == "不支持"
    assert result["citations"][0]["verification_state"] == "verified_contradiction"


def test_epistemic_disclaimer_is_not_negative_capability_evidence():
    result = evaluate(
        "支持 IPv6 协议解析性能",
        [candidate("cap-ipv6", "提供 IPv6 与 IPv4 双栈接入", ["不证明 IPv6 协议解析性能"])],
    )
    assert result["verdict"] == "无法确认"


def test_blank_threshold_is_unknown_and_never_guessed():
    result = evaluate(
        "最大并发连接数不低于 ______",
        [candidate("cap-perf", "最大并发连接数不低于 20000")],
        ambiguity="blank_threshold",
        threshold=None,
    )
    assert result["verdict"] == "无法确认"


def test_threshold_satisfied_supports_but_lower_number_is_not_unsupported():
    supported = evaluate(
        "最大并发连接数不低于 10000",
        [candidate("cap-perf", "最大并发连接数 20000")],
        threshold="10000",
        threshold_operator="不低于",
    )
    assert supported["verdict"] == "支持"
    lower = evaluate(
        "最大并发连接数不低于 10000",
        [candidate("cap-perf", "最大并发连接数 5000")],
        threshold="10000",
        threshold_operator="不低于",
    )
    assert lower["verdict"] == "无法确认"


def test_conflicting_support_and_limitation_is_unknown():
    result = evaluate(
        "支持 IPv6 协议解析",
        [
            candidate("cap-support", "支持 IPv6 协议解析"),
            negative_candidate("cap-limit", "支持协议解析", "不支持 IPv6 协议解析"),
        ],
    )
    assert result["verdict"] == "无法确认"


def test_optional_atomic_is_evaluated_but_not_necessary():
    result = evaluate(
        "支持可选审计扩展",
        [candidate("cap-audit", "支持可选审计扩展")],
        is_mandatory=False,
        mandatory_status="optional",
    )
    assert result["necessary"] is False


def test_uncertain_open_enumeration_is_unknown_without_retrieval():
    knowledge = FakeKnowledge([candidate("cap", "支持全部功能")])
    evaluator = AtomicEvaluator(
        knowledge, job_id="job", principal_id="alice", model_mode="structural"
    )
    result = evaluator.evaluate(
        [requirement("支持某功能", mandatory_status="uncertain", ambiguity="open_enumeration")],
        "参数",
    )[0]
    assert result["verdict"] == "无法确认"
    assert knowledge.calls == 0
