"""R04：每个锚点必须绑定到原始分段，而不是聚合单元文本。"""

from app.knowledge.document_semantics import (
    SemanticBatch,
    SemanticCandidate,
    SemanticUnit,
    validate_batch,
)


def semantic_unit(segment_texts):
    return SemanticUnit(
        key="unit-1",
        document_id="doc-1",
        filename="manual.docx",
        segment_ids=list(segment_texts),
        locator={"paragraph": 1},
        text=" ".join(segment_texts.values()),
        classification="current_capability",
        segment_texts=segment_texts,
    )


def candidate(quote, segment_id):
    return SemanticCandidate(
        name="透明桥部署",
        action="支持透明桥接入",
        target="透明桥",
        supported_scope=["透明桥"],
        anchors=[{"segment_id": segment_id, "quote": quote}],
    )


def test_quote_must_belong_to_the_anchor_segment_not_the_aggregate_unit():
    unit = semantic_unit({"segment-a": "当前版本支持透明桥接入", "segment-b": "旁路镜像"})
    units = {segment_id: unit for segment_id in unit.segment_ids}
    accepted, gaps = validate_batch(
        SemanticBatch(candidates=[candidate("旁路镜像", "segment-a")]), units
    )
    assert accepted == []
    assert gaps[0]["code"] == "MODEL_ANCHOR_QUOTE_MISMATCH"


def test_multiple_anchors_are_validated_per_segment_then_merged():
    unit = semantic_unit({"segment-a": "当前版本支持透明桥接入", "segment-b": "旁路镜像"})
    units = {segment_id: unit for segment_id in unit.segment_ids}
    draft = SemanticCandidate(
        name="透明桥部署",
        action="支持透明桥接入",
        target="透明桥",
        supported_scope=["透明桥"],
        anchors=[
            {"segment_id": "segment-a", "quote": "当前版本支持透明桥接入"},
            {"segment_id": "segment-b", "quote": "旁路镜像"},
        ],
    )
    accepted, gaps = validate_batch(SemanticBatch(candidates=[draft]), units)
    assert gaps == []
    assert len(accepted) == 1
    assert [anchor["segment_id"] for anchor in accepted[0]["anchors"]] == [
        "segment-a",
        "segment-b",
    ]
