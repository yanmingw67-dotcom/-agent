"""R02/R05 双源语义匹配：只输出潜在关系，不自动合并或裁决。"""

from types import SimpleNamespace

from app.knowledge.conflicts import match_pair, similarity


def claim(name, channel="document", claim_type="current_capability", **draft):
    payload = {"name": name, "claim_type": claim_type, **draft}
    return SimpleNamespace(
        id=f"{channel}-{name}",
        name=name,
        source_channel=channel,
        draft_json=payload,
    )


def test_different_wording_same_capability_is_possible_duplicate():
    left = claim("模板导入", channel="source")
    right = claim("导入模板", channel="document")
    assert similarity("模板导入", "导入模板") >= 0.66
    kind, details = match_pair(left, right)
    assert kind == "possible_duplicate"
    assert details["requires_human_resolution"] is True
    assert details["match"]["auto_merge"] is False
    assert details["match"]["auto_resolve"] is False
    assert details["risk"] == "medium"


def test_support_and_explicit_exclusion_are_flagged_even_with_different_wording():
    left = claim("模板导入", channel="document")
    right = claim(
        "模板导入",
        channel="document",
        claim_type="exclusion",
        action="不支持模板导入",
        target="模板导入",
    )
    kind, details = match_pair(left, right)
    assert kind == "support_exclusion_conflict"
    assert details["risk"] == "high"
    assert details["left_claim_type"] == "current_capability"
    assert details["right_claim_type"] == "exclusion"


def test_exclusion_action_without_claim_type_is_still_flagged():
    left = claim("模板导入", channel="document")
    right = claim(
        "模板导入",
        channel="document",
        action="当前版本不支持模板导入",
        target="模板导入",
    )
    kind, details = match_pair(left, right)
    assert kind == "support_exclusion_conflict"
    assert details["risk"] == "high"


def test_product_form_and_component_differences_are_not_reported_as_conflict():
    left = claim(
        "策略导出",
        channel="source",
        action="控制台导出策略",
        target="策略",
    )
    right = claim(
        "策略导出",
        channel="document",
        action="命令行导出策略",
        target="策略",
    )
    assert match_pair(left, right) is None

    node_left = claim(
        "规则同步",
        channel="source",
        action="规则同步到管理节点",
        target="规则",
    )
    node_right = claim(
        "规则同步",
        channel="document",
        action="规则同步到边缘节点",
        target="规则",
    )
    assert match_pair(node_left, node_right) is None


def test_scope_and_condition_differences_are_not_reported_as_duplicate():
    left = claim(
        "策略导出",
        channel="source",
        supported_scope=["仅支持集群部署"],
    )
    right = claim(
        "策略导出",
        channel="document",
        supported_scope=["仅支持单机部署"],
    )
    assert match_pair(left, right) is None

    left = claim("日志导出", channel="source", conditions=["必须开启审计模式"])
    right = claim("日志导出", channel="document", conditions=["必须关闭审计模式"])
    assert match_pair(left, right) is None


def test_configuration_differences_are_not_reported_as_duplicate():
    left = claim("策略导出", channel="source", action="默认开启策略导出")
    right = claim("策略导出", channel="document", action="默认关闭策略导出")
    assert match_pair(left, right) is None

    required = claim("日志导出", channel="source", conditions=["必须开启审计模式"])
    optional = claim("日志导出", channel="document", conditions=["可选开启审计模式"])
    assert match_pair(required, optional) is None


def test_same_source_same_capability_is_not_a_dual_source_duplicate():
    left = claim("模板导入", channel="document")
    right = claim("导入模板", channel="document")
    assert match_pair(left, right) is None


def test_same_scope_support_and_exclusion_remains_a_conflict():
    left = claim(
        "规则同步",
        channel="source",
        supported_scope=["仅支持集群部署"],
        conditions=["必须开启审计模式"],
    )
    right = claim(
        "规则同步",
        channel="document",
        claim_type="exclusion",
        action="不支持规则同步",
        target="规则同步",
        supported_scope=["仅支持集群部署"],
        conditions=["必须开启审计模式"],
    )
    kind, details = match_pair(left, right)
    assert kind == "support_exclusion_conflict"
    assert details["requires_human_resolution"] is True
    assert details["match"]["applicability_relation"] == "overlap"
    assert details["match"]["auto_merge"] is False
    assert details["match"]["auto_resolve"] is False


def test_explicitly_exclusive_conditions_skip_conflict():
    left = claim("日志导出", channel="source", conditions=["必须开启审计模式"])
    right = claim(
        "日志导出",
        channel="document",
        claim_type="exclusion",
        action="不支持日志导出",
        target="日志导出",
        conditions=["必须关闭审计模式"],
    )
    assert match_pair(left, right) is None


def test_unknown_scope_does_not_suppress_support_exclusion_conflict():
    left = claim("模板导入", channel="source")
    right = claim(
        "模板导入",
        channel="document",
        claim_type="exclusion",
        action="不支持模板导入",
        target="模板导入",
    )
    kind, details = match_pair(left, right)
    assert kind == "support_exclusion_conflict"
    assert details["match"]["applicability_relation"] == "unknown"


def test_same_scope_with_different_wording_does_not_hide_conflict():
    left = claim("策略同步", channel="source", supported_scope=["仅支持集群部署"])
    right = claim(
        "策略同步",
        channel="document",
        claim_type="exclusion",
        action="不支持策略同步",
        target="策略同步",
        supported_scope=["只支持集群环境"],
    )
    kind, _ = match_pair(left, right)
    assert kind == "support_exclusion_conflict"
