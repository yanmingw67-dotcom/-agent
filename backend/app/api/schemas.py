from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class CategoryCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    name: str = Field(min_length=1, max_length=200)


class CategoryView(BaseModel):
    id: str
    code: str
    name: str
    knowledge_ready: bool


class ImportAccepted(BaseModel):
    snapshot_id: str
    job_id: str


class JobView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    snapshot_id: str
    job_type: str
    stage: str
    status: str
    error_code: str | None
    metrics_json: dict
    attempt: int
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
