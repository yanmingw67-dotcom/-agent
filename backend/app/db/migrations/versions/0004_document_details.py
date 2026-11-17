"""独立文档页状态与Sheet元数据；修正旧源码索引状态。"""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "documents",
        sa.Column("parse_details", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )
    # 旧文档任务可能覆盖源码状态；以最近一次真正source_index任务恢复，不改发布指针。
    op.execute("""
        UPDATE source_snapshots SET index_status = COALESCE((
            SELECT CASE
                WHEN j.status = 'queued' THEN 'queued'
                WHEN j.status = 'running' THEN 'indexing'
                WHEN j.status IN ('succeeded', 'partial') AND j.stage = 'indexed' THEN 'indexed'
                ELSE 'failed'
            END FROM knowledge_jobs j
            WHERE j.snapshot_id = source_snapshots.id AND j.job_type = 'source_index'
            ORDER BY j.created_at DESC, j.id DESC LIMIT 1
        ), 'not_indexed')
    """)


def downgrade():
    op.drop_column("documents", "parse_details")
