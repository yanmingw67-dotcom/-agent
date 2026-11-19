"""P3.1 原文定位回归：原子要求的 source_span 必须逐字回指参数原文。"""

from app.tender.rules import extract
from app.tender.structure import parse_text


def _assert_spans(candidate):
    for requirement in candidate.atomic_requirements:
        start = requirement["source_span"]["start"]
        end = requirement["source_span"]["end"]
        assert candidate.original_text[start:end] == requirement["original_text"], {
            "requirement": requirement["original_text"],
            "span_text": candidate.original_text[start:end],
        }


def test_explicit_subclause_spans_match_after_capability_prefix():
    result = extract(
        parse_text("1、测试参数\n（1） 支持 A 检测，支持 B 检测。\n（2）支持 C 检测。")
    )
    assert len(result.candidates) == 1
    _assert_spans(result.candidates[0])


def test_merged_explicit_subclause_spans_match():
    result = extract(
        parse_text(
            "1、规则库\n（1） 支持规则在线升级，支持离线升级。\n"
            "2、HTTP 解析\n（1） 支持 HTTP/2，支持 WebSocket。"
        )
    )
    assert len(result.candidates) == 2
    for candidate in result.candidates:
        _assert_spans(candidate)
