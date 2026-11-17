from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class RunContext(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    category_id: str
    snapshot_id: str
    principal_id: str
    job_id: str | None = None
    policy: Literal["published", "admin_staging"] = "published"


class RetrievalItem(BaseModel):
    id: str | None = None
    file_id: str | None = None
    symbol_id: str | None = None
    document_id: str | None = None
    module_id: str | None = None
    path: str | None = None
    name: str | None = None
    kind: str | None = None
    status: str | None = None
    language: str | None = None
    role: str | None = None
    sha256: str | None = None
    start_line: int | None = None
    end_line: int | None = None
    snippet: str | None = None
    locator: dict = Field(default_factory=dict)
    match_terms: list[str] = Field(default_factory=list)
    unresolved: bool = False


class ModuleContextView(BaseModel):
    symbols: list[RetrievalItem] = Field(default_factory=list)
    document_candidates: list[RetrievalItem] = Field(default_factory=list)
    dependencies: list[RetrievalItem] = Field(default_factory=list)
    dependencies_status: str = "unresolved"
    initial_clues: list[str] = Field(default_factory=list)
    truncated: bool = False
    document_matching: str = "module_name_lexical_candidates_not_verified_links"


class ToolEnvelope(BaseModel):
    snapshot_id: str
    tool: str
    results: list[RetrievalItem] = Field(default_factory=list)
    truncated: bool = False
    coverage_incomplete: bool = False
    next_cursor: str | None = None
    warnings: list[str] = Field(default_factory=list)
    duration_ms: float = 0
    module_context: ModuleContextView | None = None


class ToolRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tool: Literal[
        "get_project_map",
        "get_module_context",
        "list_entrypoints",
        "search_code_batch",
        "search_symbols",
        "search_paths",
        "read_source",
        "find_references",
        "search_documents",
        "read_document",
    ]
    query: str = Field(default="", max_length=200)
    queries: list[str] = Field(default_factory=list, max_length=8)
    mode: Literal["literal", "regex"] = "literal"
    module_ids: list[str] = Field(default_factory=list, max_length=20)
    kinds: list[str] = Field(default_factory=list, max_length=10)
    types: list[str] = Field(default_factory=list, max_length=10)
    module_id: str | None = None
    file_id: str | None = None
    symbol_id: str | None = None
    segment_id: str | None = Field(
        default=None, description="search_documents返回的results[].id；不是document_id，禁止猜测"
    )
    start_line: int = Field(default=1, ge=1)
    end_line: int | None = Field(default=None, ge=1)
    limit: int = Field(default=30, ge=1, le=100)
    cursor: int = Field(default=0, ge=0, le=100000)
