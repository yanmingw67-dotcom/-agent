"""人工审核修订及不可变发布清单；原模型草稿不修改。"""

from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models import Base, now, uid


class ClaimRevision(Base):
    __tablename__ = "claim_revisions"
    __table_args__ = (UniqueConstraint("claim_id", "number"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    claim_id: Mapped[str] = mapped_column(ForeignKey("capability_claims.id"), index=True)
    snapshot_id: Mapped[str] = mapped_column(ForeignKey("source_snapshots.id"), index=True)
    number: Mapped[int] = mapped_column(Integer)
    declaration_type: Mapped[str] = mapped_column(String(32))
    decision: Mapped[str] = mapped_column(String(32))
    content_json: Mapped[dict] = mapped_column(JSON)
    assertions_json: Mapped[list] = mapped_column(JSON)
    gate_json: Mapped[dict] = mapped_column(JSON)
    merged_into: Mapped[str | None] = mapped_column(String(80))
    availability_basis: Mapped[str | None] = mapped_column(String(32))
    product_scope_confirmed: Mapped[bool] = mapped_column(default=False, server_default="false")
    proof_kind: Mapped[str | None] = mapped_column(String(16))
    conflict_ids: Mapped[list] = mapped_column(JSON, default=list, server_default="'[]'")
    reviewer_id: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ReviewEvent(Base):
    __tablename__ = "review_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    snapshot_id: Mapped[str] = mapped_column(ForeignKey("source_snapshots.id"), index=True)
    claim_id: Mapped[str | None] = mapped_column(ForeignKey("capability_claims.id"))
    reviewer_id: Mapped[str] = mapped_column(String(32))
    action: Mapped[str] = mapped_column(String(32))
    comment: Mapped[str] = mapped_column(String(1000))
    details_json: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ReviewedRelation(Base):
    __tablename__ = "reviewed_relations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    snapshot_id: Mapped[str] = mapped_column(ForeignKey("source_snapshots.id"), index=True)
    source_revision_id: Mapped[str] = mapped_column(ForeignKey("claim_revisions.id"))
    target_revision_id: Mapped[str] = mapped_column(ForeignKey("claim_revisions.id"))
    relation_type: Mapped[str] = mapped_column(String(32))
    decision: Mapped[str] = mapped_column(String(32))
    details_json: Mapped[dict] = mapped_column(JSON)
    reviewer_id: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ConflictFinding(Base):
    """双源或同源能力冲突；阻断未解决断言的接受与发布，不自动合并。"""

    __tablename__ = "conflict_findings"
    __table_args__ = (
        UniqueConstraint(
            "snapshot_id",
            "source_claim_id",
            "target_claim_id",
            "conflict_type",
            name="uq_conflict_finding_pair",
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    category_id: Mapped[str] = mapped_column(ForeignKey("product_categories.id"), index=True)
    snapshot_id: Mapped[str] = mapped_column(ForeignKey("source_snapshots.id"), index=True)
    source_claim_id: Mapped[str] = mapped_column(ForeignKey("capability_claims.id"))
    target_claim_id: Mapped[str] = mapped_column(ForeignKey("capability_claims.id"))
    conflict_type: Mapped[str] = mapped_column(String(40))
    details_json: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(16), default="unresolved")
    resolution_json: Mapped[dict] = mapped_column(JSON, default=dict)
    resolved_by: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Publication(Base):
    __tablename__ = "knowledge_publications"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    category_id: Mapped[str] = mapped_column(ForeignKey("product_categories.id"), index=True)
    snapshot_id: Mapped[str] = mapped_column(ForeignKey("source_snapshots.id"))
    payload_json: Mapped[dict] = mapped_column(JSON)
    payload_hash: Mapped[str] = mapped_column(String(64))
    published_by: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class PublicationSwitch(Base):
    __tablename__ = "publication_switches"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    category_id: Mapped[str] = mapped_column(ForeignKey("product_categories.id"), index=True)
    publication_id: Mapped[str] = mapped_column(ForeignKey("knowledge_publications.id"))
    previous_publication_id: Mapped[str | None] = mapped_column(String(36))
    action: Mapped[str] = mapped_column(String(16))
    actor: Mapped[str] = mapped_column(String(32))
    comment: Mapped[str] = mapped_column(String(1000))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
