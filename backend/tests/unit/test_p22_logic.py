"""P2.2 确定性聚合表：最终只有支持/不支持/无法确认。"""

from app.evaluation.logic import aggregate_parameter


def leaf(rid, verdict, *, necessary=True, status="required"):
    return {
        "requirement_id": rid,
        "verdict": verdict,
        "necessary": necessary,
        "mandatory_status": status,
    }


def node(relation, children, **extra):
    return {"kind": "node", "relation": relation, "children": children, **extra}


def leaf_node(rid):
    return {"kind": "leaf", "requirement_id": rid, "children": []}


def evaluate(relation, verdicts, **extra):
    children = []
    results = []
    for index, (verdict, necessary, status) in enumerate(verdicts, 1):
        rid = f"r{index}"
        children.append(leaf_node(rid))
        results.append(leaf(rid, verdict, necessary=necessary, status=status))
    tree = node(relation, children, **extra)
    return aggregate_parameter(tree, results)


def test_and_true_unknown_false_table():
    assert evaluate("AND", [("支持", True, "required")])["verdict"] == "支持"
    assert (
        evaluate("AND", [("支持", True, "required"), ("支持", True, "required")])["verdict"]
        == "支持"
    )
    assert (
        evaluate("AND", [("支持", True, "required"), ("无法确认", True, "required")])["verdict"]
        == "无法确认"
    )
    assert (
        evaluate("AND", [("支持", True, "required"), ("不支持", True, "required")])["verdict"]
        == "不支持"
    )
    assert (
        evaluate(
            "AND",
            [
                ("支持", True, "required"),
                ("无法确认", True, "required"),
                ("不支持", True, "required"),
            ],
        )["verdict"]
        == "不支持"
    )


def test_or_semantics_do_not_require_all():
    assert (
        evaluate("OR", [("支持", True, "required"), ("不支持", True, "required")])["verdict"]
        == "支持"
    )
    assert (
        evaluate("OR", [("不支持", True, "required"), ("不支持", True, "required")])["verdict"]
        == "不支持"
    )
    assert (
        evaluate("OR", [("无法确认", True, "required"), ("不支持", True, "required")])["verdict"]
        == "无法确认"
    )


def test_at_least_counts_supported_and_unknown():
    two = evaluate(
        "AT_LEAST",
        [("支持", True, "required"), ("支持", True, "required"), ("无法确认", True, "required")],
        minimum_count=2,
    )
    assert two["verdict"] == "支持"
    need_more = evaluate(
        "AT_LEAST",
        [
            ("支持", True, "required"),
            ("无法确认", True, "required"),
            ("无法确认", True, "required"),
        ],
        minimum_count=2,
    )
    assert need_more["verdict"] == "无法确认"
    impossible = evaluate(
        "AT_LEAST",
        [("支持", True, "required"), ("不支持", True, "required"), ("不支持", True, "required")],
        minimum_count=2,
    )
    assert impossible["verdict"] == "不支持"
    missing_n = evaluate("AT_LEAST", [("支持", True, "required")])
    assert missing_n["verdict"] == "无法确认"


def test_optional_is_excluded_not_treated_as_and():
    result = evaluate(
        "AND",
        [("支持", True, "required"), ("不支持", False, "optional")],
    )
    assert result["verdict"] == "支持"
    optional_only = evaluate("AND", [("支持", False, "optional")])
    assert optional_only["verdict"] == "无法确认"


def test_unknown_relation_and_missing_tree_are_unknown():
    tree = node("UNKNOWN", [leaf_node("r1")])
    assert aggregate_parameter(tree, [leaf("r1", "支持")])["verdict"] == "无法确认"
    assert aggregate_parameter(None, [leaf("r1", "支持")])["verdict"] == "无法确认"
    assert aggregate_parameter({"kind": "mystery"}, [])["verdict"] == "无法确认"


def test_missing_leaf_result_is_unknown_not_unsupported():
    tree = node("AND", [leaf_node("absent")])
    assert aggregate_parameter(tree, [])["verdict"] == "无法确认"


def test_uncertain_requirement_forces_unknown():
    tree = node("AND", [leaf_node("r1")])
    result = aggregate_parameter(tree, [leaf("r1", "支持", status="uncertain")])
    assert result["verdict"] == "无法确认"
    assert result["reason"] == "uncertain_requirements_present"


def test_blank_threshold_atom_result_flows_to_unknown():
    tree = node("AND", [leaf_node("r1")])
    assert aggregate_parameter(tree, [leaf("r1", "无法确认")])["verdict"] == "无法确认"
