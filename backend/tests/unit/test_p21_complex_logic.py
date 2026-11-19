"""R03：复杂混合逻辑、括号、阈值、可选项与逻辑树。"""

from app.tender.rules import extract
from app.tender.structure import parse_text


def run(text):
    result = extract(parse_text(text))
    assert len(result.candidates) == 1, [item.original_text for item in result.candidates]
    return result.candidates[0]


def test_mixed_and_or_with_parentheses_keeps_one_parameter():
    candidate = run("1、支持（A或B）且C。")
    requirements = candidate.atomic_requirements
    assert candidate.logic_tree["relation"] == "AND"
    or_nodes = [node for node in candidate.logic_tree["children"] if node["relation"] == "OR"]
    assert or_nodes
    assert len(or_nodes[0]["children"]) == 2
    assert [item["original_text"] for item in requirements[:2]] == ["A", "B"]
    assert requirements[-1]["original_text"] == "C"
    assert requirements[0]["logical_relation"] == "OR"
    assert requirements[0]["depth"] > 0


def test_nested_parentheses_are_tree_not_extra_top_level_parameters():
    result = extract(parse_text("1、支持（（A或B）且C）。\n2、支持D。"))
    assert len(result.candidates) == 2
    tree = result.candidates[0].logic_tree
    assert tree["relation"] == "AND"
    assert tree["children"][0]["relation"] == "OR"
    assert [item["original_text"] for item in result.candidates[0].atomic_requirements] == [
        "A",
        "B",
        "C",
    ]


def test_at_least_n_and_threshold_units_are_preserved():
    candidate = run("1、设备吞吐量不低于 100 Gbps，支持至少3项攻击检测能力。")
    requirements = candidate.atomic_requirements
    assert requirements[0]["threshold"] == "100"
    assert requirements[0]["unit"] == "Gbps"
    minimum = [item for item in requirements if item["requirement_type"] == "minimum_count"]
    assert minimum
    assert minimum[0]["threshold"] == "3"
    assert minimum[0]["unit"] == "项"
    assert minimum[0]["logical_relation"] == "AT_LEAST"


def test_required_and_optional_are_distinguished():
    candidate = run("1、必须支持双机热备，可选支持IPv6。")
    requirements = candidate.atomic_requirements
    assert [(item["original_text"], item["is_mandatory"]) for item in requirements] == [
        ("必须支持双机热备", True),
        ("可选支持IPv6", False),
    ]


def test_open_enumeration_is_not_automatically_item_by_item_mandatory():
    candidate = run("1、包括但不限于A、B、C。")
    assert candidate.logic_tree["relation"] == "AND_OPEN"
    assert all(item["is_mandatory"] is False for item in candidate.atomic_requirements)
    assert all(item["ambiguity"] == "open_enumeration" for item in candidate.atomic_requirements)


def test_blank_threshold_keeps_ambiguity_without_inventing_value():
    candidate = run("1、设备吞吐量不低于 ______ Gbps。")
    requirement = candidate.atomic_requirements[0]
    assert requirement["threshold"] is None
    assert requirement["unit"] == "Gbps"
    assert requirement["ambiguity"] == "blank_threshold"
    assert (
        candidate.original_text[
            requirement["source_span"]["start"] : requirement["source_span"]["end"]
        ]
        == requirement["original_text"]
    )


def test_each_atomic_requirement_has_its_own_anchor_and_top_level_count_is_stable():
    result = extract(parse_text("1、支持（A或B）且C。\n2、支持D。\n3、支持E。"))
    assert len(result.candidates) == 3
    first = result.candidates[0]
    spans = [tuple(item["source_span"].values()) for item in first.atomic_requirements]
    assert len(spans) == len(set(spans))
    text = first.original_text
    for item in first.atomic_requirements:
        start = item["source_span"]["start"]
        end = item["source_span"]["end"]
        assert 0 <= start < end <= len(text)
        assert text[start:end] == item["original_text"]


def test_explicit_subclauses_can_contain_their_own_logic():
    candidate = run("1、支持 Web 防护：（1）A或B；（2）C。")
    assert len(candidate.atomic_requirements) == 3
    assert candidate.atomic_requirements[0]["logical_relation"] == "OR"
