"""P31-D04：同一条原子要求中的多个独立量化约束必须分别保留。

每个用例都只使用与真实 WAF 参数表同构的文本，不依赖外部模型。
"""

from app.evaluation.atomic import AtomicEvaluator
from app.evaluation.logic import aggregate_parameter
from app.evaluation.retrieval import Candidate, RetrievalResult
from app.tender.rules import extract
from app.tender.structure import parse_text


def run(text):
    result = extract(parse_text(text))
    assert len(result.candidates) == 1, [item.original_text for item in result.candidates]
    return result.candidates[0]


def blank_units(candidate):
    return [
        item["unit"]
        for item in candidate.atomic_requirements
        if item["ambiguity"] == "blank_threshold"
    ]


def assert_spans(candidate):
    for item in candidate.atomic_requirements:
        start = item["source_span"]["start"]
        end = item["source_span"]["end"]
        assert 0 <= start < end <= len(candidate.original_text)
        assert candidate.original_text[start:end] == item["original_text"]


def test_cpu_and_memory_blank_thresholds_are_both_kept_with_units():
    candidate = run(
        "1、软件版单实例资源占用不高于 ______vCPU / ______GB 内存（按规格填写），支持横向弹性扩容。"
    )
    assert blank_units(candidate) == ["vCPU", "GB"]
    memory = [item for item in candidate.atomic_requirements if item["unit"] == "GB"]
    assert len(memory) == 1
    assert memory[0]["threshold"] is None
    assert memory[0]["threshold_operator"] == "不高于"
    assert memory[0]["original_text"] == "______GB 内存（按规格填写）"
    assert_spans(candidate)


def test_three_blank_metrics_keep_their_own_units():
    candidate = run(
        "1、CPU 占用不超过 ______vCPU、内存占用不超过 ______GB、磁盘占用不超过 ______TB。"
    )
    assert blank_units(candidate) == ["vCPU", "GB", "TB"]
    assert all(
        item["threshold"] is None
        for item in candidate.atomic_requirements
        if item["ambiguity"] == "blank_threshold"
    )
    assert_spans(candidate)


def test_fixed_and_blank_constraints_are_kept_together():
    candidate = run("1、网络层吞吐不低于 10Gbps，应用层时延不超过 ______ms。")
    pairs = {(item["threshold"], item["unit"]) for item in candidate.atomic_requirements}
    assert ("10", "Gbps") in pairs
    assert (None, "ms") in pairs
    assert_spans(candidate)


def test_same_unit_different_metrics_are_not_collapsed():
    candidate = run("1、误报率不高于 ______%，漏报率不高于 ______%。")
    actions = [
        item["original_text"]
        for item in candidate.atomic_requirements
        if item["ambiguity"] == "blank_threshold"
    ]
    assert actions == ["误报率不高于 ______%", "漏报率不高于 ______%"]
    assert blank_units(candidate) == ["%", "%"]
    assert_spans(candidate)


def test_different_units_and_metrics_remain_independent():
    candidate = run("1、网络层吞吐不低于 10Gbps，并发连接数不低于 ______万。")
    pairs = {(item["threshold"], item["unit"]) for item in candidate.atomic_requirements}
    assert ("10", "Gbps") in pairs
    assert (None, "万") in pairs
    assert len(candidate.atomic_requirements) >= 2
    assert_spans(candidate)


def test_one_metric_with_two_bounds_keeps_both_bounds():
    candidate = run("1、并发连接数不少于 10000 个且不超过 20000 个。")
    pairs = {(item["threshold"], item["unit"]) for item in candidate.atomic_requirements}
    assert ("10000", "个") in pairs
    assert ("20000", "个") in pairs
    bounds = [
        item for item in candidate.atomic_requirements if item["threshold"] in {"10000", "20000"}
    ]
    assert {item["threshold_operator"] for item in bounds} == {"不少于", "不超过"}
    assert_spans(candidate)


def test_blank_thresholds_inside_parentheses_are_not_lost():
    candidate = run("1、支持容器化部署（CPU 占用不超过 ______vCPU，内存占用不超过 ______GB）。")
    assert blank_units(candidate) == ["vCPU", "GB"]
    assert any("CPU 占用" in item["original_text"] for item in candidate.atomic_requirements)
    assert any(
        item["original_text"].startswith("内存占用") for item in candidate.atomic_requirements
    )
    assert_spans(candidate)


def test_nested_and_or_keeps_all_quantified_leaves():
    candidate = run("1、支持 A 或 B，且 CPU 占用不超过 ______vCPU、内存占用不超过 ______GB。")
    assert blank_units(candidate) == ["vCPU", "GB"]

    def relations(node):
        values = [node.get("relation")]
        for child in node.get("children", []):
            values.extend(relations(child))
        return values

    tree_relations = relations(candidate.logic_tree)
    assert "OR" in tree_relations
    assert "AND" in tree_relations
    assert_spans(candidate)


class FakeKnowledge:
    def __init__(self, candidates):
        self.candidates = candidates

    def retrieve(self, requirement, parameter_name):
        return RetrievalResult(candidates=self.candidates, gaps=[])


def evidence():
    return {
        "id": "ev-1",
        "kind": "source",
        "source_ref": {"file_id": "f1", "path": "src/a.py", "role": "product"},
        "locator": {"start_line": 1, "end_line": 1},
        "source_hash": "a" * 64,
        "quote_hash": "b" * 64,
        "quote": "支持横向弹性扩容",
    }


def supported_candidate():
    return Candidate(
        capability_id="cap-scale",
        revision_id="rev-scale",
        declaration_type="product_function",
        content={
            "name": "弹性扩容",
            "action": "支持横向弹性扩容",
            "target": "",
            "business_goal": "",
            "implementation_mechanism": "",
            "supported_scope": ["已发布范围"],
            "conditions": [],
            "limitations": [],
        },
        assertions=[],
        text="支持横向弹性扩容",
        evidence={"ev-1": evidence()},
        verified=True,
    )


def test_p22_blank_thresholds_are_unknown_even_if_another_leaf_is_supported():
    candidate = run("1、支持横向弹性扩容，内存占用不超过 ______GB。")
    evaluator = AtomicEvaluator(
        FakeKnowledge([supported_candidate()]),
        job_id="job-1",
        principal_id="alice",
        model_mode="structural",
    )
    results = evaluator.evaluate(candidate.atomic_requirements, "示例参数")
    by_id = {item["requirement_id"]: item for item in results}
    scale = next(
        item for item in candidate.atomic_requirements if "横向弹性扩容" in item["original_text"]
    )
    blank = next(
        item for item in candidate.atomic_requirements if "______GB" in item["original_text"]
    )
    assert by_id[scale["requirement_id"]]["verdict"] == "支持"
    assert by_id[blank["requirement_id"]]["verdict"] == "无法确认"
    aggregate = aggregate_parameter(candidate.logic_tree, results)
    assert aggregate["verdict"] == "无法确认"
