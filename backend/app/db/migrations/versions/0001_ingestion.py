"""P1.1 品类、不可变快照、可靠任务 outbox 和审计。"""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "product_categories",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("code", sa.String(64), nullable=False, unique=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("active_snapshot_id", sa.String(36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "source_snapshots",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "category_id", sa.String(36), sa.ForeignKey("product_categories.id"), nullable=False
        ),
        sa.Column("source_sha256", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("archive_key", sa.String(100), nullable=False, unique=True),
        sa.Column("manifest_key", sa.String(200), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status IN ('uploaded','validating','extracted','failed','published')"),
    )
    op.create_index("ix_source_snapshots_category_id", "source_snapshots", ["category_id"])
    if op.get_bind().dialect.name == "postgresql":
        op.create_foreign_key(
            "fk_active_snapshot",
            "product_categories",
            "source_snapshots",
            ["active_snapshot_id"],
            ["id"],
        )
    op.create_table(
        "knowledge_jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "snapshot_id", sa.String(36), sa.ForeignKey("source_snapshots.id"), nullable=False
        ),
        sa.Column(
            "category_id", sa.String(36), sa.ForeignKey("product_categories.id"), nullable=False
        ),
        sa.Column("job_type", sa.String(32), nullable=False),
        sa.Column("stage", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("error_code", sa.String(64)),
        sa.Column("metrics_json", sa.JSON(), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("dispatch_token", sa.String(36)),
        sa.Column("dispatched_at", sa.DateTime(timezone=True)),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('queued','running','succeeded','failed','partial','cancelled')"
        ),
    )
    op.create_index("ix_knowledge_jobs_snapshot_id", "knowledge_jobs", ["snapshot_id"])
    op.create_index(
        "uq_active_category_import",
        "knowledge_jobs",
        ["category_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued','running')"),
        sqlite_where=sa.text("status IN ('queued','running')"),
    )
    op.create_table(
        "audit_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("principal_id", sa.String(32), nullable=False),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("resource_id", sa.String(36), nullable=False),
        sa.Column("correlation_id", sa.String(36), nullable=False),
        sa.Column("details_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    if op.get_bind().dialect.name == "postgresql":
        op.execute("""
        CREATE FUNCTION guard_active_snapshot() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF NEW.active_snapshot_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM source_snapshots s WHERE s.id = NEW.active_snapshot_id
            AND s.category_id = NEW.id AND s.status = 'published'
          ) THEN RAISE EXCEPTION 'active snapshot must be published and category-scoped'; END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER category_snapshot_guard BEFORE INSERT OR UPDATE ON product_categories
        FOR EACH ROW EXECUTE FUNCTION guard_active_snapshot();
        CREATE FUNCTION guard_snapshot_identity() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF NEW.category_id <> OLD.category_id OR NEW.source_sha256 <> OLD.source_sha256
             OR NEW.archive_key <> OLD.archive_key THEN
            RAISE EXCEPTION 'snapshot identity is immutable';
          END IF;
          IF NEW.status <> 'published' AND EXISTS (
            SELECT 1 FROM product_categories WHERE active_snapshot_id = OLD.id
          ) THEN RAISE EXCEPTION 'active snapshot must remain published'; END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER snapshot_identity_guard BEFORE UPDATE ON source_snapshots
        FOR EACH ROW EXECUTE FUNCTION guard_snapshot_identity();
        """)


def downgrade():
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP TRIGGER category_snapshot_guard ON product_categories")
        op.execute("DROP FUNCTION guard_active_snapshot()")
        op.execute("DROP TRIGGER snapshot_identity_guard ON source_snapshots")
        op.execute("DROP FUNCTION guard_snapshot_identity()")
        op.drop_constraint("fk_active_snapshot", "product_categories", type_="foreignkey")
    op.drop_table("audit_events")
    op.drop_table("knowledge_jobs")
    op.drop_table("source_snapshots")
    op.drop_table("product_categories")
