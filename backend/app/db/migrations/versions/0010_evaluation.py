"""P2.2 参数符合性评估：固定发布版本、参数结论、原子判断与证据追溯。"""

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "evaluation_jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("principal_id", sa.String(64), nullable=False),
        sa.Column(
            "category_id", sa.String(36), sa.ForeignKey("product_categories.id"), nullable=False
        ),
        sa.Column(
            "extraction_job_id",
            sa.String(36),
            sa.ForeignKey("tender_extraction_jobs.id"),
            nullable=False,
        ),
        sa.Column(
            "published_snapshot_id",
            sa.String(36),
            sa.ForeignKey("source_snapshots.id"),
            nullable=False,
        ),
        sa.Column(
            "publication_id",
            sa.String(36),
            sa.ForeignKey("knowledge_publications.id"),
            nullable=False,
        ),
        sa.Column("publication_hash", sa.String(64), nullable=False),
        sa.Column("parameter_snapshot", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("parameter_count", sa.Integer(), nullable=False),
        sa.Column("atomic_count", sa.Integer(), nullable=False),
        sa.Column("supported_count", sa.Integer(), nullable=False),
        sa.Column("unsupported_count", sa.Integer(), nullable=False),
        sa.Column("unknown_count", sa.Integer(), nullable=False),
        sa.Column("gap_count", sa.Integer(), nullable=False),
        sa.Column("metrics_json", sa.JSON(), nullable=False),
        sa.Column("checkpoint_json", sa.JSON(), nullable=False),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("idempotency_key", sa.String(128), nullable=True),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("dispatch_token", sa.String(36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('queued','running','succeeded','partial','failed','cancelled')"
        ),
        sa.UniqueConstraint("principal_id", "idempotency_key", name="uq_evaluation_idempotency"),
    )
    op.create_index("ix_evaluation_jobs_principal_id", "evaluation_jobs", ["principal_id"])
    op.create_index("ix_evaluation_jobs_category_id", "evaluation_jobs", ["category_id"])
    op.create_index(
        "ix_evaluation_jobs_extraction_job_id", "evaluation_jobs", ["extraction_job_id"]
    )
    op.create_index(
        "ix_evaluation_jobs_published_snapshot_id", "evaluation_jobs", ["published_snapshot_id"]
    )
    op.create_index("ix_evaluation_jobs_publication_id", "evaluation_jobs", ["publication_id"])
    op.create_index(
        "ix_evaluation_jobs_principal_status", "evaluation_jobs", ["principal_id", "status"]
    )
    op.create_table(
        "parameter_evaluations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("job_id", sa.String(36), sa.ForeignKey("evaluation_jobs.id"), nullable=False),
        sa.Column(
            "parameter_id", sa.String(36), sa.ForeignKey("tender_parameters.id"), nullable=False
        ),
        sa.Column("parameter_revision", sa.Integer(), nullable=False),
        sa.Column("parameter_name", sa.String(500), nullable=False),
        sa.Column("original_text", sa.Text(), nullable=False),
        sa.Column("verdict", sa.String(16), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("atomic_count", sa.Integer(), nullable=False),
        sa.Column("supported_count", sa.Integer(), nullable=False),
        sa.Column("unsupported_count", sa.Integer(), nullable=False),
        sa.Column("unknown_count", sa.Integer(), nullable=False),
        sa.Column("evidence_ids", sa.JSON(), nullable=False),
        sa.Column("logic_json", sa.JSON(), nullable=False),
        sa.Column("details_json", sa.JSON(), nullable=False),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("verdict IN ('支持','不支持','无法确认')"),
        sa.UniqueConstraint("job_id", "parameter_id", name="uq_parameter_evaluation"),
    )
    op.create_index("ix_parameter_evaluations_job_id", "parameter_evaluations", ["job_id"])
    op.create_index(
        "ix_parameter_evaluations_parameter_id", "parameter_evaluations", ["parameter_id"]
    )
    op.create_index("ix_parameter_evaluations_job", "parameter_evaluations", ["job_id"])
    op.create_table(
        "atomic_evaluations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("job_id", sa.String(36), sa.ForeignKey("evaluation_jobs.id"), nullable=False),
        sa.Column(
            "parameter_evaluation_id",
            sa.String(36),
            sa.ForeignKey("parameter_evaluations.id"),
            nullable=False,
        ),
        sa.Column(
            "requirement_id",
            sa.String(36),
            sa.ForeignKey("tender_atomic_requirements.id"),
            nullable=False,
        ),
        sa.Column("order_index", sa.Integer(), nullable=False),
        sa.Column("requirement_text", sa.Text(), nullable=False),
        sa.Column("necessary", sa.Boolean(), nullable=False),
        sa.Column("verdict", sa.String(16), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("evidence_ids", sa.JSON(), nullable=False),
        sa.Column("assessment_method", sa.String(24), nullable=False),
        sa.Column("details_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("verdict IN ('支持','不支持','无法确认')"),
        sa.UniqueConstraint(
            "parameter_evaluation_id", "requirement_id", name="uq_atomic_evaluation"
        ),
    )
    op.create_index("ix_atomic_evaluations_job_id", "atomic_evaluations", ["job_id"])
    op.create_index(
        "ix_atomic_evaluations_parameter_evaluation_id",
        "atomic_evaluations",
        ["parameter_evaluation_id"],
    )
    op.create_index(
        "ix_atomic_evaluations_requirement_id", "atomic_evaluations", ["requirement_id"]
    )
    op.create_index(
        "ix_atomic_evaluations_parameter", "atomic_evaluations", ["parameter_evaluation_id"]
    )
    op.create_table(
        "evaluation_evidence",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("job_id", sa.String(36), sa.ForeignKey("evaluation_jobs.id"), nullable=False),
        sa.Column(
            "parameter_evaluation_id",
            sa.String(36),
            sa.ForeignKey("parameter_evaluations.id"),
            nullable=False,
        ),
        sa.Column(
            "atomic_evaluation_id",
            sa.String(36),
            sa.ForeignKey("atomic_evaluations.id"),
            nullable=True,
        ),
        sa.Column("evidence_id", sa.String(80), nullable=False),
        sa.Column("capability_id", sa.String(80), nullable=False),
        sa.Column("revision_id", sa.String(80), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("source_ref", sa.JSON(), nullable=False),
        sa.Column("locator", sa.JSON(), nullable=False),
        sa.Column("source_hash", sa.String(64), nullable=False),
        sa.Column("quote_hash", sa.String(64), nullable=False),
        sa.Column("quote", sa.Text(), nullable=False),
        sa.Column("verification_state", sa.String(32), nullable=False),
        sa.Column("verification_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "verification_state IN ('verified_support','verified_contradiction','verified_context')"
        ),
    )
    op.create_index("ix_evaluation_evidence_job_id", "evaluation_evidence", ["job_id"])
    op.create_index(
        "ix_evaluation_evidence_parameter_evaluation_id",
        "evaluation_evidence",
        ["parameter_evaluation_id"],
    )
    op.create_index(
        "ix_evaluation_evidence_atomic_evaluation_id",
        "evaluation_evidence",
        ["atomic_evaluation_id"],
    )
    op.create_index("ix_evaluation_evidence_job", "evaluation_evidence", ["job_id"])
    op.create_index(
        "ix_evaluation_evidence_atomic", "evaluation_evidence", ["atomic_evaluation_id"]
    )


def downgrade():
    op.drop_index("ix_evaluation_evidence_atomic", table_name="evaluation_evidence")
    op.drop_index("ix_evaluation_evidence_job", table_name="evaluation_evidence")
    op.drop_index("ix_evaluation_evidence_atomic_evaluation_id", table_name="evaluation_evidence")
    op.drop_index(
        "ix_evaluation_evidence_parameter_evaluation_id", table_name="evaluation_evidence"
    )
    op.drop_index("ix_evaluation_evidence_job_id", table_name="evaluation_evidence")
    op.drop_table("evaluation_evidence")
    op.drop_index("ix_atomic_evaluations_parameter", table_name="atomic_evaluations")
    op.drop_index("ix_atomic_evaluations_requirement_id", table_name="atomic_evaluations")
    op.drop_index("ix_atomic_evaluations_parameter_evaluation_id", table_name="atomic_evaluations")
    op.drop_index("ix_atomic_evaluations_job_id", table_name="atomic_evaluations")
    op.drop_table("atomic_evaluations")
    op.drop_index("ix_parameter_evaluations_job", table_name="parameter_evaluations")
    op.drop_index("ix_parameter_evaluations_parameter_id", table_name="parameter_evaluations")
    op.drop_index("ix_parameter_evaluations_job_id", table_name="parameter_evaluations")
    op.drop_table("parameter_evaluations")
    op.drop_index("ix_evaluation_jobs_principal_status", table_name="evaluation_jobs")
    op.drop_index("ix_evaluation_jobs_publication_id", table_name="evaluation_jobs")
    op.drop_index("ix_evaluation_jobs_published_snapshot_id", table_name="evaluation_jobs")
    op.drop_index("ix_evaluation_jobs_extraction_job_id", table_name="evaluation_jobs")
    op.drop_index("ix_evaluation_jobs_category_id", table_name="evaluation_jobs")
    op.drop_index("ix_evaluation_jobs_principal_id", table_name="evaluation_jobs")
    op.drop_table("evaluation_jobs")
