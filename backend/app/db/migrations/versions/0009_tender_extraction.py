"""P2.1 招标参数提取独立任务、参数、原子要求、区域与修正审计。"""

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "tender_extraction_jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("principal_id", sa.String(64), nullable=False),
        sa.Column(
            "category_id", sa.String(36), sa.ForeignKey("product_categories.id"), nullable=False
        ),
        sa.Column("input_type", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("original_filename", sa.String(240), nullable=True),
        sa.Column("original_file_hash", sa.String(64), nullable=True),
        sa.Column("original_storage_key", sa.String(100), nullable=True),
        sa.Column("original_text", sa.Text(), nullable=True),
        sa.Column("input_metadata", sa.JSON(), nullable=False),
        sa.Column("extracted_count", sa.Integer(), nullable=False),
        sa.Column("skipped_count", sa.Integer(), nullable=False),
        sa.Column("ambiguous_count", sa.Integer(), nullable=False),
        sa.Column("coverage_status", sa.String(16), nullable=False),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("metrics_json", sa.JSON(), nullable=False),
        sa.Column("coverage_json", sa.JSON(), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=True),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("dispatch_token", sa.String(36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("input_type IN ('text','docx','pdf','xlsx')"),
        sa.CheckConstraint(
            "status IN ('queued','running','succeeded','partial','failed','cancelled')"
        ),
        sa.CheckConstraint("coverage_status IN ('pending','complete','incomplete')"),
        sa.UniqueConstraint("principal_id", "idempotency_key", name="uq_tender_idempotency"),
    )
    op.create_index(
        "ix_tender_extraction_jobs_principal_id", "tender_extraction_jobs", ["principal_id"]
    )
    op.create_index(
        "ix_tender_extraction_jobs_category_id", "tender_extraction_jobs", ["category_id"]
    )
    op.create_index(
        "ix_tender_jobs_principal_status",
        "tender_extraction_jobs",
        ["principal_id", "status"],
    )
    op.create_table(
        "tender_parameters",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "job_id",
            sa.String(36),
            sa.ForeignKey("tender_extraction_jobs.id"),
            nullable=False,
        ),
        sa.Column("order_index", sa.Integer(), nullable=False),
        sa.Column("original_number", sa.String(64), nullable=True),
        sa.Column("parameter_name", sa.String(500), nullable=False),
        sa.Column("original_text", sa.Text(), nullable=False),
        sa.Column("normalized_text", sa.Text(), nullable=False),
        sa.Column("source_locator", sa.JSON(), nullable=False),
        sa.Column("extraction_method", sa.String(32), nullable=False),
        sa.Column("extraction_confidence", sa.String(16), nullable=False),
        sa.Column("review_required", sa.Boolean(), nullable=False),
        sa.Column("atomic_requirements", sa.JSON(), nullable=False),
        sa.Column("logic_tree", sa.JSON(), nullable=False),
        sa.Column("original_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("superseded_by", sa.String(36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status IN ('extracted','confirmed','corrected','superseded')"),
        sa.UniqueConstraint("job_id", "order_index", name="uq_tender_parameter_order"),
    )
    op.create_index("ix_tender_parameters_job_id", "tender_parameters", ["job_id"])
    op.create_index("ix_tender_parameters_job_status", "tender_parameters", ["job_id", "status"])
    op.create_table(
        "tender_atomic_requirements",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "job_id",
            sa.String(36),
            sa.ForeignKey("tender_extraction_jobs.id"),
            nullable=False,
        ),
        sa.Column(
            "parameter_id",
            sa.String(36),
            sa.ForeignKey("tender_parameters.id"),
            nullable=False,
        ),
        sa.Column("order_index", sa.Integer(), nullable=False),
        sa.Column("original_text", sa.Text(), nullable=False),
        sa.Column("requirement_type", sa.String(40), nullable=False),
        sa.Column("logical_relation", sa.String(16), nullable=False),
        sa.Column("logical_group_id", sa.String(36), nullable=True),
        sa.Column("is_mandatory", sa.Boolean(), nullable=False),
        sa.Column("threshold", sa.String(64), nullable=True),
        sa.Column("unit", sa.String(32), nullable=True),
        sa.Column("conditions", sa.JSON(), nullable=False),
        sa.Column("ambiguity", sa.String(64), nullable=True),
        sa.Column("source_span", sa.JSON(), nullable=False),
        sa.Column("parent_requirement_id", sa.String(36), nullable=True),
        sa.Column("depth", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("parameter_id", "order_index", name="uq_tender_requirement_order"),
    )
    op.create_index(
        "ix_tender_atomic_requirements_job_id", "tender_atomic_requirements", ["job_id"]
    )
    op.create_index(
        "ix_tender_atomic_requirements_parameter_id",
        "tender_atomic_requirements",
        ["parameter_id"],
    )
    op.create_index(
        "ix_tender_requirements_parameter",
        "tender_atomic_requirements",
        ["parameter_id"],
    )
    op.create_table(
        "tender_regions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "job_id",
            sa.String(36),
            sa.ForeignKey("tender_extraction_jobs.id"),
            nullable=False,
        ),
        sa.Column(
            "parameter_id",
            sa.String(36),
            sa.ForeignKey("tender_parameters.id"),
            nullable=True,
        ),
        sa.Column("disposition", sa.String(16), nullable=False),
        sa.Column("region_kind", sa.String(32), nullable=False),
        sa.Column("original_text", sa.Text(), nullable=False),
        sa.Column("source_locator", sa.JSON(), nullable=False),
        sa.Column("reason_code", sa.String(64), nullable=True),
        sa.Column("reason", sa.String(500), nullable=True),
        sa.Column("confidence", sa.String(16), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("disposition IN ('extracted','filtered','ambiguous','parse_gap')"),
    )
    op.create_index("ix_tender_regions_job_id", "tender_regions", ["job_id"])
    op.create_index("ix_tender_regions_parameter_id", "tender_regions", ["parameter_id"])
    op.create_index(
        "ix_tender_regions_job_disposition", "tender_regions", ["job_id", "disposition"]
    )
    op.create_table(
        "tender_corrections",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "job_id",
            sa.String(36),
            sa.ForeignKey("tender_extraction_jobs.id"),
            nullable=False,
        ),
        sa.Column("parameter_id", sa.String(36), nullable=True),
        sa.Column("actor", sa.String(64), nullable=False),
        sa.Column("action", sa.String(16), nullable=False),
        sa.Column("correlation_id", sa.String(36), nullable=True),
        sa.Column("before_json", sa.JSON(), nullable=False),
        sa.Column("after_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("action IN ('edit','split','merge','add','confirm')"),
    )
    op.create_index("ix_tender_corrections_job_id", "tender_corrections", ["job_id"])
    op.create_index("ix_tender_corrections_job", "tender_corrections", ["job_id"])


def downgrade():
    op.drop_index("ix_tender_corrections_job", table_name="tender_corrections")
    op.drop_index("ix_tender_corrections_job_id", table_name="tender_corrections")
    op.drop_table("tender_corrections")
    op.drop_index("ix_tender_regions_job_disposition", table_name="tender_regions")
    op.drop_index("ix_tender_regions_parameter_id", table_name="tender_regions")
    op.drop_index("ix_tender_regions_job_id", table_name="tender_regions")
    op.drop_table("tender_regions")
    op.drop_index("ix_tender_requirements_parameter", table_name="tender_atomic_requirements")
    op.drop_index(
        "ix_tender_atomic_requirements_parameter_id",
        table_name="tender_atomic_requirements",
    )
    op.drop_index("ix_tender_atomic_requirements_job_id", table_name="tender_atomic_requirements")
    op.drop_table("tender_atomic_requirements")
    op.drop_index("ix_tender_parameters_job_status", table_name="tender_parameters")
    op.drop_index("ix_tender_parameters_job_id", table_name="tender_parameters")
    op.drop_table("tender_parameters")
    op.drop_index("ix_tender_jobs_principal_status", table_name="tender_extraction_jobs")
    op.drop_index("ix_tender_extraction_jobs_category_id", table_name="tender_extraction_jobs")
    op.drop_index("ix_tender_extraction_jobs_principal_id", table_name="tender_extraction_jobs")
    op.drop_table("tender_extraction_jobs")
