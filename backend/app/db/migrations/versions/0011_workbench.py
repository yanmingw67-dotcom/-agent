"""P2.3 评估工作台：人工复核只追加记录与导出任务。"""

import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "evaluation_reviews",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("job_id", sa.String(36), sa.ForeignKey("evaluation_jobs.id"), nullable=False),
        sa.Column(
            "parameter_evaluation_id",
            sa.String(36),
            sa.ForeignKey("parameter_evaluations.id"),
            nullable=False,
        ),
        sa.Column(
            "parameter_id",
            sa.String(36),
            sa.ForeignKey("tender_parameters.id"),
            nullable=False,
        ),
        sa.Column("reviewer_id", sa.String(64), nullable=False),
        sa.Column("action", sa.String(16), nullable=False),
        sa.Column("review_status", sa.String(32), nullable=False),
        sa.Column("auto_verdict", sa.String(16), nullable=False),
        sa.Column("final_verdict", sa.String(16), nullable=True),
        sa.Column("recognized", sa.Boolean(), nullable=False),
        sa.Column("requires_reevaluation", sa.Boolean(), nullable=False),
        sa.Column("comment", sa.Text(), nullable=False),
        sa.Column("adjust_reason", sa.Text(), nullable=False),
        sa.Column("correlation_id", sa.String(36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("action IN ('accept','adjust','reject','reassess')"),
        sa.CheckConstraint(
            "review_status IN ('accepted','adjusted','rejected','reassessment_required')"
        ),
        sa.CheckConstraint("auto_verdict IN ('支持','不支持','无法确认')"),
        sa.CheckConstraint(
            "final_verdict IS NULL OR final_verdict IN ('支持','不支持','无法确认')"
        ),
    )
    op.create_index("ix_evaluation_reviews_job_id", "evaluation_reviews", ["job_id"])
    op.create_index(
        "ix_evaluation_reviews_parameter_evaluation_id",
        "evaluation_reviews",
        ["parameter_evaluation_id"],
    )
    op.create_index("ix_evaluation_reviews_parameter_id", "evaluation_reviews", ["parameter_id"])
    op.create_index("ix_evaluation_reviews_reviewer_id", "evaluation_reviews", ["reviewer_id"])
    op.create_index("ix_evaluation_reviews_job", "evaluation_reviews", ["job_id"])
    op.create_index(
        "ix_evaluation_reviews_parameter",
        "evaluation_reviews",
        ["parameter_evaluation_id"],
    )
    op.create_index("ix_evaluation_reviews_reviewer", "evaluation_reviews", ["reviewer_id"])
    op.create_table(
        "evaluation_export_jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("job_id", sa.String(36), sa.ForeignKey("evaluation_jobs.id"), nullable=False),
        sa.Column("principal_id", sa.String(64), nullable=False),
        sa.Column("format", sa.String(8), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("filename", sa.String(240), nullable=False),
        sa.Column("storage_key", sa.String(100), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("parameter_count", sa.Integer(), nullable=False),
        sa.Column("atomic_count", sa.Integer(), nullable=False),
        sa.Column("evidence_count", sa.Integer(), nullable=False),
        sa.Column("review_count", sa.Integer(), nullable=False),
        sa.Column("summary_json", sa.JSON(), nullable=False),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("download_token", sa.String(64), nullable=False),
        sa.Column("dispatch_token", sa.String(36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("format IN ('docx','xlsx')"),
        sa.CheckConstraint("status IN ('queued','running','succeeded','failed')"),
        sa.UniqueConstraint("storage_key", name="uq_evaluation_export_storage_key"),
    )
    op.create_index("ix_evaluation_export_jobs_job_id", "evaluation_export_jobs", ["job_id"])
    op.create_index(
        "ix_evaluation_export_jobs_principal_id",
        "evaluation_export_jobs",
        ["principal_id"],
    )
    op.create_index(
        "ix_evaluation_export_principal_status",
        "evaluation_export_jobs",
        ["principal_id", "status"],
    )
    op.create_index("ix_evaluation_export_job", "evaluation_export_jobs", ["job_id"])


def downgrade():
    op.drop_index("ix_evaluation_export_job", table_name="evaluation_export_jobs")
    op.drop_index("ix_evaluation_export_principal_status", table_name="evaluation_export_jobs")
    op.drop_index("ix_evaluation_export_jobs_principal_id", table_name="evaluation_export_jobs")
    op.drop_index("ix_evaluation_export_jobs_job_id", table_name="evaluation_export_jobs")
    op.drop_table("evaluation_export_jobs")
    op.drop_index("ix_evaluation_reviews_reviewer", table_name="evaluation_reviews")
    op.drop_index("ix_evaluation_reviews_parameter", table_name="evaluation_reviews")
    op.drop_index("ix_evaluation_reviews_job", table_name="evaluation_reviews")
    op.drop_index("ix_evaluation_reviews_reviewer_id", table_name="evaluation_reviews")
    op.drop_index("ix_evaluation_reviews_parameter_id", table_name="evaluation_reviews")
    op.drop_index("ix_evaluation_reviews_parameter_evaluation_id", table_name="evaluation_reviews")
    op.drop_index("ix_evaluation_reviews_job_id", table_name="evaluation_reviews")
    op.drop_table("evaluation_reviews")
