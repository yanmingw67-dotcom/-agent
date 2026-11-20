"""R01/R02：模型外发授权记录与真实资料来源确认。

两张表都是新增事实记录，不回填历史数据：
- 没有有效模型授权记录时，真实模型调用继续失败关闭；
- 没有管理员来源确认时，Gate 2 不能把任何资料认定为真实输入。
"""

import sqlalchemy as sa
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "model_authorizations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("data_classification", sa.String(32), nullable=False),
        sa.Column("model_name", sa.String(200), nullable=False),
        sa.Column("endpoint", sa.String(300), nullable=False),
        sa.Column("approval_reference", sa.String(200), nullable=False),
        sa.Column("approved_scope", sa.String(1000), nullable=False),
        sa.Column("approved_by", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_by", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status IN ('active','revoked','expired')"),
        sa.CheckConstraint("data_classification IN ('synthetic_public','enterprise')"),
    )
    op.create_index(
        "uq_model_authorization_active",
        "model_authorizations",
        ["data_classification", "model_name", "endpoint"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
        sqlite_where=sa.text("status = 'active'"),
    )
    op.create_table(
        "material_provenance",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "category_id",
            sa.String(36),
            sa.ForeignKey("product_categories.id"),
            nullable=False,
        ),
        sa.Column(
            "snapshot_id",
            sa.String(36),
            sa.ForeignKey("source_snapshots.id"),
            nullable=False,
        ),
        sa.Column("document_id", sa.String(36), sa.ForeignKey("documents.id"), nullable=True),
        sa.Column("material_type", sa.String(32), nullable=False),
        sa.Column("material_key", sa.String(80), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("data_classification", sa.String(32), nullable=False),
        sa.Column("declared_origin", sa.String(500), nullable=False),
        sa.Column("usage_scope", sa.String(1000), nullable=False),
        sa.Column("confirmation_reference", sa.String(200), nullable=False),
        sa.Column("confirmed_by", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_by", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("material_type IN ('source_code','technical_document')"),
        sa.CheckConstraint("status IN ('confirmed','revoked')"),
        sa.CheckConstraint("data_classification IN ('enterprise')"),
    )
    op.create_index("ix_material_provenance_category", "material_provenance", ["category_id"])
    op.create_index("ix_material_provenance_snapshot", "material_provenance", ["snapshot_id"])
    op.create_index(
        "uq_material_provenance_active",
        "material_provenance",
        ["category_id", "material_key"],
        unique=True,
        postgresql_where=sa.text("status = 'confirmed'"),
        sqlite_where=sa.text("status = 'confirmed'"),
    )


def downgrade():
    op.drop_index("uq_material_provenance_active", table_name="material_provenance")
    op.drop_index("ix_material_provenance_snapshot", table_name="material_provenance")
    op.drop_index("ix_material_provenance_category", table_name="material_provenance")
    op.drop_table("material_provenance")
    op.drop_index("uq_model_authorization_active", table_name="model_authorizations")
    op.drop_table("model_authorizations")
