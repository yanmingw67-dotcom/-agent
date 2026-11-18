"""P1.3 暂存调查实体；不存在批准或发布写入口。"""

from datetime import datetime

from sqlalchemy import JSON, CheckConstraint, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models import Base, now, uid


class Investigation(Base):
    __tablename__ = "module_investigations"
    __table_args__ = (UniqueConstraint("job_id", "module_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    job_id: Mapped[str] = mapped_column(ForeignKey("knowledge_jobs.id"), index=True)
    snapshot_id: Mapped[str] = mapped_column(ForeignKey("source_snapshots.id"), index=True)
    module_id: Mapped[str] = mapped_column(ForeignKey("modules.id"))
    status: Mapped[str] = mapped_column(String(32), default="pending")
    evidence_ids: Mapped[list] = mapped_column(JSON, default=list)
    output_json: Mapped[dict] = mapped_column(JSON, default=dict)
    metrics_json: Mapped[dict] = mapped_column(JSON, default=dict)
    error_code: Mapped[str | None] = mapped_column(String(64))
    attempt: Mapped[int] = mapped_column(default=0)


class Evidence(Base):
    __tablename__ = "evidence"
    __table_args__ = (CheckConstraint("verification_state = 'source_verified'"),)
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    snapshot_id: Mapped[str] = mapped_column(ForeignKey("source_snapshots.id"), index=True)
    kind: Mapped[str] = mapped_column(String(16))
    source_ref: Mapped[dict] = mapped_column(JSON)
    locator: Mapped[dict] = mapped_column(JSON)
    source_hash: Mapped[str] = mapped_column(String(64))
    quote_hash: Mapped[str] = mapped_column(String(64))
    verification_state: Mapped[str] = mapped_column(String(32), default="source_verified")
    warnings_json: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(default=now)


class CapabilityClaim(Base):
    __tablename__ = "capability_claims"
    __table_args__ = (
        UniqueConstraint("investigation_id", "local_key"),
        CheckConstraint("status IN ('candidate', 'evidence_found')"),
    )
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    snapshot_id: Mapped[str] = mapped_column(ForeignKey("source_snapshots.id"), index=True)
    investigation_id: Mapped[str] = mapped_column(
        ForeignKey("module_investigations.id"), index=True
    )
    local_key: Mapped[str] = mapped_column(String(80))
    name: Mapped[str] = mapped_column(String(1000))
    status: Mapped[str] = mapped_column(String(32))
    draft_json: Mapped[dict] = mapped_column(JSON)
    source_channel: Mapped[str] = mapped_column(
        String(16), default="source", server_default="source"
    )
    origin_json: Mapped[dict] = mapped_column(JSON, default=dict, server_default="'{}'")


class DocumentDiscoveryRun(Base):
    """文档独立发现运行；记录预算、覆盖缺口与检查点，不作批准或发布。"""

    __tablename__ = "document_discovery_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    category_id: Mapped[str] = mapped_column(ForeignKey("product_categories.id"), index=True)
    snapshot_id: Mapped[str] = mapped_column(ForeignKey("source_snapshots.id"), index=True)
    job_id: Mapped[str | None] = mapped_column(ForeignKey("knowledge_jobs.id"), index=True)
    mode: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(32), default="pending")
    metrics_json: Mapped[dict] = mapped_column(JSON, default=dict)
    coverage_json: Mapped[dict] = mapped_column(JSON, default=dict)
    error_code: Mapped[str | None] = mapped_column(String(64))
    started_at: Mapped[datetime | None] = mapped_column()
    finished_at: Mapped[datetime | None] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(default=now)


class ClaimEvidenceLink(Base):
    __tablename__ = "claim_evidence_links"
    claim_id: Mapped[str] = mapped_column(ForeignKey("capability_claims.id"), primary_key=True)
    evidence_id: Mapped[str] = mapped_column(ForeignKey("evidence.id"), primary_key=True)
    support_type: Mapped[str] = mapped_column(String(40), default="candidate_support")
    supported_statement: Mapped[str] = mapped_column(Text)


class CapabilityRelation(Base):
    __tablename__ = "capability_relations"
    __table_args__ = (CheckConstraint("status = 'hypothesis'"),)
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    snapshot_id: Mapped[str] = mapped_column(ForeignKey("source_snapshots.id"), index=True)
    investigation_id: Mapped[str] = mapped_column(ForeignKey("module_investigations.id"))
    source_claim_id: Mapped[str] = mapped_column(ForeignKey("capability_claims.id"))
    target_claim_id: Mapped[str] = mapped_column(ForeignKey("capability_claims.id"))
    relation_type: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32), default="hypothesis")
    details_json: Mapped[dict] = mapped_column(JSON)


class InvestigationTrace(Base):
    __tablename__ = "investigation_traces"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    investigation_id: Mapped[str] = mapped_column(
        ForeignKey("module_investigations.id"), index=True
    )
    attempt: Mapped[int] = mapped_column()
    sequence: Mapped[int] = mapped_column()
    kind: Mapped[str] = mapped_column(String(32))
    details_json: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(default=now)
    __table_args__ = (UniqueConstraint("investigation_id", "attempt", "sequence"),)
