"""P1.6 双源：文档发现运行、冲突与来源元数据（向后兼容可选字段）。"""

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "source_snapshots", sa.Column("derived_from_snapshot_id", sa.String(36), nullable=True)
    )
    op.add_column("source_snapshots", sa.Column("derivation_type", sa.String(32), nullable=True))
    op.add_column(
        "documents",
        sa.Column("origin_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )
    op.add_column(
        "capability_claims",
        sa.Column("source_channel", sa.String(16), nullable=False, server_default="source"),
    )
    op.add_column(
        "capability_claims",
        sa.Column("origin_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )
    op.add_column("claim_revisions", sa.Column("availability_basis", sa.String(32), nullable=True))
    op.add_column(
        "claim_revisions",
        sa.Column(
            "product_scope_confirmed",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column("claim_revisions", sa.Column("proof_kind", sa.String(16), nullable=True))
    op.add_column(
        "claim_revisions",
        sa.Column("conflict_ids", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
    )
    op.create_table(
        "document_discovery_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "category_id",
            sa.String(36),
            sa.ForeignKey("product_categories.id"),
            nullable=False,
        ),
        sa.Column(
            "snapshot_id", sa.String(36), sa.ForeignKey("source_snapshots.id"), nullable=False
        ),
        sa.Column("job_id", sa.String(36), sa.ForeignKey("knowledge_jobs.id"), nullable=True),
        sa.Column("mode", sa.String(16), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("metrics_json", sa.JSON(), nullable=False),
        sa.Column("coverage_json", sa.JSON(), nullable=False),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_document_discovery_runs_snapshot_id", "document_discovery_runs", ["snapshot_id"]
    )
    op.create_index("ix_document_discovery_runs_job_id", "document_discovery_runs", ["job_id"])
    op.create_table(
        "conflict_findings",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "category_id",
            sa.String(36),
            sa.ForeignKey("product_categories.id"),
            nullable=False,
        ),
        sa.Column(
            "snapshot_id", sa.String(36), sa.ForeignKey("source_snapshots.id"), nullable=False
        ),
        sa.Column(
            "source_claim_id", sa.String(80), sa.ForeignKey("capability_claims.id"), nullable=False
        ),
        sa.Column(
            "target_claim_id", sa.String(80), sa.ForeignKey("capability_claims.id"), nullable=False
        ),
        sa.Column("conflict_type", sa.String(40), nullable=False),
        sa.Column("details_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("resolution_json", sa.JSON(), nullable=False),
        sa.Column("resolved_by", sa.String(32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "snapshot_id",
            "source_claim_id",
            "target_claim_id",
            "conflict_type",
            name="uq_conflict_finding_pair",
        ),
    )
    op.create_index("ix_conflict_findings_snapshot_id", "conflict_findings", ["snapshot_id"])
    op.create_index("ix_conflict_findings_category_id", "conflict_findings", ["category_id"])


def downgrade():
    op.drop_table("conflict_findings")
    op.drop_table("document_discovery_runs")
    op.drop_column("claim_revisions", "conflict_ids")
    op.drop_column("claim_revisions", "product_scope_confirmed")
    op.drop_column("claim_revisions", "proof_kind")
    op.drop_column("claim_revisions", "availability_basis")
    op.drop_column("capability_claims", "origin_json")
    op.drop_column("capability_claims", "source_channel")
    op.drop_column("documents", "origin_json")
    op.drop_column("source_snapshots", "derivation_type")
    op.drop_column("source_snapshots", "derived_from_snapshot_id")
