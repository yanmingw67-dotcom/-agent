"""R01/R02 治理记录：模型外发授权与资料来源确认。

这些记录是服务端门禁的事实来源，不替代环境变量开关：
- 模型授权必须同时满足运行时开关、当前模型/端点匹配和有效授权记录；
- 资料来源必须由管理员逐项确认，Gate 2 再核对品类与快照派生链。
"""

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models import Base, now, uid


class ModelAuthorization(Base):
    """企业模型外发授权记录；只保存端点来源，不保存密钥。"""

    __tablename__ = "model_authorizations"
    __table_args__ = (
        CheckConstraint("status IN ('active','revoked','expired')"),
        CheckConstraint("data_classification IN ('synthetic_public','enterprise')"),
        Index(
            "uq_model_authorization_active",
            "data_classification",
            "model_name",
            "endpoint",
            unique=True,
            postgresql_where=text("status = 'active'"),
            sqlite_where=text("status = 'active'"),
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    data_classification: Mapped[str] = mapped_column(String(32))
    model_name: Mapped[str] = mapped_column(String(200))
    endpoint: Mapped[str] = mapped_column(String(300))
    approval_reference: Mapped[str] = mapped_column(String(200))
    approved_scope: Mapped[str] = mapped_column(String(1000))
    approved_by: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="active")
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_by: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class MaterialProvenance(Base):
    """管理员确认的真实资料来源、适用范围与只读快照指纹。"""

    __tablename__ = "material_provenance"
    __table_args__ = (
        CheckConstraint("material_type IN ('source_code','technical_document')"),
        CheckConstraint("status IN ('confirmed','revoked')"),
        CheckConstraint("data_classification IN ('enterprise',)"),
        Index(
            "uq_material_provenance_active",
            "category_id",
            "material_key",
            unique=True,
            postgresql_where=text("status = 'confirmed'"),
            sqlite_where=text("status = 'confirmed'"),
        ),
        Index("ix_material_provenance_category", "category_id"),
        Index("ix_material_provenance_snapshot", "snapshot_id"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    category_id: Mapped[str] = mapped_column(ForeignKey("product_categories.id"))
    snapshot_id: Mapped[str] = mapped_column(ForeignKey("source_snapshots.id"))
    document_id: Mapped[str | None] = mapped_column(ForeignKey("documents.id"))
    material_type: Mapped[str] = mapped_column(String(32))
    material_key: Mapped[str] = mapped_column(String(80))
    content_sha256: Mapped[str] = mapped_column(String(64))
    data_classification: Mapped[str] = mapped_column(String(32))
    declared_origin: Mapped[str] = mapped_column(String(500))
    usage_scope: Mapped[str] = mapped_column(String(1000))
    confirmation_reference: Mapped[str] = mapped_column(String(200))
    confirmed_by: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="confirmed")
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_by: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
