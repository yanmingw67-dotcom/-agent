"""P2.2 三值结果与确定性聚合。

模型只能给出单个原子要求的判断；顶层参数结论只由本模块根据 P2.1 logic_tree
计算，禁止把模型自由文本直接当成最终结论。
"""

from typing import Literal

Verdict = Literal["支持", "不支持", "无法确认"]
SUPPORTED = "支持"
UNSUPPORTED = "不支持"
UNKNOWN = "无法确认"


def inverse(verdict: Verdict) -> Verdict:
    if verdict == SUPPORTED:
        return UNSUPPORTED
    if verdict == UNSUPPORTED:
        return SUPPORTED
    return UNKNOWN


def _leaf_state(result: dict | None) -> tuple[Verdict, bool, str]:
    """返回（三值、是否参与必要聚合、原因码）。"""
    if not result:
        return UNKNOWN, True, "missing_atomic_result"
    if result.get("mandatory_status") == "uncertain":
        return UNKNOWN, True, "uncertain_mandatory_status"
    if not result.get("necessary", True):
        return UNKNOWN, False, "optional_excluded"
    verdict = result.get("verdict")
    if verdict not in {SUPPORTED, UNSUPPORTED, UNKNOWN}:
        return UNKNOWN, True, "invalid_atomic_verdict"
    return verdict, True, "leaf"


def _minimum(node: dict, children: list[tuple[Verdict, bool, str]]) -> int | None:
    value = node.get("minimum_count") or node.get("threshold")
    if value is not None:
        try:
            number = int(str(value).strip())
            return number if number > 0 else None
        except (TypeError, ValueError):
            return None
    return None


def aggregate_node(node: dict | None, results: dict[str, dict]) -> dict:
    if not node:
        return {
            "verdict": UNKNOWN,
            "reason": "logic_tree_missing",
            "applicable": True,
            "children": [],
        }
    kind = node.get("kind")
    relation = str(node.get("relation") or node.get("logical_relation") or "").upper()
    if kind == "leaf":
        verdict, applicable, reason = _leaf_state(results.get(node.get("requirement_id")))
        return {
            "verdict": verdict,
            "reason": reason,
            "applicable": applicable,
            "requirement_id": node.get("requirement_id"),
            "children": [],
        }
    if kind != "node":
        return {
            "verdict": UNKNOWN,
            "reason": "logic_node_invalid",
            "applicable": True,
            "children": [],
        }
    if relation in {"UNKNOWN", "AMBIGUOUS"}:
        return {
            "verdict": UNKNOWN,
            "reason": "logic_relation_unknown",
            "applicable": True,
            "children": [],
        }
    child_nodes = node.get("children") or []
    states = [aggregate_node(child, results) for child in child_nodes]
    if relation in {"OPTIONAL"}:
        return {
            "verdict": UNKNOWN,
            "reason": "optional_group_excluded",
            "applicable": False,
            "relation": relation,
            "children": states,
        }
    applicable = [state for state in states if state["applicable"]]
    if not applicable:
        return {
            "verdict": UNKNOWN,
            "reason": "no_required_child",
            "applicable": True,
            "relation": relation,
            "children": states,
        }
    values = [state["verdict"] for state in applicable]
    if relation in {"AND", "AND_OPEN"}:
        if UNSUPPORTED in values:
            verdict, reason = UNSUPPORTED, "and_has_unsupported"
        elif UNKNOWN in values:
            verdict, reason = UNKNOWN, "and_has_unknown"
        else:
            verdict, reason = SUPPORTED, "and_all_supported"
    elif relation in {"OR", "OR_OPEN"}:
        if SUPPORTED in values:
            verdict, reason = SUPPORTED, "or_any_supported"
        elif all(value == UNSUPPORTED for value in values):
            verdict, reason = UNSUPPORTED, "or_all_unsupported"
        else:
            verdict, reason = UNKNOWN, "or_no_supported_evidence"
    elif relation in {"AT_LEAST", "MINIMUM", "MINIMUM_COUNT"}:
        required = _minimum(node, applicable)
        if required is None:
            verdict, reason = UNKNOWN, "minimum_count_missing"
        else:
            supported = values.count(SUPPORTED)
            unknown = values.count(UNKNOWN)
            if supported >= required:
                verdict, reason = SUPPORTED, "at_least_supported"
            elif supported + unknown < required:
                verdict, reason = UNSUPPORTED, "at_least_impossible"
            else:
                verdict, reason = UNKNOWN, "at_least_insufficient_evidence"
    else:
        verdict, reason = UNKNOWN, "logic_relation_unsupported"
    return {
        "verdict": verdict,
        "reason": reason,
        "applicable": True,
        "relation": relation,
        "required_count": _minimum(node, applicable)
        if relation in {"AT_LEAST", "MINIMUM", "MINIMUM_COUNT"}
        else None,
        "children": states,
    }


def aggregate_parameter(tree: dict | None, atomic_results: list[dict]) -> dict:
    mapping = {item["requirement_id"]: item for item in atomic_results}
    state = aggregate_node(tree, mapping)
    uncertain = [
        item["requirement_id"]
        for item in atomic_results
        if item.get("mandatory_status") == "uncertain"
    ]
    if uncertain:
        state = {
            **state,
            "verdict": UNKNOWN,
            "reason": "uncertain_requirements_present",
            "uncertain_requirement_ids": uncertain[:20],
        }
    counts = {SUPPORTED: 0, UNSUPPORTED: 0, UNKNOWN: 0}
    for item in atomic_results:
        if item.get("necessary", True) and item.get("verdict") in counts:
            counts[item["verdict"]] += 1
    return {
        "verdict": state["verdict"],
        "reason": state["reason"],
        "counts": counts,
        "tree": state,
        "uncertain_requirement_ids": uncertain[:20],
    }
