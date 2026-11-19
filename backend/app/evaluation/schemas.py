"""P2.2 请求与模型输出结构；模型输出必须通过 Pydantic 严格校验。"""

from typing import Annotated, Literal

from pydantic import Field

from app.knowledge.schemas import Strict

Verdict = Literal["支持", "不支持", "无法确认"]
Relation = Literal["supports", "contradicts", "unknown"]


class EvaluationCreate(Strict):
    extraction_job_id: Annotated[str, Field(min_length=1, max_length=36)]
    idempotency_key: Annotated[str, Field(min_length=1, max_length=128)] | None = None


class EvidenceAnchor(Strict):
    evidence_id: Annotated[str, Field(min_length=1, max_length=80)]
    quote: Annotated[str, Field(min_length=2, max_length=300)]


class ModelJudgment(Strict):
    requirement_id: Annotated[str, Field(min_length=1, max_length=36)]
    relation: Relation
    capability_id: Annotated[str, Field(max_length=80)] = ""
    evidence_ids: list[Annotated[str, Field(min_length=1, max_length=80)]] = Field(
        default_factory=list, max_length=20
    )
    anchors: list[EvidenceAnchor] = Field(default_factory=list, max_length=20)
    rationale: Annotated[str, Field(default="", max_length=500)]
    equivalence: Literal[
        "identical", "equivalent_effect", "different_mechanism", "insufficient"
    ] = "insufficient"
    mechanism_compatible: bool = True


class ModelBatch(Strict):
    judgments: list[ModelJudgment] = Field(default_factory=list, max_length=40)
    notes: list[Annotated[str, Field(max_length=300)]] = Field(default_factory=list, max_length=8)
