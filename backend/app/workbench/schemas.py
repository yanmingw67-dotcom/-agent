"""P2.3 请求结构；人工复核不接受参数 ID 或用户 ID 覆盖。"""

from typing import Annotated, Literal

from pydantic import Field

from app.knowledge.schemas import Strict

Verdict = Literal["支持", "不支持", "无法确认"]


class ReviewCreate(Strict):
    action: Literal["accept", "adjust", "reject", "reassess"]
    final_verdict: Verdict | None = None
    comment: Annotated[str, Field(max_length=2000)] = ""
    adjust_reason: Annotated[str, Field(max_length=2000)] = ""
    requires_reevaluation: bool = False


class ExportCreate(Strict):
    format: Literal["docx", "xlsx"]
