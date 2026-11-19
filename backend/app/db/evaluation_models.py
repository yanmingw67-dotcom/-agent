"""P2.2 参数技术符合性评估：任务、参数结论、原子判断与证据追溯。

招标参数原文冻结在 evaluation_jobs.parameter_snapshot_json 中，评估不再读取
可变参数行；发布知识按 job 固定的 publication_id 读取，不随当前生效指针漂移。
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

VERDICTS = ("支持", "不支持", "无法确认")


class EvaluationJob(Base):
    __tablename__ = "evaluation_jobs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued','running','succeeded','partial','failed','cancelled')"
        ),
        UniqueConstraint("principal_id", "idempotency_key", name="uq_evaluation_idempotency"),
        Index("ix_evaluation_jobs_principal_status", "principal_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    principal_id: Mapped[str] = mapped_column(String(64), index=True)
    category_id: Mapped[str] = mapped_column(ForeignKey("product_categories.id"), index=True)
    extraction_job_id: Mapped[str] = mapped_column(
        ForeignKey("tender_extraction_jobs.id"), index=True
    )
    published_snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("source_snapshots.id"), index=True
    )
    publication_id: Mapped[str] = mapped_column(ForeignKey("knowledge_publications.id"), index=True)
    publication_hash: Mapped[str] = mapped_column(String(64))
    parameter_snapshot: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(16), default="queued")
    parameter_count: Mapped[int] = mapped_column(Integer, default=0)
    atomic_count: Mapped[int] = mapped_column(Integer, default=0)
    supported_count: Mapped[int] = mapped_column(Integer, default=0)
    unsupported_count: Mapped[int] = mapped_column(Integer, default=0)
    unknown_count: Mapped[int] = mapped_column(Integer, default=0)
    gap_count: Mapped[int] = mapped_column(Integer, default=0)
    metrics_json: Mapped[dict] = mapped_column(JSON, default=dict)
    checkpoint_json: Mapped[dict] = mapped_column(JSON, default=dict)
    error_code: Mapped[str | None] = mapped_column(String(64))
    idempotency_key: Mapped[str | None] = mapped_column(String(128))
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    dispatch_token: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(default=now)
    started_at: Mapped[datetime | None] = mapped_column()
    finished_at: Mapped[datetime | None] = mapped_column()


class ParameterEvaluation(Base):
    __tablename__ = "parameter_evaluations"
    __table_args__ = (
        CheckConstraint("verdict IN ('支持','不支持','无法确认')"),
        UniqueConstraint("job_id", "parameter_id", name="uq_parameter_evaluation"),
        Index("ix_parameter_evaluations_job", "job_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    job_id: Mapped[str] = mapped_column(ForeignKey("evaluation_jobs.id"), index=True)
    parameter_id: Mapped[str] = mapped_column(ForeignKey("tender_parameters.id"), index=True)
    parameter_revision: Mapped[int] = mapped_column(Integer)
    parameter_name: Mapped[str] = mapped_column(String(500))
    original_text: Mapped[str] = mapped_column(Text)
    verdict: Mapped[str] = mapped_column(String(16))
    rationale: Mapped[str] = mapped_column(Text)
    atomic_count: Mapped[int] = mapped_column(Integer, default=0)
    supported_count: Mapped[int] = mapped_column(Integer, default=0)
    unsupported_count: Mapped[int] = mapped_column(Integer, default=0)
    unknown_count: Mapped[int] = mapped_column(Integer, default=0)
    evidence_ids: Mapped[list] = mapped_column(JSON, default=list)
    logic_json: Mapped[dict] = mapped_column(JSON, default=dict)
    details_json: Mapped[dict] = mapped_column(JSON, default=dict)
    error_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(default=now)
    updated_at: Mapped[datetime] = mapped_column(default=now, onupdate=now)


class AtomicEvaluation(Base):
    __tablename__ = "atomic_evaluations"
    __table_args__ = (
        CheckConstraint("verdict IN ('支持','不支持','无法确认')"),
        UniqueConstraint("parameter_evaluation_id", "requirement_id", name="uq_atomic_evaluation"),
        Index("ix_atomic_evaluations_parameter", "parameter_evaluation_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    job_id: Mapped[str] = mapped_column(ForeignKey("evaluation_jobs.id"), index=True)
    parameter_evaluation_id: Mapped[str] = mapped_column(
        ForeignKey("parameter_evaluations.id"), index=True
    )
    requirement_id: Mapped[str] = mapped_column(
        ForeignKey("tender_atomic_requirements.id"), index=True
    )
    order_index: Mapped[int] = mapped_column(Integer)
    requirement_text: Mapped[str] = mapped_column(Text)
    necessary: Mapped[bool] = mapped_column(Boolean, default=True)
    verdict: Mapped[str] = mapped_column(String(16))
    reason: Mapped[str] = mapped_column(Text)
    evidence_ids: Mapped[list] = mapped_column(JSON, default=list)
    assessment_method: Mapped[str] = mapped_column(String(24), default="deterministic")
    details_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(default=now)


class EvaluationEvidence(Base):
    __tablename__ = "evaluation_evidence"
    __table_args__ = (
        CheckConstraint(
            "verification_state IN ('verified_support','verified_contradiction','verified_context')"
        ),
        Index("ix_evaluation_evidence_job", "job_id"),
        Index("ix_evaluation_evidence_atomic", "atomic_evaluation_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    job_id: Mapped[str] = mapped_column(ForeignKey("evaluation_jobs.id"), index=True)
    parameter_evaluation_id: Mapped[str] = mapped_column(
        ForeignKey("parameter_evaluations.id"), index=True
    )
    atomic_evaluation_id: Mapped[str | None] = mapped_column(
        ForeignKey("atomic_evaluations.id"), index=True
    )
    evidence_id: Mapped[str] = mapped_column(String(80))
    capability_id: Mapped[str] = mapped_column(String(80))
    revision_id: Mapped[str] = mapped_column(String(80))
    kind: Mapped[str] = mapped_column(String(16))
    source_ref: Mapped[dict] = mapped_column(JSON, default=dict)
    locator: Mapped[dict] = mapped_column(JSON, default=dict)
    source_hash: Mapped[str] = mapped_column(String(64))
    quote_hash: Mapped[str] = mapped_column(String(64))
    quote: Mapped[str] = mapped_column(Text)
    verification_state: Mapped[str] = mapped_column(String(32))
    verification_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(default=now)
