"""模型只产生草稿；来源核验不代表业务能力已验证。"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Short = Annotated[str, Field(min_length=1, max_length=1000)]
Notes = Annotated[list[Short], Field(max_length=30)]
Ids = Annotated[list[str], Field(max_length=30)]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EvidenceRequest(Strict):
    kind: Literal["source", "document"]
    file_id: str | None = None
    segment_id: str | None = None
    start_line: int = Field(default=1, ge=1)
    end_line: int = Field(default=1, ge=1)
    expected_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    supported_statement: Short

    @model_validator(mode="after")
    def reference(self):
        if self.kind == "source":
            if not self.file_id or self.segment_id or self.end_line < self.start_line:
                raise ValueError("源码证据必须有文件ID和有效行号")
        elif not self.segment_id or self.file_id:
            raise ValueError("文档证据必须有分段ID")
        return self


class EvidenceReceipt(Strict):
    evidence_id: str
    snapshot_id: str
    verification_state: Literal["source_verified"] = "source_verified"
    semantic_verification: Literal["unverified"] = "unverified"
    source_ref: dict
    locator: dict
    quote_hash: str
    warnings: list[str] = Field(default_factory=list)


class CapabilityClaimDraft(Strict):
    key: Annotated[str, Field(min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9_-]+$")]
    name: Short
    category: Short
    action: Short
    target: Short
    business_goal: Short
    implementation_mechanism: Annotated[str, Field(min_length=1, max_length=3000)]
    supported_scope: Notes = Field(default_factory=list)
    conditions: Notes = Field(default_factory=list)
    limitations: Notes = Field(default_factory=list)
    evidence_ids: Ids = Field(default_factory=list)
    unresolved_questions: Notes = Field(default_factory=list)
    status: Literal["candidate", "evidence_found"] = "candidate"


class CandidateRelation(Strict):
    source_key: Short
    target_key: Short
    relation_type: Literal[
        "same_as",
        "implements",
        "depends_on",
        "composes_with",
        "subset_of",
        "possible_equivalent",
        "related_to",
    ]
    rationale: Short
    evidence_ids: Ids = Field(default_factory=list)
    unverified_gaps: Notes = Field(min_length=1)


class ModuleInvestigationOutput(Strict):
    module_id: str
    module_summary: Annotated[str, Field(max_length=3000)]
    capabilities: list[CapabilityClaimDraft] = Field(default_factory=list, max_length=20)
    candidate_relations: list[CandidateRelation] = Field(default_factory=list, max_length=30)
    unexplained_entrypoint_ids: Ids = Field(default_factory=list)
    open_questions: Notes = Field(default_factory=list)

    @model_validator(mode="after")
    def local_keys(self):
        keys = [c.key for c in self.capabilities]
        if len(keys) != len(set(keys)):
            raise ValueError("能力局部key重复")
        for rel in self.candidate_relations:
            if rel.source_key not in keys or rel.target_key not in keys:
                raise ValueError("关系只能引用本输出中存在的局部能力key")
        return self


class InvestigationRequest(Strict):
    module_ids: list[str] = Field(default_factory=list, max_length=24)
    data_classification: Literal["synthetic_public", "enterprise"] = "enterprise"
    investigation_mode: Literal["bounded", "explore"] = "explore"
    capability_limit: int = Field(default=1, ge=1, le=3)
    include_equivalence: bool = False
