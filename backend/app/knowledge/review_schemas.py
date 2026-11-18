from typing import Annotated, Literal

from pydantic import Field

from app.knowledge.schemas import Short, Strict

Declaration = Literal[
    "product_function", "technical_mechanism", "investigation_action", "invalid", "unknown"
]
RelationType = Literal[
    "same_as",
    "implements",
    "depends_on",
    "composes_with",
    "subset_of",
    "possible_equivalent",
    "related_to",
]


class ReviewedContent(Strict):
    name: Short
    action: Short
    target: Short
    business_goal: Short
    implementation_mechanism: Short
    supported_scope: list[Short] = Field(min_length=1, max_length=10)
    conditions: list[Short] = Field(default_factory=list, max_length=10)
    limitations: list[Short] = Field(min_length=1, max_length=10)


class Dependency(Strict):
    file_id: str
    symbol_id: str
    evidence_ids: list[str] = Field(min_length=1, max_length=10)


class Assertion(Strict):
    field: Annotated[str, Field(max_length=100)]
    text: Short
    evidence_ids: list[str] = Field(min_length=1, max_length=10)
    semantic_decision: Literal["supported", "unverified", "contradicted"] = "unverified"
    rationale: Short
    dependencies: list[Dependency] = Field(default_factory=list, max_length=10)
    implementation_evidence_ids: list[str] = Field(default_factory=list, max_length=10)
    # P1.6：证据类型只表示来源，不表示权威高低；文档支持须管理员逐断言确认引文。
    proof_basis: Literal["source", "document", "mixed"] | None = None
    document_scope_confirmed: bool = False


class ReviewRequest(Strict):
    action: Literal["modify", "accept", "return", "reject"]
    expected_revision: int = Field(ge=0)
    declaration_type: Declaration = "unknown"
    content: ReviewedContent | None = None
    assertions: list[Assertion] = Field(default_factory=list, max_length=50)
    reachability_evidence_ids: list[str] = Field(default_factory=list, max_length=10)
    comment: Short
    human_confirmed: bool = False
    availability_basis: Literal["source_runtime", "document_declared", "mixed"] | None = None
    product_scope_confirmed: bool = False


class RelationRequest(Strict):
    source_revision_id: str
    target_revision_id: str
    relation_type: RelationType
    decision: Literal["accept", "reject", "return"]
    evidence_ids: list[str] = Field(min_length=1, max_length=20)
    rationale: Short
    differences: Short
    input_contract: Short
    output_contract: Short
    conditions_and_errors: Short
    human_confirmed: bool = False


class MergeRequest(Strict):
    source_claim_id: str
    target_claim_id: str
    source_revision: int = Field(ge=1)
    target_revision: int = Field(ge=1)
    relation_type: Literal["same_as"]
    comment: Short
    human_confirmed: bool = False


class PublishRequest(Strict):
    revision_ids: list[str] = Field(min_length=1, max_length=100)
    relation_ids: list[str] = Field(default_factory=list, max_length=100)
    expected_active_publication_id: str | None
    acknowledge_coverage_gaps: bool = False
    comment: Short


class RollbackRequest(Strict):
    publication_id: str
    expected_active_publication_id: str | None
    comment: Short


class ConflictResolutionRequest(Strict):
    decision: Literal["resolved", "dismissed"]
    explanation: Short
    human_confirmed: bool = False
