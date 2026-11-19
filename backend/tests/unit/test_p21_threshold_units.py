"""P3.1 真实量化条件回归：单位保留与“至少包括”语义。"""

from app.tender.rules import extract
from app.tender.structure import parse_text


def _requirements(text):
    result = extract(parse_text(text))
    assert len(result.candidates) == 1, [item.original_text for item in result.candidates]
    return result.candidates[0].atomic_requirements


def test_count_time_size_and_frequency_units_are_preserved():
    requirements = _requirements(
        "1、内置规则不少于 3000 条；日志留存不少于 6 个月；存储不少于 7 天；"
        "升级包不小于 1MB；升级频率不低于每周 1 次。"
    )
    pairs = {(item["threshold"], item["unit"]) for item in requirements if item["threshold"]}
    assert ("3000", "条") in pairs
    assert ("6", "个月") in pairs
    assert ("7", "天") in pairs
    assert ("1", "MB") in pairs
    assert ("1", "次") in pairs


def test_blank_percent_and_vcpu_units_are_preserved():
    requirements = _requirements("1、误报率不高于 ______%；资源占用不高于 ______vCPU。")
    blank = [item for item in requirements if item["ambiguity"] == "blank_threshold"]
    assert any(item["unit"] == "%" for item in blank)
    assert any(item["unit"] == "vCPU" for item in blank)
    assert all(item["threshold"] is None for item in blank)


def test_at_least_include_is_open_enumeration_not_blank_threshold():
    requirements = _requirements("1、覆盖 OWASP Top 10，至少包括 SQL 注入、XSS 等攻击。")
    assert any(item["ambiguity"] == "open_enumeration" for item in requirements)
    assert all(item["ambiguity"] != "blank_threshold" for item in requirements)
