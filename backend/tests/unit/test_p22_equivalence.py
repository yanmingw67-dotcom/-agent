"""P2.2 R01–R04 反例与边界：受控支持、量化指标、否定绑定和至少 N 项。"""

from app.evaluation.atomic import AtomicEvaluator
from app.evaluation.equivalence import (
    direct_support,
    metric_evaluation,
    minimum_scope,
    negative_assertion_bindings,
    negative_scope_matches,
)
from app.evaluation.retrieval import Candidate, RetrievalResult
from app.evaluation.schemas import ModelBatch, ModelJudgment


def evidence(eid, quote, kind="source"):
    return {
        "id": eid,
        "kind": kind,
        "source_ref": {"file_id": "f1", "path": "src/a.py", "role": "product"},
        "locator": {"start_line": 1, "end_line": 1},
        "source_hash": "a" * 64,
        "quote_hash": "b" * 64,
        "quote": quote,
    }


def make_candidate(cap_id, text, limitation=None, kind="source", conditions=None):
    content = {
        "name": cap_id,
        "action": text,
        "target": "测试对象",
        "business_goal": "验证受控判断",
        "implementation_mechanism": "测试构造",
        "supported_scope": ["已发布范围"],
        "conditions": conditions or [],
        "limitations": [limitation] if limitation else ["不证明真实产品能力"],
    }
    assertions = []
    ev = {"ev-1": evidence("ev-1", text, kind)}
    if limitation:
        ev["ev-2"] = evidence("ev-2", limitation, kind)
        assertions = [
            {
                "field": "limitations.0",
                "text": limitation,
                "evidence_ids": ["ev-2"],
                "semantic_decision": "supported",
            }
        ]
    return Candidate(
        capability_id=cap_id,
        revision_id=f"rev-{cap_id}",
        declaration_type="product_function",
        content=content,
        assertions=assertions,
        text=text,
        evidence=ev,
        verified=True,
    )


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


class FakeKnowledge:
    def __init__(self, candidates):
        self.candidates = candidates

    def retrieve(self, requirement, parameter_name):
        return RetrievalResult(candidates=self.candidates, gaps=[])


class LookupKnowledge:
    def __init__(self, mapping, issues=None):
        self.mapping = mapping
        self.issues = issues or {}

    def retrieve(self, requirement, parameter_name):
        issue = self.issues.get(requirement, {})
        return RetrievalResult(
            candidates=self.mapping.get(requirement, []),
            gaps=issue.get("gaps", []),
            truncated=issue.get("truncated", False),
        )


def evaluate(text, candidates, **extra):
    evaluator = AtomicEvaluator(
        FakeKnowledge(candidates),
        job_id="job-1",
        principal_id="alice",
        model_mode="structural",
    )
    return evaluator.evaluate([requirement(text, **extra)], "示例参数")[0]


def minimum(text, candidates_by_option, threshold="2", issues=None):
    evaluator = AtomicEvaluator(
        LookupKnowledge(candidates_by_option, issues),
        job_id="job-1",
        principal_id="alice",
        model_mode="structural",
    )
    return evaluator.evaluate(
        [requirement(text, requirement_type="minimum_count", threshold=threshold)],
        "安全功能",
    )[0]


def test_direct_support_requires_the_same_technical_action():
    ok, _ = direct_support("支持 SQL 注入检测", "支持 SQL 注入检测")
    assert ok
    for required, candidate in (
        ("支持 SQL 注入检测与阻断", "支持 SQL 注入检测"),
        ("支持恶意文件识别与处置", "支持恶意文件识别"),
        ("支持异常流量监测与自动响应", "支持异常流量监测"),
        ("支持集群高可用部署", "支持单机部署"),
        ("支持单机部署", "支持集群高可用部署"),
    ):
        supported, reasons = direct_support(required, candidate)
        assert not supported, (required, candidate)
        assert reasons


def test_direct_support_keeps_a_genuine_positive_path():
    supported, reasons = direct_support("支持 IPv6 协议解析", "支持 IPv6 与 IPv4 协议解析")
    assert supported, reasons
    supported, reasons = direct_support(
        "支持 HTTP/1.1 协议解析", "支持 HTTP/1.1 和 HTTP/2 协议解析"
    )
    assert supported, reasons


def test_condition_must_be_covered_by_candidate():
    supported, reasons = direct_support(
        "支持 IPv6 协议解析", "支持 IPv6 协议解析", ["仅限双栈环境"]
    )
    assert not supported
    assert "condition_not_covered" in reasons


def test_candidate_conditions_can_cover_required_condition():
    result = evaluate(
        "支持 IPv6 协议解析",
        [
            make_candidate(
                "cap-ipv6",
                "支持 IPv6 协议解析",
                conditions=["仅限双栈环境"],
            )
        ],
        conditions=["仅限双栈环境"],
    )
    assert result["verdict"] == "支持", result


def test_candidate_conditions_cannot_supply_a_missing_action():
    result = evaluate(
        "支持 SQL 注入检测与阻断",
        [
            make_candidate(
                "cap-detect",
                "支持 SQL 注入检测",
                conditions=["阻断功能需另购"],
            )
        ],
    )
    assert result["verdict"] != "支持", result


def test_metrics_are_bound_to_name_unit_and_conversion():
    result = metric_evaluation(
        "吞吐量不低于 1 Gbps",
        "吞吐量",
        "1",
        "不低于",
        "Gbps",
        "吞吐量 1200 Mbps",
    )
    assert result["state"] == "satisfied"
    assert result["claim"]["unit"] == "Mbps"

    result = metric_evaluation(
        "时延不高于 100 ms",
        "时延",
        "100",
        "不高于",
        "ms",
        "时延 0.05 秒",
    )
    assert result["state"] == "satisfied"

    result = metric_evaluation(
        "时延不高于 100 ms",
        "时延",
        "100",
        "不高于",
        "ms",
        "时延 150 毫秒",
    )
    assert result["state"] == "not_satisfied"


def test_unrelated_numbers_are_not_compared_as_metric_values():
    result = metric_evaluation(
        "吞吐量不低于 1 Gbps",
        "吞吐量",
        "1",
        "不低于",
        "Gbps",
        "支持 HTTP/2 协议解析，最大并发连接数 20000 个，吞吐量 800 Mbps",
    )
    assert result["state"] == "not_satisfied"
    assert result["claim"]["value"] == 800
    assert result["claim"]["metric"] == "吞吐量"


def test_product_upper_bound_is_usable_but_default_config_is_not():
    result = metric_evaluation(
        "最大并发连接数不低于 10000 个",
        "最大并发连接数",
        "10000",
        "不低于",
        "个",
        "默认并发连接数 1000 个，最大并发连接数 20000 个",
    )
    assert result["state"] == "satisfied"

    result = metric_evaluation(
        "最大并发连接数不低于 10000 个",
        "最大并发连接数",
        "10000",
        "不低于",
        "个",
        "默认最大并发连接数 1000 个",
    )
    assert result["state"] == "unknown"
    assert result["reason"] == "candidate_only_default_scope"


def test_trailing_default_marker_is_not_a_product_upper_bound():
    result = metric_evaluation(
        "最大并发连接数不低于 10000 个",
        "最大并发连接数",
        "10000",
        "不低于",
        "个",
        "最大并发连接数 20000 个（默认配置）",
    )
    assert result["state"] == "unknown"
    assert result["reason"] == "candidate_only_default_scope"


def test_missing_unit_and_multiple_unbounded_values_are_unknown():
    result = metric_evaluation(
        "最大并发连接数不低于 10000",
        "最大并发连接数",
        "10000",
        "不低于",
        None,
        "最大并发连接数 20000",
    )
    assert result["state"] == "unknown"
    assert result["reason"] == "metric_or_unit_unbound"

    result = metric_evaluation(
        "吞吐量不低于 1 Gbps",
        "吞吐量",
        "1",
        "不低于",
        "Gbps",
        "吞吐量 800 Mbps，吞吐量 1200 Mbps",
    )
    assert result["state"] == "unknown"
    assert result["reason"] == "multiple_candidate_values"


def test_multiple_bounded_values_are_unknown():
    result = metric_evaluation(
        "吞吐量不低于 1 Gbps",
        "吞吐量",
        "1",
        "不低于",
        "Gbps",
        "最大吞吐量 2 Gbps，最大吞吐量 3 Gbps",
    )
    assert result["state"] == "unknown"
    assert result["reason"] == "multiple_candidate_values"


def test_count_units_must_match_exactly():
    result = metric_evaluation(
        "最大并发连接数不低于 10000 个",
        "最大并发连接数",
        "10000",
        "不低于",
        "个",
        "最大并发连接数 20000 条",
    )
    assert result["state"] == "unknown"
    assert result["reason"] == "candidate_metric_not_bound"


def test_lower_metric_is_not_automatically_unsupported():
    result = evaluate(
        "最大并发连接数不低于 10000 个",
        [make_candidate("cap-perf", "最大并发连接数 5000 个")],
        threshold="10000",
        threshold_operator="不低于",
        unit="个",
    )
    assert result["verdict"] == "无法确认"
    assert result["details_json"]["numeric_contradiction"] == "required_value_not_met"


def test_negative_scope_must_match_action_object_and_deployment():
    assert negative_scope_matches("支持 IPv6 协议解析", "不支持 IPv6 协议解析")
    assert not negative_scope_matches("支持 IPv6 协议解析", "不支持 IPv4 协议解析")
    assert not negative_scope_matches("支持 SQL 注入检测与阻断", "不支持 SQL 注入检测")
    assert not negative_scope_matches("支持集群高可用部署", "不支持单机部署")


def test_negative_binding_comes_from_reviewed_limitations_only():
    candidate = make_candidate("cap-ipv4", "支持 IPv4 协议解析", "不支持 IPv6 协议解析")
    bindings = negative_assertion_bindings(
        {"content": candidate.content, "assertions": candidate.assertions}
    )
    assert bindings == [
        {
            "field": "limitations.0",
            "claim": "不支持 IPv6 协议解析",
            "evidence_ids": ["ev-2"],
        }
    ]

    result = evaluate(
        "支持 IPv6 协议解析",
        [candidate],
    )
    assert result["verdict"] == "不支持"


def test_unable_to_provide_is_a_reviewed_negative_claim():
    result = evaluate(
        "支持 IPv6 协议解析",
        [make_candidate("cap-ipv4", "支持 IPv4 协议解析", "无法提供 IPv6 协议解析")],
    )
    assert result["verdict"] == "不支持"


def test_unverified_limitation_is_not_a_negative_binding():
    candidate = make_candidate("cap-ipv4", "支持 IPv4 协议解析", "不支持 IPv6 协议解析")
    candidate.assertions[0]["semantic_decision"] = "unverified"
    assert (
        negative_assertion_bindings(
            {"content": candidate.content, "assertions": candidate.assertions}
        )
        == []
    )


def test_negative_quote_with_different_object_is_unknown():
    result = evaluate(
        "支持集群高可用部署",
        [make_candidate("cap-single", "支持单机部署", "不支持单机部署")],
    )
    assert result["verdict"] == "无法确认"


def test_model_contradiction_requires_audited_negative_evidence():
    candidate = make_candidate("cap-single", "支持单机部署")
    candidate.evidence["ev-2"] = evidence("ev-2", "不支持集群高可用部署")
    evaluator = AtomicEvaluator(
        FakeKnowledge([candidate]),
        job_id="job-1",
        principal_id="alice",
        model_mode="structural",
    )
    prepared = evaluator._deterministic(requirement("支持集群高可用部署"), "示例参数")
    judgment = ModelJudgment(
        requirement_id="req-1",
        relation="contradicts",
        capability_id="cap-single",
        evidence_ids=["ev-2"],
        anchors=[{"evidence_id": "ev-2", "quote": "不支持集群高可用部署"}],
        rationale="证据否定该部署形态",
        equivalence="different_mechanism",
        mechanism_compatible=False,
    )
    accepted, gaps = evaluator._validate(
        ModelBatch(judgments=[judgment]), [prepared], {"req-1": {"cap-single"}}
    )
    assert not accepted
    assert gaps[0]["reason"] == "contradiction_evidence_not_audited"


def test_model_contradiction_rejects_scope_mismatch():
    candidate = make_candidate("cap-single", "支持单机部署", "不支持单机部署")
    evaluator = AtomicEvaluator(
        FakeKnowledge([candidate]),
        job_id="job-1",
        principal_id="alice",
        model_mode="structural",
    )
    prepared = evaluator._deterministic(requirement("支持集群高可用部署"), "示例参数")
    judgment = ModelJudgment(
        requirement_id="req-1",
        relation="contradicts",
        capability_id="cap-single",
        evidence_ids=["ev-2"],
        anchors=[{"evidence_id": "ev-2", "quote": "不支持单机部署"}],
        rationale="否定句看似相关",
        equivalence="different_mechanism",
        mechanism_compatible=False,
    )
    accepted, gaps = evaluator._validate(
        ModelBatch(judgments=[judgment]), [prepared], {"req-1": {"cap-single"}}
    )
    assert not accepted
    assert gaps[0]["reason"] == "contradiction_scope_or_binding_mismatch"


def test_model_contradiction_cannot_override_verified_support():
    candidate = make_candidate("cap-ipv6", "支持 IPv6 协议解析", "不支持 IPv6 协议解析")
    evaluator = AtomicEvaluator(
        FakeKnowledge([candidate]),
        job_id="job-1",
        principal_id="alice",
        model_mode="structural",
    )
    prepared = evaluator._deterministic(requirement("支持 IPv6 协议解析"), "示例参数")
    assert any(
        item["verification_state"] == "verified_support" for item in prepared.result.citations
    )
    judgment = ModelJudgment(
        requirement_id="req-1",
        relation="contradicts",
        capability_id="cap-ipv6",
        evidence_ids=["ev-2"],
        anchors=[{"evidence_id": "ev-2", "quote": "不支持 IPv6 协议解析"}],
        rationale="试图否定已有支持",
        equivalence="different_mechanism",
        mechanism_compatible=False,
    )
    accepted, gaps = evaluator._validate(
        ModelBatch(judgments=[judgment]), [prepared], {"req-1": {"cap-ipv6"}}
    )
    assert not accepted
    assert gaps[0]["reason"] == "contradiction_conflicts_with_support"


def test_model_contradiction_accepts_valid_reviewed_negative_assertion():
    candidate = make_candidate("cap-ipv4", "支持 IPv4 协议解析", "不支持 IPv6 协议解析")
    evaluator = AtomicEvaluator(
        FakeKnowledge([candidate]),
        job_id="job-1",
        principal_id="alice",
        model_mode="structural",
    )
    prepared = evaluator._deterministic(requirement("支持 IPv6 协议解析"), "示例参数")
    judgment = ModelJudgment(
        requirement_id="req-1",
        relation="contradicts",
        capability_id="cap-ipv4",
        evidence_ids=["ev-2"],
        anchors=[{"evidence_id": "ev-2", "quote": "不支持 IPv6 协议解析"}],
        rationale="已审核限制明确否定同一能力",
        equivalence="different_mechanism",
        mechanism_compatible=False,
    )
    accepted, gaps = evaluator._validate(
        ModelBatch(judgments=[judgment]), [prepared], {"req-1": {"cap-ipv4"}}
    )
    assert accepted, gaps


def test_minimum_count_uses_confirmed_complete_set():
    result = minimum(
        "支持至少 2 项功能，仅限以下选项：A 功能、B 功能、C 功能",
        {
            "A 功能": [make_candidate("cap-a", "支持 A 功能")],
            "B 功能": [make_candidate("cap-b", "支持 B 功能")],
            "C 功能": [],
        },
    )
    assert result["verdict"] == "支持"
    details = result["details_json"]
    assert details["supported"] == 2
    assert details["complete_set"] is True
    assert details["complete_basis"] == "only_following"
    assert details["complete_marker_start"] >= 0
    assert details["complete_marker_end"] > details["complete_marker_start"]
    assert details["uncertainty"] == []


def test_minimum_count_unsupported_only_for_complete_set_with_insufficient_max():
    result = minimum(
        "支持至少 2 项功能，仅限以下选项：A 功能、B 功能",
        {
            "A 功能": [make_candidate("cap-a", "支持 A 功能")],
            "B 功能": [make_candidate("cap-b", "支持 A 功能", "不支持 B 功能")],
        },
    )
    assert result["verdict"] == "不支持"
    details = result["details_json"]
    assert details["complete_set"] is True
    assert details["max_possible_supported"] == 1
    state = next(item for item in details["options"] if item["option"] == "B 功能")
    assert state["verdict"] == "不支持"
    assert state["negative_evidence_ids"] == ["ev-2"]
    assert any(
        item["evidence_id"] == "ev-2" and item["verification_state"] == "verified_contradiction"
        for item in result["citations"]
    )


def test_minimum_count_with_unknown_member_is_not_unsupported():
    result = minimum(
        "支持至少 2 项功能：A 功能、B 功能、C 功能",
        {
            "A 功能": [make_candidate("cap-a", "支持 A 功能")],
            "B 功能": [make_candidate("cap-b", "支持 A 功能", "不支持 B 功能")],
            "C 功能": [],
        },
    )
    assert result["verdict"] == "无法确认"
    assert result["details_json"]["max_possible_supported"] >= 2


def test_minimum_option_with_conflicting_support_and_negative_is_unknown():
    result = minimum(
        "支持至少 1 项功能：A 功能",
        {"A 功能": [make_candidate("cap-a", "支持 A 功能", "不支持 A 功能")]},
        threshold="1",
    )
    assert result["verdict"] == "无法确认"


def test_minimum_count_with_unknown_full_set_is_not_unsupported():
    result = minimum("支持至少 2 项安全功能", {})
    assert result["verdict"] == "无法确认"
    assert result["details_json"]["complete_set"] is False


def test_minimum_count_without_count_unit_is_unknown():
    result = minimum(
        "支持 2 项功能：A 功能、B 功能",
        {
            "A 功能": [make_candidate("cap-a", "支持 A 功能")],
            "B 功能": [make_candidate("cap-b", "支持 B 功能")],
        },
    )
    assert result["verdict"] == "无法确认"
    assert result["details_json"]["count_unit"] is None


def test_minimum_count_accepts_synonym_for_at_least():
    result = minimum(
        "支持不少于 2 项功能：A 功能、B 功能",
        {
            "A 功能": [make_candidate("cap-a", "支持 A 功能")],
            "B 功能": [make_candidate("cap-b", "支持 B 功能")],
        },
    )
    assert result["verdict"] == "支持"
    assert result["details_json"]["count_unit"] == "项"
    assert result["details_json"]["complete_set"] is False


def test_open_enumeration_is_not_a_complete_set():
    result = minimum(
        "支持至少 2 项功能，包括但不限于 A 功能、B 功能",
        {
            "A 功能": [make_candidate("cap-a", "支持 A 功能")],
            "B 功能": [make_candidate("cap-b", "支持 A 功能", "不支持 B 功能")],
        },
    )
    assert result["verdict"] == "无法确认"
    assert result["details_json"]["complete_set"] is False


def test_explicit_universe_with_open_marker_is_not_complete():
    scope = minimum_scope(
        "支持至少 2 项功能，以下为全部可选项，包括但不限于 A 功能、B 功能",
        ["A 功能", "B 功能"],
        2,
    )
    assert scope["complete_set"] is False
    assert scope["complete_basis"] == "following_is_all"
    assert "open_enumeration" in scope["uncertainty"]


def test_classified_protection_is_not_an_open_enumeration():
    scope = minimum_scope(
        "支持至少 2 项等保合规功能，仅限以下选项：A 功能、B 功能",
        ["A 功能", "B 功能"],
        2,
    )
    assert scope["complete_set"] is True


def test_minimum_count_without_explicit_universe_is_not_unsupported():
    result = minimum(
        "支持至少 2 项功能：A 功能、B 功能",
        {
            "A 功能": [make_candidate("cap-a", "支持 A 功能")],
            "B 功能": [make_candidate("cap-b", "支持基础功能", "不支持 B 功能")],
        },
    )
    assert result["verdict"] == "无法确认"
    details = result["details_json"]
    assert details["complete_set"] is False
    assert details["complete_basis"] is None
    assert details["complete_marker"] is None
    assert "no_explicit_full_set_marker" in details["uncertainty"]
    assert details["option_count"] == 2


def test_minimum_count_explicit_three_choose_two_is_complete():
    result = minimum(
        "支持至少 2 项功能，以下三项中选两项：A 功能、B 功能、C 功能",
        {
            "A 功能": [make_candidate("cap-a", "支持 A 功能")],
            "B 功能": [make_candidate("cap-b", "支持 B 功能")],
            "C 功能": [make_candidate("cap-c", "支持基础功能", "不支持 C 功能")],
        },
    )
    assert result["verdict"] == "支持"
    details = result["details_json"]
    assert details["complete_set"] is True
    assert details["complete_basis"] == "choose_n_of_m"
    assert details["declared_option_count"] == 3
    assert details["declared_pick_count"] == 2
    assert details["option_count_matches"] is True
    assert details["pick_count_matches"] is True
    assert details["uncertainty"] == []


def test_minimum_count_from_following_universe_is_complete():
    result = minimum(
        "支持至少 2 项功能，从下列三项中选择：A 功能、B 功能、C 功能",
        {
            "A 功能": [make_candidate("cap-a", "支持 A 功能")],
            "B 功能": [make_candidate("cap-b", "支持 B 功能")],
            "C 功能": [make_candidate("cap-c", "支持基础功能", "不支持 C 功能")],
        },
    )
    assert result["verdict"] == "支持"
    details = result["details_json"]
    assert details["complete_set"] is True
    assert details["complete_basis"] == "from_following"
    assert details["complete_marker"] == "从下列三项中"
    assert details["declared_option_count"] == 3


def test_minimum_count_declared_universe_must_match_parsed_options():
    scope = minimum_scope(
        "支持至少 2 项功能，从下列三项中：A 功能、B 功能",
        ["A 功能", "B 功能"],
        2,
    )
    assert scope["complete_set"] is False
    assert scope["declared_option_count"] == 3
    assert scope["option_count"] == 2
    assert "declared_option_count_mismatch" in scope["uncertainty"]

    scope = minimum_scope(
        "支持至少 2 项功能，仅限以下三项：A 功能、B 功能",
        ["A 功能", "B 功能"],
        2,
    )
    assert scope["complete_set"] is False
    assert scope["declared_option_count"] == 3
    assert "declared_option_count_mismatch" in scope["uncertainty"]


def test_minimum_scope_records_marker_position_in_original_text():
    text = "1. 支持至少 2 项功能，仅限以下选项：A 功能、B 功能"
    scope = minimum_scope(text, ["A 功能", "B 功能"], 2)
    assert scope["complete_set"] is True
    assert scope["complete_marker_start"] == text.index("仅限以下选项")
    assert scope["complete_marker_end"] == text.index("仅限以下选项") + len("仅限以下选项")


def test_minimum_count_complete_set_with_all_negative_is_unsupported():
    result = minimum(
        "支持至少 2 项功能，以下为全部可选项：A 功能、B 功能",
        {
            "A 功能": [make_candidate("cap-a", "支持基础功能", "不支持 A 功能")],
            "B 功能": [make_candidate("cap-b", "支持基础功能", "不支持 B 功能")],
        },
    )
    assert result["verdict"] == "不支持"
    details = result["details_json"]
    assert details["complete_set"] is True
    assert details["complete_basis"] == "following_is_all"
    assert details["supported"] == 0
    assert details["unsupported"] == 2
    for state in details["options"]:
        assert state["negative_evidence_ids"] == ["ev-2"]
        assert state["evidence_ids"] == ["ev-2"]
    assert {item["verification_state"] for item in result["citations"]} == {
        "verified_contradiction"
    }


def test_minimum_count_same_candidate_support_and_negative_is_unknown():
    result = minimum(
        "支持至少 1 项功能，仅限以下选项：A 功能",
        {"A 功能": [make_candidate("cap-a", "支持 A 功能", "不支持 A 功能")]},
        threshold="1",
    )
    assert result["verdict"] == "无法确认"
    state = result["details_json"]["options"][0]
    assert state["reason"] == "conflicting_support_and_negative"
    assert state["support_evidence_ids"]
    assert state["negative_evidence_ids"] == ["ev-2"]


def test_minimum_count_different_candidates_conflict_and_order_is_irrelevant():
    support = make_candidate("cap-support", "支持 A 功能")
    negative = make_candidate("cap-negative", "支持基础功能", "不支持 A 功能")
    verdicts = []
    for candidates in ([support, negative], [negative, support]):
        result = minimum(
            "支持至少 1 项功能，仅限以下选项：A 功能",
            {"A 功能": list(candidates)},
            threshold="1",
        )
        verdicts.append(result["verdict"])
        state = result["details_json"]["options"][0]
        assert state["reason"] == "conflicting_support_and_negative"
        assert state["support_evidence_ids"]
        assert state["negative_evidence_ids"] == ["ev-2"]
    assert verdicts == ["无法确认", "无法确认"]


def test_minimum_count_retrieval_gaps_or_truncation_degrades_to_unknown():
    text = "支持至少 2 项功能，仅限以下选项：A 功能、B 功能"
    mapping = {
        "A 功能": [make_candidate("cap-a", "支持 A 功能")],
        "B 功能": [],
    }
    for issue in (
        {"gaps": [{"reason": "evidence_read_failed"}]},
        {"truncated": True},
    ):
        result = minimum(text, mapping, issues={"B 功能": issue})
        assert result["verdict"] == "无法确认"
        details = result["details_json"]
        assert details["complete_set"] is True
        assert details["retrieval_issues"]
        state = next(item for item in details["options"] if item["option"] == "B 功能")
        assert state["verdict"] == "无法确认"
        assert state["reason"] == "retrieval_incomplete"
        assert state["retrieval_gaps"] or state["retrieval_truncated"]


def test_source_or_document_evidence_each_supports():
    for kind in ("source", "document"):
        result = evaluate(
            "支持 IPv6 协议解析",
            [make_candidate("cap-ipv6", "支持 IPv6 协议解析", kind=kind)],
        )
        assert result["verdict"] == "支持", (kind, result)


def test_no_evidence_is_still_unknown():
    assert evaluate("支持 IPv6 协议解析", [])["verdict"] == "无法确认"
