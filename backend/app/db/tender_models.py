"""P2.1 招标参数提取：独立于 Agent A 产品知识库的任务与结果模型。

普通用户的招标文件只写入独立表与 tender 存储根，不产生 CapabilityClaim、
不进入审核发布，也不参与当前生效知识库检索。
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


class TenderExtractionJob(Base):
    __tablename__ = "tender_extraction_jobs"
    __table_args__ = (
        CheckConstraint("input_type IN ('text','docx','pdf','xlsx')"),
        CheckConstraint(
            "status IN ('queued','running','succeeded','partial','failed','cancelled')"
        ),
        CheckConstraint("coverage_status IN ('pending','complete','incomplete')"),
        UniqueConstraint("principal_id", "idempotency_key", name="uq_tender_idempotency"),
        Index("ix_tender_jobs_principal_status", "principal_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    principal_id: Mapped[str] = mapped_column(String(64), index=True)
    category_id: Mapped[str] = mapped_column(ForeignKey("product_categories.id"), index=True)
    input_type: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), default="queued")
    original_filename: Mapped[str | None] = mapped_column(String(240))
    original_file_hash: Mapped[str | None] = mapped_column(String(64))
    original_storage_key: Mapped[str | None] = mapped_column(String(100))
    original_text: Mapped[str | None] = mapped_column(Text)
    input_metadata: Mapped[dict] = mapped_column(JSON, default=dict)
    extracted_count: Mapped[int] = mapped_column(Integer, default=0)
    skipped_count: Mapped[int] = mapped_column(Integer, default=0)
    ambiguous_count: Mapped[int] = mapped_column(Integer, default=0)
    coverage_status: Mapped[str] = mapped_column(String(16), default="pending")
    error_code: Mapped[str | None] = mapped_column(String(64))
    metrics_json: Mapped[dict] = mapped_column(JSON, default=dict)
    coverage_json: Mapped[dict] = mapped_column(JSON, default=dict)
    idempotency_key: Mapped[str | None] = mapped_column(String(128))
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    dispatch_token: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(default=now)
    started_at: Mapped[datetime | None] = mapped_column()
    finished_at: Mapped[datetime | None] = mapped_column()


class TenderParameter(Base):
    __tablename__ = "tender_parameters"
    __table_args__ = (
        CheckConstraint("status IN ('extracted','confirmed','corrected','superseded')"),
        UniqueConstraint("job_id", "order_index", name="uq_tender_parameter_order"),
        Index("ix_tender_parameters_job_status", "job_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    job_id: Mapped[str] = mapped_column(ForeignKey("tender_extraction_jobs.id"), index=True)
    order_index: Mapped[int] = mapped_column(Integer)
    original_number: Mapped[str | None] = mapped_column(String(64))
    parameter_name: Mapped[str] = mapped_column(String(500))
    original_text: Mapped[str] = mapped_column(Text)
    normalized_text: Mapped[str] = mapped_column(Text)
    source_locator: Mapped[dict] = mapped_column(JSON)
    extraction_method: Mapped[str] = mapped_column(String(32), default="structural")
    extraction_confidence: Mapped[str] = mapped_column(String(16), default="high")
    review_required: Mapped[bool] = mapped_column(Boolean, default=False)
    atomic_requirements: Mapped[list] = mapped_column(JSON, default=list)
    logic_tree: Mapped[dict] = mapped_column(JSON, default=dict)
    original_json: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="extracted")
    revision: Mapped[int] = mapped_column(Integer, default=1)
    superseded_by: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(default=now)
    updated_at: Mapped[datetime] = mapped_column(default=now, onupdate=now)


class TenderAtomicRequirement(Base):
    __tablename__ = "tender_atomic_requirements"
    __table_args__ = (
        UniqueConstraint("parameter_id", "order_index", name="uq_tender_requirement_order"),
        Index("ix_tender_requirements_parameter", "parameter_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    job_id: Mapped[str] = mapped_column(ForeignKey("tender_extraction_jobs.id"), index=True)
    parameter_id: Mapped[str] = mapped_column(ForeignKey("tender_parameters.id"), index=True)
    order_index: Mapped[int] = mapped_column(Integer)
    original_text: Mapped[str] = mapped_column(Text)
    requirement_type: Mapped[str] = mapped_column(String(40), default="capability")
    logical_relation: Mapped[str] = mapped_column(String(16), default="AND")
    logical_group_id: Mapped[str | None] = mapped_column(String(36))
    is_mandatory: Mapped[bool] = mapped_column(Boolean, default=True)
    threshold: Mapped[str | None] = mapped_column(String(64))
    unit: Mapped[str | None] = mapped_column(String(32))
    conditions: Mapped[list] = mapped_column(JSON, default=list)
    ambiguity: Mapped[str | None] = mapped_column(String(64))
    source_span: Mapped[dict] = mapped_column(JSON, default=dict)
    parent_requirement_id: Mapped[str | None] = mapped_column(String(36))
    depth: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(default=now)


class TenderRegion(Base):
    """所有解析区域的处置记录；过滤、疑似和缺口均不可无痕丢弃。"""

    __tablename__ = "tender_regions"
    __table_args__ = (
        CheckConstraint("disposition IN ('extracted','filtered','ambiguous','parse_gap')"),
        Index("ix_tender_regions_job_disposition", "job_id", "disposition"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    job_id: Mapped[str] = mapped_column(ForeignKey("tender_extraction_jobs.id"), index=True)
    parameter_id: Mapped[str | None] = mapped_column(ForeignKey("tender_parameters.id"), index=True)
    disposition: Mapped[str] = mapped_column(String(16))
    region_kind: Mapped[str] = mapped_column(String(32))
    original_text: Mapped[str] = mapped_column(Text, default="")
    source_locator: Mapped[dict] = mapped_column(JSON, default=dict)
    reason_code: Mapped[str | None] = mapped_column(String(64))
    reason: Mapped[str | None] = mapped_column(String(500))
    confidence: Mapped[str | None] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(default=now)


class TenderCorrection(Base):
    """用户修正审计；before_json 保留服务端最初提取结果。"""

    __tablename__ = "tender_corrections"
    __table_args__ = (
        CheckConstraint("action IN ('edit','split','merge','add','confirm')"),
        Index("ix_tender_corrections_job", "job_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    job_id: Mapped[str] = mapped_column(ForeignKey("tender_extraction_jobs.id"), index=True)
    parameter_id: Mapped[str | None] = mapped_column(String(36))
    actor: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(16))
    correlation_id: Mapped[str | None] = mapped_column(String(36))
    before_json: Mapped[dict] = mapped_column(JSON, default=dict)
    after_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(default=now)
