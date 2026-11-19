"""P2.3 评估工作台：人工复核历史与导出任务。

自动评估结果保存在 P2.2 的 parameter_evaluations / atomic_evaluations /
evaluation_evidence 中。本模块只追加人工复核记录与导出任务，不更新、不覆盖
任何自动结论、原子判断或证据行。

复核记录不可变：每次提交新增一行；最新记录按 created_at、id 倒序确定。
导出任务固定引用 evaluation_jobs.id，生成文件前再次校验用户归属与发布快照。
"""

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models import Base, now, uid

REVIEW_ACTIONS = ("accept", "adjust", "reject", "reassess")
REVIEW_STATUSES = ("accepted", "adjusted", "rejected", "reassessment_required")
VERDICTS = ("支持", "不支持", "无法确认")
EXPORT_FORMATS = ("docx", "xlsx")
EXPORT_STATUSES = ("queued", "running", "succeeded", "failed")


class EvaluationReview(Base):
    """人工复核的只追加记录；自动结论仅作为不可变副本保存。"""

    __tablename__ = "evaluation_reviews"
    __table_args__ = (
        CheckConstraint("action IN ('accept','adjust','reject','reassess')"),
        CheckConstraint(
            "review_status IN ('accepted','adjusted','rejected','reassessment_required')"
        ),
        CheckConstraint("auto_verdict IN ('支持','不支持','无法确认')"),
        CheckConstraint("final_verdict IS NULL OR final_verdict IN ('支持','不支持','无法确认')"),
        Index("ix_evaluation_reviews_job", "job_id"),
        Index("ix_evaluation_reviews_parameter", "parameter_evaluation_id"),
        Index("ix_evaluation_reviews_reviewer", "reviewer_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    job_id: Mapped[str] = mapped_column(ForeignKey("evaluation_jobs.id"), index=True)
    parameter_evaluation_id: Mapped[str] = mapped_column(
        ForeignKey("parameter_evaluations.id"), index=True
    )
    parameter_id: Mapped[str] = mapped_column(ForeignKey("tender_parameters.id"), index=True)
    reviewer_id: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(16))
    review_status: Mapped[str] = mapped_column(String(32))
    auto_verdict: Mapped[str] = mapped_column(String(16))
    final_verdict: Mapped[str | None] = mapped_column(String(16))
    recognized: Mapped[bool] = mapped_column(Boolean, default=False)
    requires_reevaluation: Mapped[bool] = mapped_column(Boolean, default=False)
    comment: Mapped[str] = mapped_column(Text, default="")
    adjust_reason: Mapped[str] = mapped_column(Text, default="")
    correlation_id: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(default=now)


class EvaluationExportJob(Base):
    """导出任务；只有成功任务才产生可下载文件。"""

    __tablename__ = "evaluation_export_jobs"
    __table_args__ = (
        CheckConstraint("format IN ('docx','xlsx')"),
        CheckConstraint("status IN ('queued','running','succeeded','failed')"),
        UniqueConstraint("storage_key", name="uq_evaluation_export_storage_key"),
        Index("ix_evaluation_export_principal_status", "principal_id", "status"),
        Index("ix_evaluation_export_job", "job_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    job_id: Mapped[str] = mapped_column(ForeignKey("evaluation_jobs.id"), index=True)
    principal_id: Mapped[str] = mapped_column(String(64), index=True)
    format: Mapped[str] = mapped_column(String(8))
    status: Mapped[str] = mapped_column(String(16), default="queued")
    filename: Mapped[str] = mapped_column(String(240), default="")
    storage_key: Mapped[str | None] = mapped_column(String(100))
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    parameter_count: Mapped[int] = mapped_column(Integer, default=0)
    atomic_count: Mapped[int] = mapped_column(Integer, default=0)
    evidence_count: Mapped[int] = mapped_column(Integer, default=0)
    review_count: Mapped[int] = mapped_column(Integer, default=0)
    summary_json: Mapped[dict] = mapped_column(JSON, default=dict)
    error_code: Mapped[str | None] = mapped_column(String(64))
    download_token: Mapped[str] = mapped_column(String(64))
    # 旧导出任务这两列为空：凭证视为已过期、文件完整性摘要不可用，必须重新生成。
    download_expires_at: Mapped[datetime | None] = mapped_column()
    file_sha256: Mapped[str | None] = mapped_column(String(64))
    dispatch_token: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(default=now)
    started_at: Mapped[datetime | None] = mapped_column()
    finished_at: Mapped[datetime | None] = mapped_column()
