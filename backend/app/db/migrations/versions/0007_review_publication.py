"""独立人工审核和不可变发布；不升级任何历史模型草稿。"""

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "product_categories", sa.Column("active_publication_id", sa.String(36), nullable=True)
    )
    op.create_table(
        "claim_revisions",
        sa.Column("id", sa.String(length=36), nullable=False, primary_key=True),
        sa.Column(
            "claim_id", sa.String(length=80), sa.ForeignKey("capability_claims.id"), nullable=False
        ),
        sa.Column(
            "snapshot_id",
            sa.String(length=36),
            sa.ForeignKey("source_snapshots.id"),
            nullable=False,
        ),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("declaration_type", sa.String(length=32), nullable=False),
        sa.Column("decision", sa.String(length=32), nullable=False),
        sa.Column("content_json", sa.JSON(), nullable=False),
        sa.Column("assertions_json", sa.JSON(), nullable=False),
        sa.Column("gate_json", sa.JSON(), nullable=False),
        sa.Column("merged_into", sa.String(length=80), nullable=True),
        sa.Column("reviewer_id", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("claim_id", "number"),
    )
    op.create_index("ix_claim_revisions_claim_id", "claim_revisions", ["claim_id"])
    op.create_index("ix_claim_revisions_snapshot_id", "claim_revisions", ["snapshot_id"])
    op.create_table(
        "review_events",
        sa.Column("id", sa.String(length=36), nullable=False, primary_key=True),
        sa.Column(
            "snapshot_id",
            sa.String(length=36),
            sa.ForeignKey("source_snapshots.id"),
            nullable=False,
        ),
        sa.Column(
            "claim_id", sa.String(length=80), sa.ForeignKey("capability_claims.id"), nullable=True
        ),
        sa.Column("reviewer_id", sa.String(length=32), nullable=False),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("comment", sa.String(length=1000), nullable=False),
        sa.Column("details_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_review_events_snapshot_id", "review_events", ["snapshot_id"])
    op.create_table(
        "reviewed_relations",
        sa.Column("id", sa.String(length=36), nullable=False, primary_key=True),
        sa.Column(
            "snapshot_id",
            sa.String(length=36),
            sa.ForeignKey("source_snapshots.id"),
            nullable=False,
        ),
        sa.Column(
            "source_revision_id",
            sa.String(length=36),
            sa.ForeignKey("claim_revisions.id"),
            nullable=False,
        ),
        sa.Column(
            "target_revision_id",
            sa.String(length=36),
            sa.ForeignKey("claim_revisions.id"),
            nullable=False,
        ),
        sa.Column("relation_type", sa.String(length=32), nullable=False),
        sa.Column("decision", sa.String(length=32), nullable=False),
        sa.Column("details_json", sa.JSON(), nullable=False),
        sa.Column("reviewer_id", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_reviewed_relations_snapshot_id", "reviewed_relations", ["snapshot_id"])
    op.create_table(
        "knowledge_publications",
        sa.Column("id", sa.String(length=36), nullable=False, primary_key=True),
        sa.Column(
            "category_id",
            sa.String(length=36),
            sa.ForeignKey("product_categories.id"),
            nullable=False,
        ),
        sa.Column(
            "snapshot_id",
            sa.String(length=36),
            sa.ForeignKey("source_snapshots.id"),
            nullable=False,
        ),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("published_by", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_knowledge_publications_category_id", "knowledge_publications", ["category_id"]
    )
    op.create_table(
        "publication_switches",
        sa.Column("id", sa.String(length=36), nullable=False, primary_key=True),
        sa.Column(
            "category_id",
            sa.String(length=36),
            sa.ForeignKey("product_categories.id"),
            nullable=False,
        ),
        sa.Column(
            "publication_id",
            sa.String(length=36),
            sa.ForeignKey("knowledge_publications.id"),
            nullable=False,
        ),
        sa.Column("previous_publication_id", sa.String(length=36), nullable=True),
        sa.Column("action", sa.String(length=16), nullable=False),
        sa.Column("actor", sa.String(length=32), nullable=False),
        sa.Column("comment", sa.String(length=1000), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_publication_switches_category_id", "publication_switches", ["category_id"])


def downgrade():
    op.drop_table("publication_switches")
    op.drop_table("knowledge_publications")
    op.drop_table("reviewed_relations")
    op.drop_table("review_events")
    op.drop_table("claim_revisions")
    op.drop_column("product_categories", "active_publication_id")
