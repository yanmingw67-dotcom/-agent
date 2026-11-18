"""单条需求只读检索契约，不包含符合性结论或写入工具。"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.knowledge.review_schemas import Assertion, ReviewedContent


class ProbeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    category_id: str = Field(min_length=1, max_length=64)
    requirement_text: str = Field(min_length=1, max_length=2000)
    limit: int = Field(default=10, ge=1, le=20)

    @field_validator("category_id", "requirement_text")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("输入不能为空白")
        return value.strip()


class ProbeGap(BaseModel):
    code: str
    capability_id: str | None = None
    relation_id: str | None = None
    evidence_id: str | None = None


class ProbeEvidence(BaseModel):
    id: str
    snapshot_id: str
    kind: Literal["source", "document"]
    source_ref: dict
    locator: dict
    source_hash: str
    quote_hash: str
    quote: str
    verification_state: Literal["source_verified"]
    semantic_verification: Literal["unverified"]
    warnings: list[str]


class ProbeCapability(BaseModel):
    capability_id: str
    revision_id: str
    declaration_type: Literal["product_function", "technical_mechanism"]
    content: ReviewedContent
    assertions: list[Assertion]
    reachability_evidence_ids: list[str]
    evidence_ids: list[str]
    review_status: Literal["human_accepted"]
    reviewer_id: str
    reviewed_at: str
    match_terms: list[str]
    retrieval_basis: Literal["lexical_overlap_not_compliance"] = "lexical_overlap_not_compliance"


class ProbeRelation(BaseModel):
    id: str
    type: Literal[
        "same_as",
        "implements",
        "depends_on",
        "composes_with",
        "subset_of",
        "possible_equivalent",
        "related_to",
    ]
    source_revision_id: str
    target_revision_id: str
    status: Literal["human_reviewed", "hypothesis"]
    details: dict


class ProbeCoverageSummary(BaseModel):
    """普通读取契约：只保留覆盖计数和缺口类型，不含内部明细。"""

    model_config = ConfigDict(extra="forbid")
    coverage_incomplete: bool
    knowledge_build_complete: bool
    semantic_coverage: str
    source_file_count: int = Field(ge=0)
    uninvestigated_file_count: int = Field(ge=0)
    unexplained_entrypoint_count: int = Field(ge=0)
    pending_investigation_count: int = Field(ge=0)
    deferred_investigation_count: int = Field(ge=0)
    unverified_assertion_count: int = Field(ge=0)
    unverified_document_segment_count: int = Field(ge=0)
    documents_truncated: bool
    gap_types: list[str]


class ProbeResponse(BaseModel):
    status: Literal["candidate_retrieval", "no_match", "knowledge_empty", "partial"]
    category_id: str
    publication_id: str
    snapshot_id: str
    publication_hash: str
    candidates: list[ProbeCapability]
    relations: list[ProbeRelation]
    evidence: list[ProbeEvidence]
    coverage: ProbeCoverageSummary
    unverified_gaps: list[ProbeGap]
    warnings: list[str]
    truncated: bool = False
    next_cursor: None = None
    formal_compliance_decision: None = None
    support_status: Literal["not_assessed"] = "not_assessed"
    consistency: Literal["request_start_publication"] = "request_start_publication"
    duration_ms: float = 0
