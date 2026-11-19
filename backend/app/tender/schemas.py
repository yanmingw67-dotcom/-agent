"""P2.1 API 请求模型。"""

from pydantic import BaseModel, Field


class TextCreate(BaseModel):
    category_id: str = Field(min_length=1, max_length=36)
    text: str = Field(min_length=1, max_length=2_000_000)
    idempotency_key: str | None = Field(default=None, max_length=128)
    title: str | None = Field(default=None, max_length=200)


class ParameterEdit(BaseModel):
    parameter_name: str | None = Field(default=None, max_length=500)
    original_text: str | None = Field(default=None, max_length=2_000_000)


class SplitPart(BaseModel):
    parameter_name: str | None = Field(default=None, max_length=500)
    original_text: str = Field(min_length=1, max_length=2_000_000)


class SplitRequest(BaseModel):
    parts: list[SplitPart] = Field(min_length=2, max_length=50)


class MergeRequest(BaseModel):
    parameter_ids: list[str] = Field(min_length=2, max_length=50)
    parameter_name: str | None = Field(default=None, max_length=500)


class AddRequest(BaseModel):
    parameter_name: str | None = Field(default=None, max_length=500)
    original_text: str = Field(min_length=1, max_length=2_000_000)
